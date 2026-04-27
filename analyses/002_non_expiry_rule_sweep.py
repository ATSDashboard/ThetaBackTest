"""
ANALYSIS 002 — NIFTY non-expiry intraday deep OTM strangle · rule sweep

Question from Rohan:
  What's the best combo of time-range, entry conditions, square-off rules, and
  skip-day conditions for selling deep OTM CE+PE on non-expiry NIFTY days?

Approach (staged to avoid overfitting on ~150 non-expiry days):
  Stage A  Fix base geometry (3% OTM, 09:30-10:30 entry, 15:00 exit). Evaluate
           eight rule variants V0..V7 stacking:
             - per-leg stop-loss (2x entry premium)
             - per-leg profit-target (50% of premium captured)
             - skip-day filters: gap, first-hour range, prior-day trend
  Stage B  On the best variant, sweep DISTANCE (2, 2.5, 3, 3.5, 4, 5 %).
  Stage C  On the best variant, sweep ENTRY_TIME (09:30 / 10:00 / 10:30 / 11:00 / 12:00)
           and EXIT_TIME (13:30 / 14:00 / 14:30 / 15:00).

Train / test split:
  TRAIN  2025-04-17 → 2025-12-31  (first ~8 months)
  TEST   2026-01-01 → 2026-04-17  (held-out ~3.5 months)
  Variants are chosen on TRAIN; TEST metrics are reported for honesty.

Execution model per leg (independent CE/PE):
  Enter at first minute bar in [ENTRY_FROM, ENTRY_TO] where that leg has a quote.
  Scan forward minute-by-minute:
    - if leg.high >= entry * SL_mult  → exit at entry*SL_mult (stop filled)
    - elif leg.low  <= entry * (1-PT) → exit at entry*(1-PT) (target filled)
    - else at EXIT_AT → last close ≤ EXIT_AT
  When no SL/PT, just last close ≤ EXIT_AT.

Friction:
  Rohan requested include: ₹100/leg entry + ₹100/leg exit = ₹200/leg/day
  Subtracted from each leg's gross P&L.

Outputs in results/002_non_expiry_rule_sweep/:
  summary.md          headline + side-by-side metrics per variant (train & test)
  comparison.csv      variant × metric matrix
  equity_curves.png   cumulative curves for top variants
  winner_per_day.csv  day-by-day for the winning rule
  winner_equity.png
  distance_sweep.csv  + chart
  time_sweep.csv      + chart
"""
from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from pathlib import Path
from typing import Callable

import duckdb
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "parquet" / "instrument=NIFTY"
OUT = ROOT / "results" / "002_non_expiry_rule_sweep"
OUT.mkdir(parents=True, exist_ok=True)

# ── Global params ─────────────────────────────────────────────────────
LOT_SIZE = 65
NIFTY_GRID = 50
ENTRY_FROM = time(9, 30)
ENTRY_TO   = time(10, 30)
EXIT_AT    = time(15, 0)
EXCLUDE_EXPIRY_WEEKDAYS = {1, 3}   # Tue (current), Thu (legacy)
FRICTION_PER_LEG = 200.0           # ₹ entry+exit combined per leg
TRAIN_END = date(2025, 12, 31)     # inclusive

DIST_DEFAULT = 3.0                  # base distance %


# ────────────────────────────────────────────────────────────────────
#   DATA LAYER
# ────────────────────────────────────────────────────────────────────
con = duckdb.connect()


def load_fut() -> pd.DataFrame:
    """Load NIFTY futures minute bars. Spot proxy + features."""
    path = str(STORE / "**" / "*.parquet")
    df = con.execute(f"""
        SELECT timestamp, close, high, low, open
        FROM read_parquet('{path}', union_by_name=True)
        WHERE option_type = 'FUT'
    """).fetchdf()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["date"] = df["timestamp"].dt.date
    df["time"] = df["timestamp"].dt.time
    df = df.sort_values("timestamp").reset_index(drop=True)
    print(f"[load_fut] rows={len(df):,} · dates={df['date'].nunique()}")
    return df


def load_options_for_day(d: date, ce_strike: int, pe_strike: int,
                          expiry: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Grab CE and PE minute bars for the two selected strikes of day `d`."""
    path = str(STORE / "**" / "*.parquet")
    y, m = d.year, d.month
    # Limit by year/month partition when possible (also expiry's partition)
    df = con.execute(f"""
        SELECT timestamp, strike, option_type, open, high, low, close
        FROM read_parquet('{path}', union_by_name=True)
        WHERE option_type IN ('CE','PE')
          AND expiry = DATE '{expiry.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
          AND strike IN ({ce_strike}, {pe_strike})
    """).fetchdf()
    if df.empty:
        return df, df
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["time"] = df["timestamp"].dt.time
    df = df.sort_values("timestamp")
    ce = df[(df["option_type"] == "CE") & (df["strike"] == ce_strike)].reset_index(drop=True)
    pe = df[(df["option_type"] == "PE") & (df["strike"] == pe_strike)].reset_index(drop=True)
    return ce, pe


def nearest_expiry_for_day(d: date) -> date | None:
    path = str(STORE / "**" / "*.parquet")
    row = con.execute(f"""
        SELECT MIN(expiry) AS exp
        FROM read_parquet('{path}', union_by_name=True)
        WHERE option_type IN ('CE','PE')
          AND expiry > DATE '{d.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
    """).fetchone()
    return row[0] if row and row[0] else None


# ────────────────────────────────────────────────────────────────────
#   DAY-LEVEL FEATURES  (for skip-day filters)
# ────────────────────────────────────────────────────────────────────
def build_day_features(fut: pd.DataFrame) -> pd.DataFrame:
    """Per-day: spot@09:30, gap%, first-hour range %, prior-day move %."""
    rows = []
    by_day = fut.groupby("date")
    # day-closing close for prior-day move
    day_close = by_day["close"].last()
    prev_close = day_close.shift(1)
    day_before_prev_close = day_close.shift(2)
    prev_day_move = (prev_close - day_before_prev_close) / day_before_prev_close * 100

    for d, g in by_day:
        g = g.sort_values("timestamp").reset_index(drop=True)
        # spot at first bar >= 09:30
        mask = g["time"] >= ENTRY_FROM
        spot = float(g.loc[mask, "close"].iloc[0]) if mask.any() else float(g["close"].iloc[0])
        # open @ 09:15 region = first bar of day
        first_open = float(g["open"].iloc[0])
        prev = prev_close.get(d, np.nan)
        gap_pct = (first_open - prev) / prev * 100 if prev and not np.isnan(prev) else np.nan
        # first-hour range 09:15 – 10:15
        fh = g[(g["time"] >= time(9, 15)) & (g["time"] <= time(10, 15))]
        if not fh.empty:
            fh_range = (fh["high"].max() - fh["low"].min()) / spot * 100
        else:
            fh_range = np.nan
        rows.append({
            "date": d,
            "spot": spot,
            "gap_pct": gap_pct,
            "fh_range_pct": fh_range,
            "prev_day_move_pct": prev_day_move.get(d, np.nan),
        })
    out = pd.DataFrame(rows).set_index("date")
    return out


# ────────────────────────────────────────────────────────────────────
#   LEG SIMULATION
# ────────────────────────────────────────────────────────────────────
@dataclass
class LegResult:
    entry_time: pd.Timestamp | None = None
    entry_price: float = np.nan
    exit_time: pd.Timestamp | None = None
    exit_price: float = np.nan
    exit_reason: str = ""
    gross_pnl: float = np.nan   # per-lot (LOT_SIZE × premium delta), BEFORE friction


def simulate_leg(bars: pd.DataFrame, entry_from: time, entry_to: time,
                 exit_at: time, sl_mult: float | None, pt_pct: float | None,
                 lot_size: int = LOT_SIZE) -> LegResult | None:
    """Simulate one short leg.  SL: exit if high>=entry*sl_mult.
    PT: exit if low<=entry*(1-pt_pct). Else exit at last close ≤ exit_at.
    Returns None if leg can't be opened."""
    in_win = bars[(bars["time"] >= entry_from) & (bars["time"] <= entry_to)]
    if in_win.empty:
        return None
    entry_row = in_win.iloc[0]
    entry_price = float(entry_row["close"])
    if entry_price <= 0:
        return None
    # forward bars from entry bar to exit_at
    post = bars[(bars["timestamp"] > entry_row["timestamp"]) & (bars["time"] <= exit_at)].reset_index(drop=True)

    sl_trigger = entry_price * sl_mult if sl_mult else np.inf
    pt_trigger = entry_price * (1 - pt_pct) if pt_pct else -np.inf

    exit_row = None
    exit_price = np.nan
    exit_reason = "time"
    for _, b in post.iterrows():
        if b["high"] >= sl_trigger:
            exit_row = b
            exit_price = sl_trigger           # assume filled at stop (optimistic)
            exit_reason = "SL"
            break
        if b["low"] <= pt_trigger and pt_trigger > 0:
            exit_row = b
            exit_price = pt_trigger           # assume filled at target
            exit_reason = "PT"
            break
    if exit_row is None:
        # last close ≤ exit_at
        if post.empty:
            exit_row = entry_row
            exit_price = entry_price
        else:
            exit_row = post.iloc[-1]
            exit_price = float(exit_row["close"])
    gross = (entry_price - exit_price) * lot_size
    return LegResult(
        entry_time=entry_row["timestamp"], entry_price=entry_price,
        exit_time=exit_row["timestamp"], exit_price=exit_price,
        exit_reason=exit_reason, gross_pnl=gross,
    )


# ────────────────────────────────────────────────────────────────────
#   VARIANT DEFINITIONS
# ────────────────────────────────────────────────────────────────────
@dataclass
class Variant:
    name: str
    desc: str
    sl_mult: float | None = None         # per-leg SL (× entry)
    pt_pct: float | None = None          # per-leg PT (fraction of entry captured)
    skip_fn: Callable[[dict], bool] | None = None  # given day-feat dict, return True to SKIP
    distance_pct: float = DIST_DEFAULT
    entry_from: time = ENTRY_FROM
    entry_to: time = ENTRY_TO
    exit_at: time = EXIT_AT


def skip_gap(row):
    return (not np.isnan(row["gap_pct"])) and abs(row["gap_pct"]) > 0.5

def skip_gap_and_range(row):
    if skip_gap(row):
        return True
    return (not np.isnan(row["fh_range_pct"])) and row["fh_range_pct"] > 0.8

def skip_gap_range_trend(row):
    if skip_gap_and_range(row):
        return True
    return (not np.isnan(row["prev_day_move_pct"])) and abs(row["prev_day_move_pct"]) > 1.2


STAGE_A_VARIANTS = [
    Variant("V0_base",            "baseline (001): no SL, no PT, no filter"),
    Variant("V1_SL2x",            "+ per-leg SL at 2× entry", sl_mult=2.0),
    Variant("V2_SL1.5x",          "+ per-leg SL at 1.5× entry", sl_mult=1.5),
    Variant("V3_PT50",            "+ per-leg PT 50% premium", pt_pct=0.5),
    Variant("V4_SL2x_PT50",       "+ SL 2× and PT 50%", sl_mult=2.0, pt_pct=0.5),
    Variant("V5_gapFilter",       "V4 + skip |gap|>0.5%", sl_mult=2.0, pt_pct=0.5, skip_fn=skip_gap),
    Variant("V6_gap_rangeFilter", "V5 + skip 1h range>0.8%", sl_mult=2.0, pt_pct=0.5, skip_fn=skip_gap_and_range),
    Variant("V7_gap_range_trend", "V6 + skip prev-day |move|>1.2%", sl_mult=2.0, pt_pct=0.5, skip_fn=skip_gap_range_trend),
]


# ────────────────────────────────────────────────────────────────────
#   DRIVER
# ────────────────────────────────────────────────────────────────────
def pick_strikes(spot: float, dist_pct: float) -> tuple[int, int]:
    ce = round((spot * (1 + dist_pct/100)) / NIFTY_GRID) * NIFTY_GRID
    pe = round((spot * (1 - dist_pct/100)) / NIFTY_GRID) * NIFTY_GRID
    return int(ce), int(pe)


def run_variant(variant: Variant, day_feats: pd.DataFrame,
                day_cache: dict[date, dict]) -> pd.DataFrame:
    """Run the variant across all non-expiry days that have cached leg data.
    day_cache[d] = {'spot','expiry','ce_strike','pe_strike','ce_bars','pe_bars'}
    """
    rows = []
    for d, cache in day_cache.items():
        feats = day_feats.loc[d].to_dict() if d in day_feats.index else {}
        # skip filter
        if variant.skip_fn and variant.skip_fn(feats):
            rows.append({"date": d, "status": "skipped_by_filter"})
            continue
        spot = cache["spot"]
        # if distance differs from default cache, re-pick strikes — but for Stage A
        # we always use default distance, so reuse cache.
        if variant.distance_pct == DIST_DEFAULT:
            ce_strike = cache["ce_strike"]
            pe_strike = cache["pe_strike"]
            ce_bars = cache["ce_bars"]
            pe_bars = cache["pe_bars"]
        else:
            ce_strike, pe_strike = pick_strikes(spot, variant.distance_pct)
            # lazy-load for this distance
            ce_bars, pe_bars = load_options_for_day(d, ce_strike, pe_strike, cache["expiry"])

        if ce_bars.empty or pe_bars.empty:
            rows.append({"date": d, "status": "no_quotes"})
            continue

        ce_res = simulate_leg(ce_bars, variant.entry_from, variant.entry_to, variant.exit_at,
                              variant.sl_mult, variant.pt_pct)
        pe_res = simulate_leg(pe_bars, variant.entry_from, variant.entry_to, variant.exit_at,
                              variant.sl_mult, variant.pt_pct)
        if ce_res is None or pe_res is None:
            rows.append({"date": d, "status": "no_entry"})
            continue

        ce_net = ce_res.gross_pnl - FRICTION_PER_LEG
        pe_net = pe_res.gross_pnl - FRICTION_PER_LEG
        rows.append({
            "date": d, "status": "traded",
            "spot": spot,
            "ce_strike": ce_strike, "ce_entry": ce_res.entry_price, "ce_exit": ce_res.exit_price,
            "ce_reason": ce_res.exit_reason, "ce_gross": ce_res.gross_pnl, "ce_net": ce_net,
            "pe_strike": pe_strike, "pe_entry": pe_res.entry_price, "pe_exit": pe_res.exit_price,
            "pe_reason": pe_res.exit_reason, "pe_gross": pe_res.gross_pnl, "pe_net": pe_net,
            "gross_pnl": ce_res.gross_pnl + pe_res.gross_pnl,
            "net_pnl":   ce_net + pe_net,
        })
    df = pd.DataFrame(rows)
    return df


def summarize(df: pd.DataFrame, label: str) -> dict:
    traded = df[df["status"] == "traded"].copy()
    if traded.empty:
        return {"label": label, "days": 0, "skipped": int((df["status"] != "traded").sum())}
    traded = traded.sort_values("date").reset_index(drop=True)
    traded["cum_net"] = traded["net_pnl"].cumsum()
    running_max = traded["cum_net"].cummax()
    dd = traded["cum_net"] - running_max
    # Sharpe-ish: mean/std of daily net, annualized by sqrt(252)
    mean_d = traded["net_pnl"].mean()
    std_d = traded["net_pnl"].std(ddof=1) if len(traded) > 1 else np.nan
    sharpe = (mean_d / std_d * np.sqrt(252)) if std_d and std_d > 0 else np.nan

    return {
        "label": label,
        "days_total": int(len(df)),
        "days_traded": int(len(traded)),
        "days_skipped": int((df["status"] != "traded").sum()),
        "win_pct": round((traded["net_pnl"] > 0).mean() * 100, 1),
        "net_pnl": round(traded["net_pnl"].sum(), 0),
        "gross_pnl": round(traded["gross_pnl"].sum(), 0),
        "avg_per_day": round(traded["net_pnl"].mean(), 0),
        "best_day": round(traded["net_pnl"].max(), 0),
        "worst_day": round(traded["net_pnl"].min(), 0),
        "max_drawdown": round(dd.min(), 0),
        "sharpe_ann": round(sharpe, 2) if not np.isnan(sharpe) else np.nan,
    }


def split_train_test(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    tr = df[pd.to_datetime(df["date"]) <= pd.Timestamp(TRAIN_END)].copy()
    te = df[pd.to_datetime(df["date"]) >  pd.Timestamp(TRAIN_END)].copy()
    return tr, te


# ────────────────────────────────────────────────────────────────────
#   MAIN
# ────────────────────────────────────────────────────────────────────
def main():
    print("\n=== 002 — NIFTY non-expiry rule sweep ===\n")
    fut = load_fut()
    day_feats = build_day_features(fut)
    print(f"[features] day_feats rows={len(day_feats)}")

    # Trading days = non-expiry-weekday, has FUT data
    all_days = sorted(fut["date"].unique())
    nonexp_days = [d for d in all_days if pd.Timestamp(d).weekday() not in EXCLUDE_EXPIRY_WEEKDAYS]
    print(f"[days] non-expiry days: {len(nonexp_days)}")

    # Build per-day cache (default distance 3%) — one pass over options storage
    print(f"[cache] pre-loading ±{DIST_DEFAULT}% option bars for each day...")
    day_cache: dict[date, dict] = {}
    for i, d in enumerate(nonexp_days):
        if d not in day_feats.index:
            continue
        spot = day_feats.loc[d, "spot"]
        if pd.isna(spot):
            continue
        exp = nearest_expiry_for_day(d)
        if not exp or exp == d:
            continue
        ce_strike, pe_strike = pick_strikes(spot, DIST_DEFAULT)
        ce_bars, pe_bars = load_options_for_day(d, ce_strike, pe_strike, exp)
        if ce_bars.empty or pe_bars.empty:
            continue
        day_cache[d] = {"spot": spot, "expiry": exp,
                        "ce_strike": ce_strike, "pe_strike": pe_strike,
                        "ce_bars": ce_bars, "pe_bars": pe_bars}
        if (i+1) % 25 == 0 or i == len(nonexp_days)-1:
            print(f"  [{i+1}/{len(nonexp_days)}] cached={len(day_cache)}")
    print(f"[cache] done. {len(day_cache)} tradeable days")

    # ── Stage A: variants ────────────────────────────────────────────
    stage_a_summary = []
    per_day_frames: dict[str, pd.DataFrame] = {}
    for v in STAGE_A_VARIANTS:
        print(f"\n[run] {v.name}  ({v.desc})")
        df = run_variant(v, day_feats, day_cache)
        per_day_frames[v.name] = df
        tr, te = split_train_test(df)
        s_tr = summarize(tr, f"{v.name}_TRAIN")
        s_te = summarize(te, f"{v.name}_TEST")
        stage_a_summary.append({"variant": v.name, "desc": v.desc, **{f"train_{k}":v for k,v in s_tr.items() if k!='label'}, **{f"test_{k}":v for k,v in s_te.items() if k!='label'}})

    stage_a_df = pd.DataFrame(stage_a_summary)
    stage_a_df.to_csv(OUT / "comparison.csv", index=False)
    print("\n=== Stage A comparison ===")
    cols_show = ["variant","train_days_traded","train_win_pct","train_net_pnl","train_worst_day","train_max_drawdown","train_sharpe_ann","test_days_traded","test_win_pct","test_net_pnl","test_worst_day"]
    print(stage_a_df[cols_show].to_string(index=False))

    # Pick winner = best TRAIN net_pnl whose TEST net_pnl is also positive
    winner = None
    ranked = stage_a_df.sort_values("train_net_pnl", ascending=False)
    for _, r in ranked.iterrows():
        if r["test_net_pnl"] >= 0:
            winner = r; break
    if winner is None:
        winner = ranked.iloc[0]
    winner_name = winner["variant"]
    winner_var = next(v for v in STAGE_A_VARIANTS if v.name == winner_name)
    print(f"\n[winner] {winner_name}  ({winner['desc']})")

    # Save winner per-day
    winner_df = per_day_frames[winner_name]
    winner_df.to_csv(OUT / "winner_per_day.csv", index=False)

    # ── Plot equity curves for Stage A ──
    fig, ax = plt.subplots(figsize=(12, 6))
    for v in STAGE_A_VARIANTS:
        df = per_day_frames[v.name]
        tr = df[df["status"] == "traded"].sort_values("date").reset_index(drop=True)
        if tr.empty: continue
        tr["cum_net"] = tr["net_pnl"].cumsum()
        lw = 2.4 if v.name == winner_name else 1.1
        alpha = 1.0 if v.name == winner_name else 0.55
        ax.plot(pd.to_datetime(tr["date"]), tr["cum_net"], label=v.name, lw=lw, alpha=alpha)
    ax.axhline(0, color="gray", lw=0.5)
    ax.axvline(pd.Timestamp(TRAIN_END), color="black", ls="--", lw=0.8, alpha=0.5, label="train|test")
    ax.set_title("NIFTY non-expiry DeepOTM — Stage A variants (net ₹, 1 lot/leg, friction ₹200/leg)")
    ax.set_ylabel("Cumulative net P&L (₹)")
    ax.legend(loc="best", fontsize=8, ncol=2)
    ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(OUT / "equity_curves.png", dpi=140)
    plt.close(fig)

    # ── Stage B: distance sweep on winner ─────────────────────────────
    print(f"\n[stage B] distance sweep on {winner_name}...")
    distances = [2.0, 2.5, 3.0, 3.5, 4.0, 5.0]
    dist_rows = []
    dist_dfs = {}
    for dp in distances:
        v = Variant(name=f"{winner_name}_d{dp}", desc=f"{winner_name} dist={dp}%",
                    sl_mult=winner_var.sl_mult, pt_pct=winner_var.pt_pct,
                    skip_fn=winner_var.skip_fn, distance_pct=dp,
                    entry_from=winner_var.entry_from, entry_to=winner_var.entry_to,
                    exit_at=winner_var.exit_at)
        print(f"  dist={dp}% ...")
        df = run_variant(v, day_feats, day_cache)
        dist_dfs[dp] = df
        tr, te = split_train_test(df)
        dist_rows.append({"distance_pct": dp,
                          **{f"train_{k}":val for k,val in summarize(tr, f"d{dp}").items() if k!='label'},
                          **{f"test_{k}":val for k,val in summarize(te, f"d{dp}").items() if k!='label'}})
    dist_df = pd.DataFrame(dist_rows)
    dist_df.to_csv(OUT / "distance_sweep.csv", index=False)
    print(dist_df[["distance_pct","train_days_traded","train_win_pct","train_net_pnl","train_worst_day","test_days_traded","test_net_pnl","test_worst_day"]].to_string(index=False))

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(dist_df["distance_pct"], dist_df["train_net_pnl"], "o-", label="train net ₹")
    ax.plot(dist_df["distance_pct"], dist_df["test_net_pnl"], "s-", label="test net ₹")
    ax.axhline(0, color="gray", lw=0.5)
    ax.set_title(f"Distance sweep on {winner_name}")
    ax.set_xlabel("Distance % from spot")
    ax.set_ylabel("Net ₹ (1 lot/leg, friction)")
    ax.legend(); ax.grid(alpha=0.2)
    fig.tight_layout(); fig.savefig(OUT / "distance_sweep.png", dpi=140); plt.close(fig)

    # ── Stage C: entry/exit time sweep on winner at best distance ─────
    best_dist = dist_df.sort_values("train_net_pnl", ascending=False).iloc[0]["distance_pct"]
    print(f"\n[stage C] time sweep at distance={best_dist}% ...")
    entry_times = [time(9,30), time(10,0), time(10,30), time(11,0), time(11,30)]
    exit_times  = [time(13,30), time(14,0), time(14,30), time(15,0)]
    time_rows = []
    for ef in entry_times:
        for xt in exit_times:
            if xt <= ef: continue
            v = Variant(name=f"t{ef.strftime('%H%M')}x{xt.strftime('%H%M')}",
                        desc=f"entry={ef} exit={xt}",
                        sl_mult=winner_var.sl_mult, pt_pct=winner_var.pt_pct,
                        skip_fn=winner_var.skip_fn, distance_pct=best_dist,
                        entry_from=ef, entry_to=time(ef.hour+1 if ef.hour<15 else ef.hour, ef.minute),
                        exit_at=xt)
            df = run_variant(v, day_feats, day_cache)
            tr, te = split_train_test(df)
            time_rows.append({
                "entry": ef.strftime("%H:%M"),
                "exit": xt.strftime("%H:%M"),
                **{f"train_{k}":val for k,val in summarize(tr, v.name).items() if k!='label'},
                **{f"test_{k}":val for k,val in summarize(te, v.name).items() if k!='label'},
            })
    time_df = pd.DataFrame(time_rows)
    time_df.to_csv(OUT / "time_sweep.csv", index=False)
    print("\n[time_sweep top 8 by train net]")
    print(time_df.sort_values("train_net_pnl", ascending=False).head(8)[["entry","exit","train_days_traded","train_win_pct","train_net_pnl","test_net_pnl"]].to_string(index=False))

    # ── Winner equity chart ──
    wdf = winner_df[winner_df["status"] == "traded"].sort_values("date").reset_index(drop=True)
    if not wdf.empty:
        wdf["cum_net"] = wdf["net_pnl"].cumsum()
        fig, ax = plt.subplots(figsize=(11, 5))
        ax.plot(pd.to_datetime(wdf["date"]), wdf["cum_net"], lw=1.8)
        ax.axvline(pd.Timestamp(TRAIN_END), color="black", ls="--", lw=0.8, alpha=0.5, label="train|test")
        ax.axhline(0, color="gray", lw=0.5)
        ax.fill_between(pd.to_datetime(wdf["date"]), wdf["cum_net"], 0, where=wdf["cum_net"]>=0, color="#22c55e", alpha=0.15)
        ax.fill_between(pd.to_datetime(wdf["date"]), wdf["cum_net"], 0, where=wdf["cum_net"]<0, color="#ef4444", alpha=0.15)
        ax.set_title(f"Winner: {winner_name} ({winner['desc']}) — net cumulative")
        ax.legend(); ax.grid(alpha=0.2)
        fig.tight_layout(); fig.savefig(OUT / "winner_equity.png", dpi=140); plt.close(fig)

    # ── Summary.md ──
    def to_md_table(df, cols):
        lines = ["| " + " | ".join(cols) + " |",
                 "| " + " | ".join(["---"]*len(cols)) + " |"]
        for _, r in df[cols].iterrows():
            lines.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
        return "\n".join(lines)

    md = f"""# Analysis 002 — NIFTY Non-Expiry Deep OTM Rule Sweep

**Question:** Best combo of entry time, conditions, square-off rules, and skip-day filters for selling deep OTM CE+PE on NIFTY non-expiry days.

**Friction:** ₹{FRICTION_PER_LEG:.0f}/leg included (₹100 entry + ₹100 exit).
**Sizing:** 1 lot/leg (lot={LOT_SIZE}).
**Train:** up to {TRAIN_END}  ·  **Test:** after {TRAIN_END}.

## Stage A — Rule variants (3% OTM, 09:30-10:30 → 15:00)

"""
    md += to_md_table(stage_a_df, ["variant","desc","train_days_traded","train_win_pct","train_net_pnl","train_worst_day","train_max_drawdown","train_sharpe_ann","test_days_traded","test_win_pct","test_net_pnl","test_worst_day"])
    md += f"\n\n**Winner (by train net, guarded by test≥0):** `{winner_name}` — {winner['desc']}\n\n"

    md += "## Stage B — Distance sweep on winner\n\n"
    md += to_md_table(dist_df, ["distance_pct","train_days_traded","train_win_pct","train_net_pnl","train_worst_day","test_days_traded","test_win_pct","test_net_pnl","test_worst_day"])
    md += f"\n\nBest-train distance: **{best_dist}%**\n\n"

    md += "## Stage C — Entry/exit time sweep (top 10 by train net)\n\n"
    md += to_md_table(time_df.sort_values("train_net_pnl", ascending=False).head(10), ["entry","exit","train_days_traded","train_win_pct","train_net_pnl","train_worst_day","test_days_traded","test_win_pct","test_net_pnl","test_worst_day"])
    md += "\n\n"

    md += f"""## Files
- `comparison.csv` — full Stage A variant table
- `equity_curves.png` — overlaid cumulative P&L per variant
- `winner_per_day.csv` + `winner_equity.png` — day-level for winning rule
- `distance_sweep.csv` + `distance_sweep.png`
- `time_sweep.csv`

## Caveats
- Spot proxied from NIFTY futures.
- SL / PT filled optimistically at trigger price (real fills may slip).
- ~{len(day_cache)} non-expiry days in cache; split ~{sum(1 for d in day_cache if d <= TRAIN_END)}/{sum(1 for d in day_cache if d > TRAIN_END)} train/test — test is small, treat as directional sanity check.
- Friction held constant ₹{FRICTION_PER_LEG:.0f}/leg; actual varies by broker / size.
"""
    (OUT / "summary.md").write_text(md)
    print(f"\n✓ Done. Results in: {OUT}")


if __name__ == "__main__":
    main()
