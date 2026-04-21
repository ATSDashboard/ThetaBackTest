"""Build Parquet store from historical GFDL archive + TW incremental dumps.

Usage:
    python -m ingest.build_store [--dry-run] [--max-files N]

Output:
    data/parquet/instrument=<X>/year=<Y>/month=<M>/<run>.parquet
    data/manifest.parquet   — tracks (source_path, sha256, rows, ingested_at)

Idempotent: files already in manifest (by sha256) are skipped.
"""
from __future__ import annotations

import argparse
import io
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ingest.common import (
    CANONICAL_COLS, INDICATOR_COLS, empty_row,
    parse_gfdl_ticker, parse_tw_symbol, sha256_of,
)

ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = ROOT / "data" / "parquet"
MANIFEST = ROOT / "data" / "manifest.parquet"
REJECTS = ROOT / "results" / "ingestion_rejects.csv"

HIST = Path("/Users/rohanshah/Desktop/AI Instructions/Trading Developments/Options Data/NIFTY")
TW = Path("/Users/rohanshah/Desktop/AI Instructions/Trading Developments/Options Data/New Options Data from 13:4:26 Manually Pulled from TW")


# ═══════════════════════════════════════════════════════════════════════
#  GFDL historical parser (NIFTY ZIPs — 20-22 daily CSVs per monthly ZIP)
# ═══════════════════════════════════════════════════════════════════════
def parse_gfdl_csv_bytes(content: bytes, source_file: str) -> tuple[list[dict], list[str]]:
    """Parse one daily GFDL CSV → canonical rows + reject reasons."""
    rejects: list[str] = []
    try:
        df = pd.read_csv(io.BytesIO(content))
    except Exception as e:
        return [], [f"{source_file}: read error — {e}"]

    required = {"Ticker","Date","Time","Open","High","Low","Close","Volume"}
    if not required.issubset(df.columns):
        return [], [f"{source_file}: missing cols {required - set(df.columns)}"]

    rows: list[dict] = []
    for rec in df.itertuples(index=False):
        try:
            meta = parse_gfdl_ticker(rec.Ticker)
            ts = pd.to_datetime(f"{rec.Date} {rec.Time}", format="%d/%m/%Y %H:%M:%S").tz_localize("Asia/Kolkata")
            r = empty_row()
            r.update(meta)
            r["timestamp"] = ts
            r["source"] = "GFDL_HISTORICAL"
            r["open"] = float(rec.Open); r["high"] = float(rec.High)
            r["low"]  = float(rec.Low);  r["close"] = float(rec.Close)
            r["volume"] = int(rec.Volume)
            r["oi"] = int(getattr(rec, "_8", 0)) if hasattr(rec, "_8") else None   # Open Interest
            if "Open Interest" in df.columns:
                v = df.iloc[0]["Open Interest"]
                # fallback only; use column access below
                pass
            r["bar_minutes"] = 1
            if r["expiry"]:
                r["dte"] = (r["expiry"] - ts.date()).days
            rows.append(r)
        except Exception as e:
            rejects.append(f"{source_file}: ticker={rec.Ticker} — {e}")
    # Fix OI column access (tuple attr name for "Open Interest" is messy)
    if "Open Interest" in df.columns:
        oi_series = df["Open Interest"]
        for i, r in enumerate(rows):
            try: r["oi"] = int(oi_series.iloc[i])
            except Exception: r["oi"] = None
    return rows, rejects


def walk_gfdl_zips(root: Path):
    """Yield (source_label, daily_csv_bytes, zip_entry_name)."""
    for z in sorted(root.rglob("*.zip")):
        rel = z.relative_to(root)
        try:
            with zipfile.ZipFile(z) as zf:
                for name in zf.namelist():
                    if not name.lower().endswith(".csv"): continue
                    with zf.open(name) as fh:
                        yield f"{rel}/{name}", fh.read(), z, name
        except Exception as e:
            print(f"[WARN] zip read failed: {z} — {e}")


# ═══════════════════════════════════════════════════════════════════════
#  TW narrow parser (narrow CSVs, with or without indicators)
# ═══════════════════════════════════════════════════════════════════════
_IND_MAP = {
    "ema55": "ema55", "ema200": "ema200",
    "macd": "macd", "signal line": "macd_signal", "histogram": "macd_hist",
    "rsi": "rsi", "rsi-based ma": "rsi_ma",
    "%k": "stoch_k", "%d": "stoch_d",
    "early cross": "early_cross", "golden cross": "golden_cross",
    "regular bullish": "bullish_divergence", "regular bearish": "bearish_divergence",
    "regular bullish label": None,   # ignore the label variants
    "regular bearish label": None,
}


def parse_tw_narrow(path: Path) -> tuple[list[dict], list[str]]:
    try:
        df = pd.read_csv(path)
    except Exception as e:
        return [], [f"{path.name}: read error — {e}"]

    meta = parse_tw_symbol(path.name)
    if meta is None:
        return [], [f"{path.name}: cannot parse filename"]

    rows: list[dict] = []
    for rec in df.to_dict(orient="records"):
        try:
            ts = pd.to_datetime(rec["time"])
            r = empty_row()
            r.update({k: v for k, v in meta.items() if k != "bar_minutes"})
            r["timestamp"] = ts
            r["source"] = "TW_NARROW_IND" if "ema55" in df.columns else "TW_NARROW"
            r["open"]  = float(rec.get("open",  0)) or None
            r["high"]  = float(rec.get("high",  0)) or None
            r["low"]   = float(rec.get("low",   0)) or None
            r["close"] = float(rec.get("close", 0)) or None
            r["volume"] = int(rec.get("Volume", 0) or 0)
            r["bar_minutes"] = meta["bar_minutes"]
            if r["expiry"]:
                r["dte"] = (r["expiry"] - ts.date()).days

            # Indicators — TW column name (lowercased) → canonical
            for tw_col, canonical in _IND_MAP.items():
                if canonical is None: continue
                for c in df.columns:
                    if c.strip().lower() == tw_col:
                        v = rec.get(c)
                        if pd.notna(v) and v != "":
                            try: r[canonical] = float(v) if isinstance(v, (int, float)) else str(v)
                            except Exception: r[canonical] = str(v)
                        break
            rows.append(r)
        except Exception as e:
            rows.append(empty_row())  # placeholder; silently dropped below
    return rows, []


# ═══════════════════════════════════════════════════════════════════════
#  TW wide parser (Sensex-05-03-26.csv — multi-instrument side-by-side)
# ═══════════════════════════════════════════════════════════════════════
def parse_tw_wide(path: Path) -> tuple[list[dict], list[str]]:
    """Handles the multi-column wide-format TW dump.

    Strategy: read the FIRST TWO rows (instrument headers + column names),
    then chunk every 6-7 cols into one instrument block, emit rows for each.
    """
    try:
        raw = pd.read_csv(path, header=None, keep_default_na=False)
    except Exception as e:
        return [], [f"{path.name}: read error — {e}"]

    if len(raw) < 3:
        return [], [f"{path.name}: too few rows"]

    instrument_header = raw.iloc[0].tolist()
    col_header = raw.iloc[1].tolist()

    # Group column ranges: each new non-empty instrument_header starts a block
    blocks: list[tuple[str, int, int]] = []
    curr_name = None
    curr_start = 0
    for i, name in enumerate(instrument_header):
        name = str(name).strip()
        if name and name not in ("", "nan"):
            if curr_name is not None:
                blocks.append((curr_name, curr_start, i))
            curr_name, curr_start = name, i
    if curr_name is not None:
        blocks.append((curr_name, curr_start, len(instrument_header)))

    data_rows = raw.iloc[2:].reset_index(drop=True)
    out: list[dict] = []
    for inst_name, lo, hi in blocks:
        sub_cols = [str(col_header[i]).strip().lower() for i in range(lo, hi)]
        col_idx = {c: lo + k for k, c in enumerate(sub_cols)}
        # Figure out instrument / option metadata from header label
        inst_name_u = inst_name.upper()
        if "SENSEX" in inst_name_u or "NIFTY" in inst_name_u:
            instr = "SENSEX" if "SENSEX" in inst_name_u else "NIFTY"
            meta = {"instrument": instr, "expiry": None, "strike": None, "option_type": "SPOT"}
        else:
            # e.g. "76500 PE" / "81700 CE"
            import re as _re
            m = _re.match(r"^(\d+)\s*(CE|PE)$", inst_name_u.replace(" ", ""))
            if not m:
                continue  # unknown header block; skip
            meta = {"instrument": "SENSEX", "expiry": None, "strike": int(m.group(1)),
                    "option_type": m.group(2)}

        for _, rec in data_rows.iterrows():
            try:
                t = rec[col_idx["time"]]
                if not t or str(t).strip() == "": continue
                ts = pd.to_datetime(t)
                r = empty_row(); r.update(meta)
                r["timestamp"] = ts
                r["source"] = "TW_WIDE"
                r["open"]  = float(rec[col_idx["open"]])
                r["high"]  = float(rec[col_idx["high"]])
                r["low"]   = float(rec[col_idx["low"]])
                r["close"] = float(rec[col_idx["close"]])
                r["volume"] = int(float(rec[col_idx["volume"]] or 0))
                r["bar_minutes"] = 5
                out.append(r)
            except Exception:
                continue

    return out, []


# ═══════════════════════════════════════════════════════════════════════
#  Writer
# ═══════════════════════════════════════════════════════════════════════
def write_parquet(rows: list[dict], manifest_entry: dict) -> None:
    if not rows: return
    df = pd.DataFrame(rows)
    df = df[[c for c in CANONICAL_COLS if c in df.columns]]   # enforce column order

    # Partition by instrument / year / month
    df["_year"]  = df["timestamp"].dt.year.astype(int)
    df["_month"] = df["timestamp"].dt.month.astype(int)

    for (instr, year, month), group in df.groupby(["instrument", "_year", "_month"]):
        out_dir = DATA_ROOT / f"instrument={instr}" / f"year={year}" / f"month={month:02d}"
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = out_dir / f"{manifest_entry['sha256'][:12]}.parquet"
        # Drop partition cols for the actual write
        group = group.drop(columns=["_year", "_month"])
        table = pa.Table.from_pandas(group, preserve_index=False)
        pq.write_table(table, fname, compression="snappy")


def load_manifest() -> set[str]:
    if MANIFEST.exists():
        return set(pd.read_parquet(MANIFEST)["sha256"].tolist())
    return set()


def append_manifest(entry: dict) -> None:
    df_new = pd.DataFrame([entry])
    if MANIFEST.exists():
        df = pd.concat([pd.read_parquet(MANIFEST), df_new], ignore_index=True)
    else:
        df = df_new
    df.to_parquet(MANIFEST)


# ═══════════════════════════════════════════════════════════════════════
#  Driver
# ═══════════════════════════════════════════════════════════════════════
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-zips", type=int, default=0, help="0 = all")
    ap.add_argument("--skip-historical", action="store_true")
    ap.add_argument("--skip-tw", action="store_true")
    args = ap.parse_args()

    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    REJECTS.parent.mkdir(parents=True, exist_ok=True)

    already = load_manifest()
    print(f"[init] {len(already)} files already ingested (will skip)\n")

    total_rows = 0
    total_rejects: list[str] = []
    zip_count = 0

    if not args.skip_historical:
        print("═══ Historical GFDL archive ═══")
        # Process each ZIP as a unit — manifest tracks the ZIP's sha256
        for zip_path in sorted(HIST.rglob("*.zip")):
            if args.max_zips and zip_count >= args.max_zips: break
            zip_count += 1
            z_sha = sha256_of(zip_path)
            if z_sha in already:
                print(f"  ⏭  skip (ingested):  {zip_path.relative_to(HIST)}")
                continue

            zip_rows: list[dict] = []
            try:
                with zipfile.ZipFile(zip_path) as zf:
                    for name in zf.namelist():
                        if not name.lower().endswith(".csv"): continue
                        with zf.open(name) as fh:
                            content = fh.read()
                        rows, rejects = parse_gfdl_csv_bytes(content, f"{zip_path.name}/{name}")
                        zip_rows.extend(rows)
                        total_rejects.extend(rejects)
            except Exception as e:
                print(f"  ❌ zip failed: {zip_path} — {e}")
                continue

            if args.dry_run:
                print(f"  📦 {zip_path.relative_to(HIST)}  rows={len(zip_rows):>9,}  (dry-run)")
                continue

            entry = {"sha256": z_sha, "source_path": str(zip_path),
                     "rows": len(zip_rows), "ingested_at": datetime.utcnow().isoformat()}
            write_parquet(zip_rows, entry)
            append_manifest(entry)
            total_rows += len(zip_rows)
            print(f"  ✅ {zip_path.relative_to(HIST)}  rows={len(zip_rows):>9,}")

    if not args.skip_tw:
        print("\n═══ TW incremental dumps ═══")
        tw_rows_total = 0
        for f in sorted(TW.rglob("*.csv")):
            sha = sha256_of(f)
            if sha in already:
                print(f"  ⏭  skip: {f.name}")
                continue
            if "05-03-26" in f.name.lower() or "sensex-05-03" in f.name.lower():
                rows, rejects = parse_tw_wide(f)
                src_label = "WIDE"
            else:
                rows, rejects = parse_tw_narrow(f)
                src_label = "NARROW"
            total_rejects.extend(rejects)
            if args.dry_run:
                print(f"  📄 {src_label}: {f.name}  rows={len(rows)}  (dry-run)")
                continue
            entry = {"sha256": sha, "source_path": str(f), "rows": len(rows),
                     "ingested_at": datetime.utcnow().isoformat()}
            write_parquet(rows, entry)
            append_manifest(entry)
            tw_rows_total += len(rows)
            print(f"  ✅ {src_label}: {f.name}  rows={len(rows):,}")
        total_rows += tw_rows_total

    # Reject log
    if total_rejects:
        REJECTS.write_text("source\n" + "\n".join(total_rejects))

    print(f"\n═══ Summary ═══")
    print(f"  Total rows ingested: {total_rows:,}")
    print(f"  Rejects:             {len(total_rejects)}")
    if total_rejects:
        print(f"  Reject log:          {REJECTS}")


if __name__ == "__main__":
    main()
