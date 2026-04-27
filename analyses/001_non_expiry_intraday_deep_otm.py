"""
ANALYSIS 001 — NIFTY non-expiry intraday Deep OTM short strangle

Question from Rohan:
  If I sell NIFTY CE + PE ~3% away from spot between 09:30 and 10:30 on
  non-expiry days, and square off at ~15:00 — how many days was I in profit
  and what was the total?

Assumptions (documented so you can re-run with different params):
  - Non-expiry days only. NIFTY weekly expiry removed (Tuesday as per 2026
    rule; Thursday historically — we exclude BOTH to be safe)
  - Entry: first available bar between 09:30 and 10:30 IST where both CE and
    PE strikes ~3% from spot have a non-zero quote
  - 3% = round to nearest NIFTY strike grid (50 points)
  - Expiry chosen = nearest weekly (earliest expiry ≥ today, ≥ 1 day DTE)
  - Exit: last bar before or at 15:00 IST same day (or last bar of day)
  - P&L per lot = (entry_premium - exit_premium) × 65 (lot size)
  - Both legs: sum of CE + PE P&L
  - No brokerage / slippage modeled (can be layered in a follow-up)
  - 1 lot per leg (you can multiply)

Outputs in results/001_non_expiry_intraday_deep_otm/:
  - summary.md       — headline numbers + quick commentary
  - per_day.csv      — one row per day with entry/exit/P&L
  - equity_curve.png — cumulative P&L over time
"""
from __future__ import annotations

import sys
from datetime import date, time, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "parquet" / "instrument=NIFTY"
OUT   = ROOT / "results" / "001_non_expiry_intraday_deep_otm"
OUT.mkdir(parents=True, exist_ok=True)

# ── Params (tweak as needed) ─────────────────────────────────────────────
DISTANCE_PCT    = 3.0          # % from spot
ENTRY_FROM      = time(9, 30)
ENTRY_TO        = time(10, 30)
EXIT_AT         = time(15, 0)  # square off ≤ this
NIFTY_STRIKE_GRID = 50
LOT_SIZE        = 65           # current NIFTY lot
EXCLUDE_EXPIRY_WEEKDAYS = {1, 3}  # Tue (current), Thu (legacy)

con = duckdb.connect()


def load_all() -> pd.DataFrame:
    path = str(STORE / "**" / "*.parquet")
    print(f"[load] scanning {path}")
    df = con.execute(f"""
        SELECT timestamp, instrument, expiry, strike, option_type,
               open, high, low, close, volume, oi, bar_minutes, dte
        FROM read_parquet('{path}')
        WHERE instrument = 'NIFTY'
    """).fetchdf()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["date"] = df["timestamp"].dt.date
    df["time"] = df["timestamp"].dt.time
    df["weekday"] = df["timestamp"].dt.weekday
    # Normalize expiry to python date (parquet may load it as datetime64)
    df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce").dt.date
    print(f"[load] rows={len(df):,} · dates={df['date'].nunique()} · source={STORE}")
    return df


def spot_at(df: pd.DataFrame, day: date, at: time) -> float | None:
    """Best estimate of NIFTY spot on `day` near `at` — use futures close as proxy."""
    future = df[(df["date"] == day) & (df["option_type"] == "FUT")]
    if future.empty:
        # try SPOT if we later ingest it
        return None
    # Earliest bar at/after `at`
    ts_key = pd.Timestamp.combine(day, at).tz_localize("Asia/Kolkata")
    after = future[future["timestamp"] >= ts_key]
    row = (after.iloc[0] if not after.empty else future.sort_values("timestamp").iloc[-1])
    return float(row["close"])


def pick_strikes(spot: float, dist_pct: float) -> tuple[int, int]:
    ce = round((spot * (1 + dist_pct/100)) / NIFTY_STRIKE_GRID) * NIFTY_STRIKE_GRID
    pe = round((spot * (1 - dist_pct/100)) / NIFTY_STRIKE_GRID) * NIFTY_STRIKE_GRID
    return int(ce), int(pe)


def nearest_expiry(df: pd.DataFrame, day: date) -> date | None:
    """Find the earliest expiry ≥ day+1 with CE + PE data on `day`."""
    options = df[(df["date"] == day) & (df["option_type"].isin(["CE","PE"]))]
    if options.empty: return None
    future_exp = options[options["expiry"] > day]["expiry"].dropna().unique()
    if len(future_exp) == 0: return None
    return min(future_exp)


def first_price_in_window(df: pd.DataFrame, day: date, strike: int,
                            opt: str, exp: date,
                            t_from: time, t_to: time) -> tuple[pd.Timestamp, float] | None:
    leg = df[(df["date"] == day) & (df["expiry"] == exp) &
             (df["strike"] == strike) & (df["option_type"] == opt)]
    leg = leg[(leg["time"] >= t_from) & (leg["time"] <= t_to)]
    if leg.empty: return None
    row = leg.sort_values("timestamp").iloc[0]
    return row["timestamp"], float(row["close"])


def last_price_before(df: pd.DataFrame, day: date, strike: int,
                       opt: str, exp: date, t_cutoff: time) -> tuple[pd.Timestamp, float] | None:
    leg = df[(df["date"] == day) & (df["expiry"] == exp) &
             (df["strike"] == strike) & (df["option_type"] == opt)]
    leg = leg[leg["time"] <= t_cutoff]
    if leg.empty: return None
    row = leg.sort_values("timestamp").iloc[-1]
    return row["timestamp"], float(row["close"])


def analyze() -> None:
    df = load_all()

    records = []
    skipped = []
    all_dates = sorted(df["date"].unique())
    print(f"[run] evaluating {len(all_dates)} trading days")

    for d in all_dates:
        # Skip weekends (shouldn't be present) + expiry days
        if pd.Timestamp(d).weekday() in EXCLUDE_EXPIRY_WEEKDAYS:
            skipped.append((d, "expiry_weekday")); continue

        spot = spot_at(df, d, ENTRY_FROM)
        if spot is None:
            skipped.append((d, "no_spot_proxy")); continue

        exp = nearest_expiry(df, d)
        if exp is None:
            skipped.append((d, "no_expiry")); continue

        # Skip if this day IS the expiry
        if exp == d:
            skipped.append((d, "is_expiry")); continue

        ce_strike, pe_strike = pick_strikes(spot, DISTANCE_PCT)

        ce_entry = first_price_in_window(df, d, ce_strike, "CE", exp, ENTRY_FROM, ENTRY_TO)
        pe_entry = first_price_in_window(df, d, pe_strike, "PE", exp, ENTRY_FROM, ENTRY_TO)
        if ce_entry is None or pe_entry is None:
            skipped.append((d, f"missing_entry_quote CE={ce_strike} PE={pe_strike}"))
            continue

        ce_exit = last_price_before(df, d, ce_strike, "CE", exp, EXIT_AT)
        pe_exit = last_price_before(df, d, pe_strike, "PE", exp, EXIT_AT)
        if ce_exit is None or pe_exit is None:
            skipped.append((d, "missing_exit_quote")); continue

        ce_pnl = (ce_entry[1] - ce_exit[1]) * LOT_SIZE
        pe_pnl = (pe_entry[1] - pe_exit[1]) * LOT_SIZE
        pnl = ce_pnl + pe_pnl

        records.append({
            "date": d, "spot": round(spot, 2), "expiry": exp,
            "ce_strike": ce_strike, "ce_entry_time": ce_entry[0].time(), "ce_entry_price": ce_entry[1],
            "ce_exit_time": ce_exit[0].time(), "ce_exit_price": ce_exit[1], "ce_pnl": round(ce_pnl, 2),
            "pe_strike": pe_strike, "pe_entry_time": pe_entry[0].time(), "pe_entry_price": pe_entry[1],
            "pe_exit_time": pe_exit[0].time(), "pe_exit_price": pe_exit[1], "pe_pnl": round(pe_pnl, 2),
            "total_pnl": round(pnl, 2),
        })

    df_res = pd.DataFrame(records)
    df_res.to_csv(OUT / "per_day.csv", index=False)

    # Equity curve
    if not df_res.empty:
        df_res_sorted = df_res.sort_values("date").reset_index(drop=True)
        df_res_sorted["cum_pnl"] = df_res_sorted["total_pnl"].cumsum()
        fig, ax = plt.subplots(figsize=(11, 5))
        ax.plot(df_res_sorted["date"], df_res_sorted["cum_pnl"], lw=1.8)
        ax.axhline(0, color="gray", lw=0.5)
        ax.fill_between(df_res_sorted["date"], df_res_sorted["cum_pnl"], 0,
                         where=df_res_sorted["cum_pnl"]>=0, color="#22c55e", alpha=0.15)
        ax.fill_between(df_res_sorted["date"], df_res_sorted["cum_pnl"], 0,
                         where=df_res_sorted["cum_pnl"]<0, color="#ef4444", alpha=0.15)
        ax.set_title(f"NIFTY non-expiry intraday Deep OTM · ±{DISTANCE_PCT}% · 1 lot each side")
        ax.set_ylabel("Cumulative P&L (₹)")
        ax.grid(alpha=0.2)
        fig.tight_layout()
        fig.savefig(OUT / "equity_curve.png", dpi=140)
        plt.close(fig)

    # Summary
    winners = (df_res["total_pnl"] > 0).sum() if not df_res.empty else 0
    losers  = (df_res["total_pnl"] < 0).sum() if not df_res.empty else 0
    total_pnl = df_res["total_pnl"].sum() if not df_res.empty else 0
    avg_pnl   = df_res["total_pnl"].mean() if not df_res.empty else 0
    best      = df_res["total_pnl"].max() if not df_res.empty else 0
    worst     = df_res["total_pnl"].min() if not df_res.empty else 0

    summary = f"""# NIFTY Non-Expiry Intraday Deep OTM — Results

**Rule:** Sell NIFTY CE + PE ~{DISTANCE_PCT}% from spot · entry {ENTRY_FROM}-{ENTRY_TO} IST · exit ≤ {EXIT_AT} IST · 1 lot each side · non-expiry days only · nearest weekly expiry.

**Period:** {df_res['date'].min() if not df_res.empty else '—'} to {df_res['date'].max() if not df_res.empty else '—'}

## Headline
| Metric | Value |
|---|---:|
| Days traded | **{len(df_res)}** |
| Days in profit | **{winners}** ({winners / max(len(df_res),1) * 100:.1f}%) |
| Days in loss | {losers} |
| Breakeven / zero | {len(df_res) - winners - losers} |
| **Net total P&L** | **₹{total_pnl:,.0f}** |
| Average per day | ₹{avg_pnl:,.0f} |
| Best day | ₹{best:,.0f} |
| Worst day | ₹{worst:,.0f} |

## Skipped days
{len(skipped)} days skipped. Reasons breakdown:
"""
    from collections import Counter
    for reason, cnt in Counter(r for _, r in skipped).most_common():
        summary += f"- `{reason}`: {cnt}\n"

    summary += f"""
## Files
- `per_day.csv` — every day's entry/exit/P&L
- `equity_curve.png` — cumulative chart

## Params used (edit script + re-run to try variants)
- DISTANCE_PCT = {DISTANCE_PCT}
- ENTRY_FROM = {ENTRY_FROM}, ENTRY_TO = {ENTRY_TO}
- EXIT_AT = {EXIT_AT}
- LOT_SIZE = {LOT_SIZE} (per leg)
- Excluded weekdays (expiry days): {sorted(EXCLUDE_EXPIRY_WEEKDAYS)} (Mon=0..Sun=6)

## Caveats
- No brokerage / slippage modeled — subtract ~₹80-120/leg for real-world estimate
- Spot proxied from NIFTY futures (historical spot file not yet ingested)
- 1 lot per leg; P&L scales linearly with lots
"""
    (OUT / "summary.md").write_text(summary)
    print(f"\n✓ Done. Results in: {OUT}")
    print(f"  Days traded:     {len(df_res)}")
    print(f"  Days in profit:  {winners}")
    print(f"  Net total P&L:   ₹{total_pnl:,.0f}")


if __name__ == "__main__":
    analyze()
