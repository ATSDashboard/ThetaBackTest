"""Sample analysis — non-expiry-day premium decay by distance bucket.

This is the end-to-end smoke test for the stack. Once Phase 2 ingestion
lands, running this script should produce a real decay curve grouped by
cushion bucket.

Pattern for every analysis in this folder:
  - At top: @param block (distances, DTEs, instruments)
  - 1 SQL pull via DuckDB over Parquet
  - 1 DataFrame manipulation + chart
  - Writes to results/YYYY-MM-DD_<slug>/ with summary.md + data.csv + chart.png
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

# ── Params ───────────────────────────────────────────────────────────────
INSTRUMENT = "NIFTY"
PERIOD_FROM = "2025-04-01"
PERIOD_TO = "2025-12-31"
EXCLUDE_EXPIRY_DAYS = True          # Tuesdays for NIFTY, Thursdays for SENSEX
DISTANCE_BUCKETS = [(1, 2), (2, 3), (3, 5), (5, 100)]    # % of spot
DTE_RANGE = (1, 7)                  # weekly horizon

# ── Output location ─────────────────────────────────────────────────────
OUTDIR = Path(__file__).resolve().parent.parent / "results" / f"{date.today()}_non_expiry_premium_decay"
OUTDIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    """
    Once Phase 2 lands:

      1. DuckDB query over data/parquet/ partitioned by instrument/year/month
      2. Group by (distance_bucket, minute_of_day)
      3. Compute mean premium per bucket per minute
      4. Chart: decay curves stacked by bucket
      5. Write summary.md explaining what it tells us
    """
    print("Placeholder — Phase 2 ingestion must complete first")
    print(f"Will write to: {OUTDIR}")


if __name__ == "__main__":
    main()
