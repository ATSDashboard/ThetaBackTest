"""Dataset ingestion — handles CSV/Parquet/JSON uploads.

Each file is validated, checksummed, stored (local or S3), and indexed in `ml_datasets`.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.ml.schemas import DatasetUploadMeta

log = structlog.get_logger(__name__)
_s = get_settings()

# Local storage; swap for S3 in prod via config flag
_STORAGE_ROOT = Path("/tmp/navin-ml-datasets")
_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)


REQUIRED_COLUMNS: dict[str, set[str]] = {
    "CANDLES_1M":   {"timestamp", "symbol", "open", "high", "low", "close", "volume"},
    "OPTION_CHAIN": {"timestamp", "underlying", "expiry", "strike", "option_type",
                      "ltp", "bid", "ask", "oi", "volume", "iv"},
    "NEWS":         {"timestamp", "headline"},
    "MACRO":        {"date", "event"},
    "FII_DII":      {"date", "fii_net", "dii_net"},
    "VIX":          {"timestamp", "vix"},
}


def _store_file(content: bytes, kind: str, name: str) -> tuple[Path, str]:
    """Save bytes to storage; return (path, sha256)."""
    digest = hashlib.sha256(content).hexdigest()
    dest = _STORAGE_ROOT / kind / f"{digest[:12]}-{name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    return dest, digest


def _detect_format(name: str) -> str:
    if name.endswith(".parquet"): return "parquet"
    if name.endswith(".json"):    return "json"
    return "csv"


def _validate_headers(kind: str, content: bytes, fmt: str) -> tuple[bool, str, int]:
    required = REQUIRED_COLUMNS.get(kind, set())
    if not required:
        return True, "", 0

    if fmt == "csv":
        reader = csv.reader(io.StringIO(content.decode("utf-8", errors="replace")))
        try:
            header = {h.strip().lower() for h in next(reader)}
        except StopIteration:
            return False, "empty file", 0
        missing = required - header
        if missing:
            return False, f"missing columns: {sorted(missing)}", 0
        row_count = sum(1 for _ in reader)
        return True, "", row_count

    if fmt == "json":
        try:
            data = json.loads(content)
            if isinstance(data, list):
                return True, "", len(data)
            return True, "", 1
        except Exception as e:
            return False, f"invalid JSON: {e}", 0

    # parquet — would need pyarrow; for now accept without row count
    return True, "", -1


async def ingest_dataset(
    db: AsyncSession, meta: DatasetUploadMeta, content: bytes,
    original_name: str, uploaded_by: int,
) -> int:
    """Validate + store + record. Returns dataset id."""
    fmt = _detect_format(original_name)
    ok, err, rows = _validate_headers(meta.kind, content, fmt)
    if not ok:
        raise ValueError(f"validation failed: {err}")

    path, digest = _store_file(content, meta.kind, original_name)

    row = await db.execute(
        text("""INSERT INTO ml_datasets
                  (name, kind, underlying, period_start, period_end,
                   row_count, file_path, file_format, checksum_sha256,
                   uploaded_by, notes)
                VALUES (:n, :k, :u, :ps, :pe, :rc, :fp, :ff, :ck, :ub, :nt)
                RETURNING id"""),
        {
            "n": meta.name, "k": meta.kind, "u": meta.underlying,
            "ps": meta.period_start, "pe": meta.period_end,
            "rc": rows, "fp": str(path), "ff": fmt,
            "ck": digest, "ub": uploaded_by, "nt": meta.notes,
        },
    )
    ds_id = row.scalar_one()
    await db.commit()

    log.info("ml.dataset.ingested", id=ds_id, kind=meta.kind,
             rows=rows, size_bytes=len(content), checksum=digest[:12])
    return ds_id


async def list_datasets(db: AsyncSession) -> list[dict[str, Any]]:
    rows = await db.execute(
        text("""SELECT id, name, kind, underlying, period_start, period_end,
                        row_count, file_format, uploaded_at, notes
                 FROM ml_datasets ORDER BY uploaded_at DESC"""),
    )
    return [dict(r._mapping) for r in rows]
