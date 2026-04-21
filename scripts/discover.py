"""Phase 1 Discovery: scan both data folders, report schemas + conventions.

DO NOT build the pipeline yet. This script ONLY describes what's there.
User reviews the report and signs off before Phase 2.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

HIST  = Path("/Users/rohanshah/Desktop/AI Instructions/Trading Developments/Options Data")
INCR  = Path("/Users/rohanshah/Desktop/AI Instructions/Trading Developments/Options Data/New Options Data from 13:4:26 Manually Pulled from TW")


def file_type(p: Path) -> str:
    s = p.suffix.lower()
    if s in (".csv", ".tsv"): return "delimited"
    if s in (".parquet", ".pq"): return "parquet"
    if s in (".xlsx", ".xls"): return "excel"
    if s in (".json", ".jsonl"): return "json"
    return f"other ({s})"


def sample_csv_schema(p: Path, rows: int = 3) -> dict:
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            header = next(reader, [])
            sample_rows = [next(reader, []) for _ in range(rows)]
        # Infer dtype from sample
        dtypes: list[str] = []
        for i, col in enumerate(header):
            col_vals = [r[i] for r in sample_rows if i < len(r) and r[i]]
            if not col_vals: dtypes.append("?"); continue
            if all(v.replace(".", "").replace("-", "").isdigit() for v in col_vals):
                dtypes.append("num")
            elif any(":" in v or "-" in v for v in col_vals if len(v) > 6):
                dtypes.append("date/time")
            else:
                dtypes.append("str")
        return {"columns": header, "dtypes": dtypes, "sample_rows": sample_rows}
    except Exception as e:
        return {"error": str(e)}


def scan(folder: Path, max_depth: int = 6) -> dict:
    if not folder.exists():
        return {"exists": False, "path": str(folder)}

    file_list: list[Path] = []
    for root, _, files in os.walk(folder):
        depth = len(Path(root).relative_to(folder).parts)
        if depth > max_depth: continue
        for f in files:
            if f.startswith("."): continue
            file_list.append(Path(root) / f)

    by_type = Counter(file_type(p) for p in file_list)
    by_suffix = Counter(p.suffix.lower() for p in file_list)

    # Name convention examples (up to 5)
    name_examples = [str(p.relative_to(folder)) for p in file_list[:5]]

    # Schema sampling — up to 3 delimited files
    delim = [p for p in file_list if file_type(p) == "delimited"][:3]
    schemas = {str(p.relative_to(folder)): sample_csv_schema(p) for p in delim}

    # Size stats
    total_bytes = sum(p.stat().st_size for p in file_list)
    biggest = sorted(file_list, key=lambda p: p.stat().st_size, reverse=True)[:3]

    return {
        "exists": True, "path": str(folder),
        "file_count": len(file_list),
        "total_mb": round(total_bytes / 1024 / 1024, 2),
        "by_type": dict(by_type), "by_suffix": dict(by_suffix),
        "name_examples": name_examples,
        "biggest_files": [{"name": str(p.relative_to(folder)), "mb": round(p.stat().st_size/1024/1024, 2)} for p in biggest],
        "sample_schemas": schemas,
    }


def main() -> None:
    report = {
        "historical": scan(HIST, max_depth=6),
        "incremental": scan(INCR, max_depth=6),
    }
    # Crude schema comparison
    h_cols = set()
    i_cols = set()
    for s in report["historical"].get("sample_schemas", {}).values():
        h_cols.update(s.get("columns", []))
    for s in report["incremental"].get("sample_schemas", {}).values():
        i_cols.update(s.get("columns", []))
    report["schema_diff"] = {
        "historical_only_columns": sorted(h_cols - i_cols),
        "incremental_only_columns": sorted(i_cols - h_cols),
        "common_columns": sorted(h_cols & i_cols),
    }
    out_path = Path(__file__).resolve().parent.parent / "results" / "phase1_discovery.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))
    print(f"\n✓ Report written to {out_path}")


if __name__ == "__main__":
    main()
