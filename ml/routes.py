"""ML API — dataset upload, training runs, live predictions."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.db import get_db
from app.ml import ingest, predictor, training
from app.ml.schemas import (
    DatasetOut, DatasetUploadMeta, PredictionBatch, PredictRequest,
    TrainingRunOut, TrainingRunRequest,
)

router = APIRouter()


# ── Datasets ─────────────────────────────────────────────────────────────────
@router.post("/datasets/upload")
async def upload_dataset(
    name: str = Form(...),
    kind: str = Form(...),                 # CANDLES_1M | OPTION_CHAIN | NEWS | ...
    underlying: str | None = Form(None),
    period_start: str = Form(...),         # YYYY-MM-DD
    period_end: str = Form(...),
    notes: str | None = Form(None),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from datetime import date
    meta = DatasetUploadMeta(
        name=name, kind=kind, underlying=underlying,
        period_start=date.fromisoformat(period_start),
        period_end=date.fromisoformat(period_end),
        notes=notes,
    )
    content = await file.read()
    try:
        ds_id = await ingest.ingest_dataset(db, meta, content, file.filename or name, user.id)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return {"id": ds_id, "size_bytes": len(content)}


@router.get("/datasets", response_model=list[DatasetOut])
async def list_datasets(db: AsyncSession = Depends(get_db),
                         _: User = Depends(get_current_user)) -> list[dict]:
    return await ingest.list_datasets(db)


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(dataset_id: int, db: AsyncSession = Depends(get_db),
                          _: User = Depends(get_current_user)) -> dict:
    await db.execute(text("DELETE FROM ml_datasets WHERE id=:id"), {"id": dataset_id})
    await db.commit()
    return {"deleted": dataset_id}


# ── Training ─────────────────────────────────────────────────────────────────
@router.post("/training/runs")
async def create_run(body: TrainingRunRequest,
                      user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)) -> dict:
    run_id = await training.queue_training_run(db, body, user.id)
    return {"run_id": run_id, "status": "QUEUED"}


@router.get("/training/runs", response_model=list[TrainingRunOut])
async def list_runs(db: AsyncSession = Depends(get_db),
                     _: User = Depends(get_current_user)) -> list[dict]:
    rows = await db.execute(
        text("""SELECT id, name, model_type, underlying, status, metrics,
                        started_at, completed_at
                 FROM ml_training_runs ORDER BY id DESC LIMIT 100"""),
    )
    return [dict(r._mapping) for r in rows]


@router.get("/training/runs/{run_id}")
async def get_run(run_id: int, db: AsyncSession = Depends(get_db),
                   _: User = Depends(get_current_user)) -> dict:
    row = await db.execute(
        text("""SELECT id, name, model_type, underlying, dataset_ids,
                        feature_config, hyperparams, status, metrics,
                        model_path, started_at, completed_at, error_message
                 FROM ml_training_runs WHERE id=:id"""),
        {"id": run_id},
    )
    r = row.first()
    if r is None:
        raise HTTPException(404, "training run not found")
    return dict(r._mapping)


# ── Predictions ──────────────────────────────────────────────────────────────
@router.post("/predict", response_model=PredictionBatch)
async def live_predict(body: PredictRequest,
                        db: AsyncSession = Depends(get_db),
                        _: User = Depends(get_current_user)) -> PredictionBatch:
    return await predictor.predict(db, body)


@router.get("/predictions/recent")
async def recent_predictions(limit: int = 50,
                              db: AsyncSession = Depends(get_db),
                              _: User = Depends(get_current_user)) -> list[dict]:
    rows = await db.execute(
        text("""SELECT id, predicted_at, underlying, expiry_date, strike, option_type,
                        predicted_probability_otm, confidence_score,
                        recommended_action, reasoning
                 FROM ml_predictions ORDER BY predicted_at DESC LIMIT :l"""),
        {"l": limit},
    )
    return [dict(r._mapping) for r in rows]
