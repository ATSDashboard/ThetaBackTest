"""Replay engine — connects the existing modules to historical Parquet data.

Pulled together from:
- lib/deep_otm.py        — Deep OTM tier + strike recommendation (from algo platform)
- lib/strike_filters.py  — 16 composable filters (from algo platform's Strike Selector Engine)
- lib/strike_rules.py    — AllOf/AnyOf/Not combinator (same)
- lib/technicals.py      — pivot/Fibonacci/weekly pivot

None of that was written in a vacuum — it's the same logic the live trading
engine uses. Backtesting runs the IDENTICAL rule expressions against historical
option chains, so results are apples-to-apples with live behavior.

Usage (after Phase 2 Parquet store is built):

    from lib.replay import replay_rule_over_period
    result = replay_rule_over_period(
        instrument="NIFTY",
        from_date="2025-04-01", to_date="2025-12-31",
        rule={"all_of": [
            {"filter": "DISTANCE_PERCENT", "params": {"min": 3}},
            {"filter": "PREMIUM_PER_LEG",  "params": {"min": 0.8}},
        ]},
        entry_time_ist="09:30", exit_time_ist="15:15",
    )
    print(result.summary())
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path

try:
    import duckdb
    import pandas as pd
except ImportError:
    duckdb = None
    pd = None

from lib.deep_otm import MarketSnapshot
from lib.strike_filters import MarketCtx, StrikeRow
from lib.strike_rules import evaluate_rule


DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "parquet"


@dataclass
class TradeLeg:
    entry_time: datetime
    strike: float
    option_type: str
    entry_price: float
    exit_price: float
    exit_time: datetime
    pnl: float


@dataclass
class ReplayResult:
    instrument: str
    rule: dict
    from_date: date
    to_date: date
    trades: list[TradeLeg] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def summary(self) -> dict:
        n = len(self.trades)
        if n == 0: return {"trades": 0}
        wins = sum(1 for t in self.trades if t.pnl > 0)
        total_pnl = sum(t.pnl for t in self.trades)
        return {
            "trades": n, "wins": wins, "win_rate_pct": round(wins / n * 100, 2),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(total_pnl / n, 2),
            "worst": round(min(t.pnl for t in self.trades), 2),
            "best": round(max(t.pnl for t in self.trades), 2),
        }


def load_chain_at(instrument: str, ts: datetime) -> tuple[list[StrikeRow], MarketCtx]:
    """Load the option chain + spot at a given timestamp from the Parquet store."""
    if duckdb is None:
        raise RuntimeError("duckdb not installed — `pip install -r requirements.txt`")
    con = duckdb.connect(":memory:")
    path = str(DATA_ROOT / f"instrument={instrument}" / "**" / "*.parquet")
    # Placeholder query — real schema confirmed after Phase 1 discovery
    df = con.execute(f"""
        SELECT * FROM read_parquet('{path}')
        WHERE timestamp = ? AND instrument = ?
    """, [ts, instrument]).fetchdf()
    # Normalise into StrikeRow + MarketCtx (once Phase 2 schema is locked in)
    raise NotImplementedError("load_chain_at wired after Phase 2 ingestion")


def replay_rule_over_period(
    instrument: str, from_date: str, to_date: str, rule: dict,
    entry_time_ist: str = "09:30", exit_time_ist: str = "15:15",
) -> ReplayResult:
    """Replay a Strike Selector rule over historical data.

    For each trading day in range:
      1. Load chain at entry_time
      2. Evaluate rule → pick top-matching CE/PE strikes
      3. Load chain at exit_time to realize P&L
      4. Store TradeLeg records
    """
    raise NotImplementedError("Replay engine wired after Phase 2 ingestion — see CLAUDE.md")
