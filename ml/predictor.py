"""Live prediction engine.

Given a trained model and current market snapshot, produce ranked strike
recommendations with calibrated probabilities + entry/exit windows.

Until a real model is trained, this returns a deterministic scaffold output
derived from the same Deep OTM tier logic (`app.analytics.deep_otm`) so the
UI + API contract are fully exercised end-to-end.
"""
from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.deep_otm import (
    MarketSnapshot, StrikeData, expected_move, recommend,
)
from app.config import get_settings
from app.ml.schemas import PredictRequest, Prediction, PredictionBatch

log = structlog.get_logger(__name__)
_s = get_settings()


async def predict(db: AsyncSession, req: PredictRequest) -> PredictionBatch:
    # Get latest completed model for this (model_type, underlying) if not specified
    if req.model_run_id is None:
        row = await db.execute(
            text("""SELECT id, name FROM ml_training_runs
                    WHERE model_type=:mt AND underlying=:u AND status='COMPLETED'
                    ORDER BY completed_at DESC LIMIT 1"""),
            {"mt": req.model_type, "u": req.underlying},
        )
        r = row.first()
        if r is None:
            # No trained model yet — fall through to deterministic scaffold
            model_id = 0
            model_name = f"{req.model_type}-scaffold-v0"
        else:
            model_id = r[0]
            model_name = r[1]
    else:
        model_id = req.model_run_id
        row = await db.execute(
            text("SELECT name FROM ml_training_runs WHERE id=:id"), {"id": model_id},
        )
        model_name = row.scalar() or "unknown"

    preds = _scaffold_predict(req)
    snapshot = _snapshot(req.underlying)

    # Persist each prediction for calibration tracking
    for p in preds:
        await db.execute(
            text("""INSERT INTO ml_predictions
                      (model_run_id, underlying, expiry_date, strike, option_type,
                       horizon_minutes, predicted_price, predicted_probability_otm,
                       confidence_score, recommended_action,
                       recommended_entry_window_start, recommended_entry_window_end,
                       recommended_exit_pct, market_snapshot, reasoning)
                    VALUES (:r,:u,:e,:s,:ot,:h,:pp,:po,:c,:ra,:ews,:ewe,:rxp,:ms,:rsn)"""),
            {
                "r": model_id or None, "u": req.underlying,
                "e": p.expiry_date, "s": float(p.strike), "ot": p.option_type,
                "h": req.horizon_minutes,
                "pp": float(p.predicted_price_at_horizon) if p.predicted_price_at_horizon else None,
                "po": p.predicted_probability_otm,
                "c": p.confidence_score, "ra": p.recommended_action,
                "ews": p.recommended_entry_window_start,
                "ewe": p.recommended_entry_window_end,
                "rxp": p.recommended_exit_pct,
                "ms": __import__("json").dumps(snapshot, default=str),
                "rsn": p.reasoning,
            },
        )
    if model_id:
        await db.commit()

    return PredictionBatch(
        generated_at=datetime.now(UTC),
        model_run_id=model_id, model_name=model_name,
        underlying=req.underlying, snapshot=snapshot, predictions=preds,
    )


def _snapshot(underlying: str) -> dict:
    # Placeholder — in live mode this reads from /data/quote cache
    spot = 24812.40 if underlying == "NIFTY" else 81204.15
    return {
        "spot": spot, "vix": 13.24, "vix_change_pct": -1.8,
        "oi_pcr": 1.11, "max_pain": 24800 if underlying == "NIFTY" else 81000,
        "expected_move_expiry": round(expected_move(spot, 13.24, 1), 1),
        "fii_net_cr": -842, "dii_net_cr": 1240,
    }


def _scaffold_predict(req: PredictRequest) -> list[Prediction]:
    """Deterministic recommendations derived from the Deep OTM tier engine.

    Replace with actual model inference once a completed training run exists.
    The SHAPE of output is production-final — only the internals change.
    """
    underlying = req.underlying
    spot = 24812.40 if underlying == "NIFTY" else 81204.15
    step = 50 if underlying == "NIFTY" else 100
    lot = _s.nifty_lot_size if underlying == "NIFTY" else _s.sensex_lot_size
    exp = req.expiry_date or _next_thursday()
    dte = max(1, (exp - date.today()).days)

    # Synthesize a realistic chain (same generator as /analytics/deep-otm)
    chain: list[StrikeData] = []
    for off in range(-30, 31):
        k = spot + off * step
        for opt in ("CE", "PE"):
            distance = abs(k - spot)
            decay = max(0.05, 180 * math.exp(-((distance / 400) ** 2)))
            chain.append(StrikeData(
                strike=k, option_type=opt, ltp=decay,
                bid=decay - 0.5, ask=decay + 0.5,
                oi=int(max(50_000, 3_000_000 - distance * 400
                            + (1_500_000 if off in (-10, -6, 4, 8) else 0))),
                oi_change_pct=(70 if off in (8, -10) else 25),
                volume=int(10_000 * math.exp(-((distance / 300) ** 2))),
                iv=16.5,
            ))

    snap = MarketSnapshot(
        spot=spot, futures=spot + 25, max_pain=spot,
        oi_pcr=1.11, vol_pcr=0.98, vix=13.24, vix_change_pct=-1.8,
        technical_support=[spot - 100, spot - 400, spot - 700],
        technical_resistance=[spot + 200, spot + 600, spot + 1000],
        dte=dte,
        is_monthly=req.model_type != "EXPIRY_DEEP_OTM",
    )
    recs = recommend(chain, snap, lot)

    preds: list[Prediction] = []
    for r in recs:
        # Model-specific tier filter
        if req.model_type == "EXPIRY_DEEP_OTM" and int(r.tier) > 2:
            continue
        if req.model_type == "EXPIRY_MID_OTM" and int(r.tier) < 3:
            continue

        for side in ("ce", "pe"):
            strike = getattr(r, f"{side}_strike")
            if strike is None: continue
            prem = getattr(r, f"{side}_premium") or 0
            cushion = getattr(r, f"{side}_cushion_ratio") or 0
            prob = r.probability_otm_estimate
            confidence = min(0.99, 0.4 + 0.15 * int(r.tier) + 0.15 * (cushion - 1))
            reasoning_bits = [
                f"Tier {r.tier} ({r.tier_label})",
                f"cushion {cushion:.2f}x expected move",
                f"OI {(getattr(r, f'{side}_oi') or 0)/1e5:.1f}L",
            ] + r.notes[:2]

            preds.append(Prediction(
                underlying=underlying, expiry_date=exp,
                strike=Decimal(str(strike)), option_type=side.upper(),
                recommended_action="SELL",
                predicted_probability_otm=round(prob, 4),
                predicted_price_at_horizon=Decimal(str(round(prem * 0.65, 2))),  # heuristic
                confidence_score=round(confidence, 3),
                recommended_entry_window_start=time(9, 30),
                recommended_entry_window_end=time(10, 15),
                recommended_exit_pct=70,
                reasoning=" · ".join(reasoning_bits),
            ))
    return preds


def _next_thursday() -> date:
    today = date.today()
    days_ahead = (3 - today.weekday()) % 7 or 7
    return today + timedelta(days=days_ahead)
