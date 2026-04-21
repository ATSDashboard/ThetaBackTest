"""Pydantic schemas for ML API."""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


DatasetKind = Literal["CANDLES_1M", "OPTION_CHAIN", "NEWS", "MACRO", "FII_DII", "VIX"]
ModelType = Literal["EXPIRY_DEEP_OTM", "EXPIRY_MID_OTM", "INTRADAY", "WEEKLY_CUSTOM"]


class DatasetUploadMeta(BaseModel):
    name: str
    kind: DatasetKind
    underlying: str | None = None
    period_start: date
    period_end: date
    notes: str | None = None


class DatasetOut(BaseModel):
    id: int
    name: str
    kind: str
    underlying: str | None
    period_start: date
    period_end: date
    row_count: int | None
    file_format: str | None
    uploaded_at: datetime
    notes: str | None


class TrainingRunRequest(BaseModel):
    name: str
    model_type: ModelType
    underlying: Literal["NIFTY", "SENSEX"]
    dataset_ids: list[int]
    horizon_minutes: int = Field(60, ge=5, le=375)     # predict T+N up to full session
    hyperparams: dict = Field(default_factory=dict)
    feature_config: dict = Field(default_factory=lambda: {
        "include_oi_walls": True,
        "include_vix_regime": True,
        "include_news_sentiment": True,
        "include_fii_dii": True,
        "include_greeks": True,
        "include_max_pain": True,
    })


class TrainingRunOut(BaseModel):
    id: int
    name: str
    model_type: str
    underlying: str
    status: str
    metrics: dict | None
    started_at: datetime | None
    completed_at: datetime | None


class PredictRequest(BaseModel):
    model_run_id: int | None = None          # None = use latest completed model for this type
    underlying: Literal["NIFTY", "SENSEX"] = "NIFTY"
    model_type: ModelType = "EXPIRY_DEEP_OTM"
    expiry_date: date | None = None          # default: nearest weekly expiry
    horizon_minutes: int = 60


class Prediction(BaseModel):
    underlying: str
    expiry_date: date
    strike: Decimal
    option_type: Literal["CE", "PE"]
    recommended_action: Literal["SELL", "HOLD", "SKIP"]
    predicted_probability_otm: float
    predicted_price_at_horizon: Decimal | None
    confidence_score: float
    recommended_entry_window_start: time | None
    recommended_entry_window_end: time | None
    recommended_exit_pct: int | None
    reasoning: str


class PredictionBatch(BaseModel):
    generated_at: datetime
    model_run_id: int
    model_name: str
    underlying: str
    snapshot: dict
    predictions: list[Prediction]
