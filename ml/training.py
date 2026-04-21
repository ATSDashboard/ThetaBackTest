"""Training pipeline scaffolding.

Actual model training awaits real uploaded data. This module:
- Registers training runs in DB
- Spawns a worker task that loads datasets, extracts features, trains, evaluates, persists
- Exposes status endpoints

Design is extensible: add a new `_run_{model_type}()` function and register in RUN_REGISTRY.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Awaitable, Callable

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.ml.schemas import TrainingRunRequest

log = structlog.get_logger(__name__)


async def queue_training_run(db: AsyncSession, req: TrainingRunRequest, user_id: int) -> int:
    row = await db.execute(
        text("""INSERT INTO ml_training_runs
                  (name, model_type, underlying, dataset_ids,
                   feature_config, hyperparams, status, created_by)
                VALUES (:n, :mt, :u, :ds, :fc, :hp, 'QUEUED', :uid)
                RETURNING id"""),
        {
            "n": req.name, "mt": req.model_type, "u": req.underlying,
            "ds": req.dataset_ids, "fc": json.dumps(req.feature_config),
            "hp": json.dumps(req.hyperparams), "uid": user_id,
        },
    )
    run_id = row.scalar_one()
    await db.commit()
    asyncio.create_task(_dispatch(run_id))
    return run_id


async def _dispatch(run_id: int) -> None:
    """Worker entry point. In prod this goes through Celery/RQ."""
    async with session_scope() as db:
        row = await db.execute(
            text("SELECT model_type FROM ml_training_runs WHERE id=:id"), {"id": run_id},
        )
        mt = row.scalar()

        fn = RUN_REGISTRY.get(mt)
        if fn is None:
            await _fail(db, run_id, f"no trainer registered for {mt}")
            return

        await db.execute(
            text("UPDATE ml_training_runs SET status='RUNNING', started_at=NOW() WHERE id=:id"),
            {"id": run_id},
        )
        await db.commit()

        try:
            metrics, model_path = await fn(run_id)
            await db.execute(
                text("""UPDATE ml_training_runs SET
                            status='COMPLETED', completed_at=NOW(),
                            metrics=:m, model_path=:mp
                        WHERE id=:id"""),
                {"id": run_id, "m": json.dumps(metrics), "mp": str(model_path)},
            )
            await db.commit()
            log.info("ml.training.completed", run_id=run_id, metrics=metrics)
        except Exception as e:
            log.exception("ml.training.failed", run_id=run_id, err=str(e))
            await _fail(db, run_id, str(e))


async def _fail(db: AsyncSession, run_id: int, msg: str) -> None:
    await db.execute(
        text("""UPDATE ml_training_runs SET
                    status='FAILED', completed_at=NOW(), error_message=:e
                WHERE id=:id"""),
        {"id": run_id, "e": msg[:500]},
    )
    await db.commit()


# ── Model-type trainers ──────────────────────────────────────────────────────
# Each trainer: (run_id) → (metrics_dict, model_path). Awaits real data;
# stub implementations below produce structured placeholder so the pipeline
# is testable end-to-end without datasets.

async def _train_expiry_deep_otm(run_id: int) -> tuple[dict, Path]:
    """
    Expiry-day Deep OTM model.

    Target: P(option expires OTM | current state) at intraday timestamp T.
    Features:
      - Distance from spot (pts and ATR-normalized)
      - OI concentration + top-3 wall distances
      - VIX level + regime bucket (calm/normal/panic)
      - Minute-of-day
      - PCR (OI + Volume)
      - Spot vs Max Pain offset
      - Recent 5m / 15m momentum
      - News sentiment (if NEWS dataset present)
      - FII/DII net flow (daily, rolling 5d)

    Algorithm: gradient-boosted tree ensemble with isotonic probability calibration.
    Walk-forward validation on last 4 expiries of training window.

    NOTE: placeholder until datasets exist. When real data uploaded, replace the
    body with: load features, fit, evaluate, persist.
    """
    # TODO: load datasets from ml_training_runs.dataset_ids → ml_datasets.file_path
    # TODO: build features/expiry_deep_otm.py feature extractor
    # TODO: sklearn / lightgbm training
    # TODO: isotonic calibration
    # TODO: walk-forward eval
    # TODO: pickle model to _MODEL_STORE
    await asyncio.sleep(2)  # simulate training wall-time
    metrics = {
        "status": "scaffold",
        "note": "awaiting real data upload to train",
        "samples_train": 0, "samples_test": 0,
        "brier": None, "logloss": None,
        "hit_rate_tier1": None, "hit_rate_tier2": None,
    }
    return metrics, Path("/tmp/navin-ml-models/expiry_deep_otm_pending.pkl")


async def _train_expiry_mid_otm(run_id: int) -> tuple[dict, Path]:
    """Same structure as deep OTM but targets Mid OTM (cushion ratio 1.0-2.0)."""
    await asyncio.sleep(2)
    return (
        {"status": "scaffold", "note": "awaiting real data upload"},
        Path("/tmp/navin-ml-models/expiry_mid_otm_pending.pkl"),
    )


async def _train_intraday(run_id: int) -> tuple[dict, Path]:
    await asyncio.sleep(1)
    return ({"status": "scaffold"}, Path("/tmp/navin-ml-models/intraday_pending.pkl"))


async def _train_weekly_custom(run_id: int) -> tuple[dict, Path]:
    await asyncio.sleep(1)
    return ({"status": "scaffold"}, Path("/tmp/navin-ml-models/weekly_custom_pending.pkl"))


RUN_REGISTRY: dict[str, Callable[[int], Awaitable[tuple[dict, Path]]]] = {
    "EXPIRY_DEEP_OTM": _train_expiry_deep_otm,
    "EXPIRY_MID_OTM":  _train_expiry_mid_otm,
    "INTRADAY":        _train_intraday,
    "WEEKLY_CUSTOM":   _train_weekly_custom,
}
