"""Deep OTM strike recommendation engine.

Implements the spec at Deep OTM Analysis/...expiry_strategy_module_spec.md:
- expected_move volatility band
- OI wall detection
- Tier classification (1=Almost Sure Shot .. 4=Aggressive)
- Wall confirmation scoring
- Directional bias (PCR + Max Pain)
- Exit triggers

Pure-python, no broker/DB dependency so it's trivially testable. Service layer
wires in live option chain data from BrokerClient + VIX from NSE.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import IntEnum


class Tier(IntEnum):
    ALMOST_SURE = 1       # cushion_ratio ≥ 3.0, target hit-rate >95%
    VERY_DEEP = 2         # ≥ 2.0, >90%
    BALANCED = 3          # ≥ 1.5, >80%
    AGGRESSIVE = 4        # ≥ 1.0, >70%


TIER_LABELS = {
    Tier.ALMOST_SURE: "Almost Sure Shot",
    Tier.VERY_DEEP:   "Very Deep",
    Tier.BALANCED:    "Balanced",
    Tier.AGGRESSIVE:  "Aggressive",
}

VIX_PANIC = 25.0
VIX_CALM  = 13.0
MIN_OI_FOR_WALL = 1_000_000
FRESH_WRITING_OI_CHG_PCT = 50.0
MIN_RECOMMEND_SCORE = 4


@dataclass(frozen=True)
class StrikeData:
    strike: float
    option_type: str     # 'CE' | 'PE'
    ltp: float
    bid: float
    ask: float
    oi: int
    oi_change_pct: float
    volume: int
    iv: float


@dataclass(frozen=True)
class MarketSnapshot:
    spot: float
    futures: float | None
    max_pain: float | None
    oi_pcr: float        # put-call ratio
    vol_pcr: float
    vix: float
    vix_change_pct: float
    technical_support: list[float]
    technical_resistance: list[float]
    dte: int             # days-to-expiry
    is_monthly: bool = False


@dataclass
class Recommendation:
    tier: Tier
    tier_label: str
    ce_strike: float | None
    ce_premium: float | None
    ce_oi: int | None
    ce_cushion_ratio: float | None
    pe_strike: float | None
    pe_premium: float | None
    pe_oi: int | None
    pe_cushion_ratio: float | None
    combined_premium_per_lot: float
    probability_otm_estimate: float
    score_ce: int
    score_pe: int
    notes: list[str]


def expected_move(spot: float, vix: float, dte: int, is_monthly: bool = False) -> float:
    """Volatility-adjusted expected move until expiry.

    expected_move_daily = spot × (VIX/100) / sqrt(252)
    expected_move_expiry = daily × sqrt(DTE) × safety_multiplier
    safety: 1.5 weekly, 2.0 monthly.
    """
    daily = spot * (vix / 100.0) / math.sqrt(252)
    safety = 2.0 if is_monthly else 1.5
    return daily * math.sqrt(max(dte, 1)) * safety


def cushion_ratio(distance_pts: float, expected: float) -> float:
    return distance_pts / expected if expected > 0 else 0.0


def classify_tier(ratio: float) -> Tier | None:
    if ratio >= 3.0: return Tier.ALMOST_SURE
    if ratio >= 2.0: return Tier.VERY_DEEP
    if ratio >= 1.5: return Tier.BALANCED
    if ratio >= 1.0: return Tier.AGGRESSIVE
    return None


def find_oi_walls(chain: list[StrikeData], side: str, top_n: int = 3) -> list[StrikeData]:
    """Top-N strikes on one side (CE or PE) by absolute OI."""
    filtered = [s for s in chain if s.option_type == side and s.oi >= MIN_OI_FOR_WALL]
    return sorted(filtered, key=lambda s: s.oi, reverse=True)[:top_n]


def wall_confirmation_score(
    strike: StrikeData, spot: float, expected: float,
    walls: list[StrikeData], technical_levels: list[float],
) -> int:
    """
    +3 strike IS a top-3 wall
    +2 strike is beyond a wall (must be breached first to reach this strike)
    +2 OI Change % > 50 (fresh aggressive writing)
    +1 strike is beyond the technical level on the correct side
    +1 strike is beyond 2× expected move
    """
    score = 0
    wall_strikes = {w.strike for w in walls}
    if strike.strike in wall_strikes:
        score += 3
    else:
        # "Behind" = if CE, our strike > max wall (further OTM); if PE, < min wall
        if strike.option_type == "CE" and walls and strike.strike > max(w.strike for w in walls):
            score += 2
        if strike.option_type == "PE" and walls and strike.strike < min(w.strike for w in walls):
            score += 2

    if strike.oi_change_pct > FRESH_WRITING_OI_CHG_PCT:
        score += 2

    distance = abs(strike.strike - spot)
    if technical_levels and distance > min(abs(t - spot) for t in technical_levels):
        score += 1
    if distance >= 2 * expected:
        score += 1

    return score


def premium_to_margin_ok(premium_per_lot: float, strike: float, lot_size: int) -> bool:
    """Reject strikes with <1% premium-to-margin ratio (not worth the risk)."""
    margin_approx = strike * lot_size * 0.11   # SPAN+Exposure ~11% of notional
    if margin_approx <= 0: return False
    return (premium_per_lot / margin_approx) >= 0.01


def recommend(
    chain: list[StrikeData], market: MarketSnapshot, lot_size: int,
) -> list[Recommendation]:
    """Generate ordered list of tiered recommendations."""
    expected = expected_move(market.spot, market.vix, market.dte, market.is_monthly)

    ce_walls = find_oi_walls(chain, "CE")
    pe_walls = find_oi_walls(chain, "PE")

    def eligible(strike: StrikeData, walls: list[StrikeData]) -> tuple[Tier, int, float] | None:
        dist = abs(strike.strike - market.spot)
        ratio = cushion_ratio(dist, expected)
        tier = classify_tier(ratio)
        if tier is None: return None
        if not premium_to_margin_ok(strike.ltp * lot_size, strike.strike, lot_size): return None
        levels = (market.technical_resistance if strike.option_type == "CE"
                  else market.technical_support)
        score = wall_confirmation_score(strike, market.spot, expected, walls, levels)
        if score < MIN_RECOMMEND_SCORE: return None
        return tier, score, ratio

    # Pick best CE and best PE per tier
    recs: list[Recommendation] = []
    for tier in Tier:
        best_ce: tuple[StrikeData, int, float] | None = None
        best_pe: tuple[StrikeData, int, float] | None = None

        for s in chain:
            result = eligible(s, ce_walls if s.option_type == "CE" else pe_walls)
            if result is None: continue
            t, score, ratio = result
            if t != tier: continue
            candidate = (s, score, ratio)
            if s.option_type == "CE":
                if best_ce is None or s.ltp > best_ce[0].ltp:   # prefer higher premium at same tier
                    best_ce = candidate
            else:
                if best_pe is None or s.ltp > best_pe[0].ltp:
                    best_pe = candidate

        if best_ce is None and best_pe is None:
            continue

        ce_strike = best_ce[0] if best_ce else None
        pe_strike = best_pe[0] if best_pe else None
        combined_per_lot = ((ce_strike.ltp if ce_strike else 0.0) +
                             (pe_strike.ltp if pe_strike else 0.0)) * lot_size

        # Probability estimate: rough mapping from tier + both-sides bonus
        prob_map = {Tier.ALMOST_SURE: 0.95, Tier.VERY_DEEP: 0.90,
                    Tier.BALANCED: 0.80, Tier.AGGRESSIVE: 0.70}
        prob = prob_map[tier]
        if ce_strike is None or pe_strike is None:
            prob *= 0.95  # single-sided rec is slightly less certain for a strangle

        notes = _risk_notes(market)

        recs.append(Recommendation(
            tier=tier,
            tier_label=TIER_LABELS[tier],
            ce_strike=ce_strike.strike if ce_strike else None,
            ce_premium=ce_strike.ltp if ce_strike else None,
            ce_oi=ce_strike.oi if ce_strike else None,
            ce_cushion_ratio=best_ce[2] if best_ce else None,
            pe_strike=pe_strike.strike if pe_strike else None,
            pe_premium=pe_strike.ltp if pe_strike else None,
            pe_oi=pe_strike.oi if pe_strike else None,
            pe_cushion_ratio=best_pe[2] if best_pe else None,
            combined_premium_per_lot=combined_per_lot,
            probability_otm_estimate=prob,
            score_ce=best_ce[1] if best_ce else 0,
            score_pe=best_pe[1] if best_pe else 0,
            notes=notes,
        ))

    return recs


def _risk_notes(m: MarketSnapshot) -> list[str]:
    notes: list[str] = []
    if m.vix >= VIX_PANIC:
        notes.append(f"VIX panic zone ({m.vix:.1f} ≥ {VIX_PANIC})")
    if m.vix_change_pct > 10:
        notes.append(f"VIX spiked {m.vix_change_pct:+.1f}% — size conservatively")
    if m.vix < VIX_CALM:
        notes.append(f"VIX calm ({m.vix:.1f}) — premium limited")
    if m.max_pain is not None and abs(m.spot - m.max_pain) / m.spot > 0.01:
        side = "above" if m.spot > m.max_pain else "below"
        notes.append(f"Spot {side} Max Pain by {abs(m.spot - m.max_pain):.0f} pts — gravitational pull")
    if m.oi_pcr > 1.3:
        notes.append(f"OI PCR {m.oi_pcr:.2f} — bullish bias, prefer selling PE deeper")
    elif m.oi_pcr < 0.8:
        notes.append(f"OI PCR {m.oi_pcr:.2f} — bearish bias, prefer selling CE deeper")
    return notes


# ── Exit logic ────────────────────────────────────────────────────────────────
def exit_decision(
    entry_premium: float, current_premium: float, dte: int,
    spot: float, sold_strike: float, vix_change_pct: float,
    is_expiry_day: bool, now_hour: int,
    fresh_oi_inside_strike: bool,
) -> tuple[str, str]:
    """Return (action, reason). action ∈ {HOLD, WARN, BOOK, DEFENSIVE_EXIT, CLOSE_EXPIRY}."""
    captured = 0.0
    if entry_premium > 0:
        captured = (entry_premium - current_premium) / entry_premium

    if is_expiry_day and captured >= 0.85 and now_hour >= 14:
        return ("CLOSE_EXPIRY", f"{captured*100:.0f}% captured on expiry day after 2pm — avoid gamma")

    if captured >= 0.70 and dte >= 2:
        return ("BOOK", f"{captured*100:.0f}% premium captured, {dte} DTE remaining")

    distance = abs(sold_strike - spot)
    if distance <= 0.20 * abs(sold_strike):  # spot within 20% of sold strike (80% breached)
        return ("DEFENSIVE_EXIT", "Spot breached 80% of distance to sold strike")

    if vix_change_pct > 25:
        return ("DEFENSIVE_EXIT", f"VIX spiked {vix_change_pct:+.1f}% intraday")

    if fresh_oi_inside_strike:
        return ("WARN", "Fresh OI buildup inside sold strike — monitor closely")

    return ("HOLD", f"{captured*100:.0f}% captured, {dte} DTE, within cushion")
