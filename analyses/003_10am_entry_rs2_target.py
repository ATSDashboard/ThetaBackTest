"""
ANALYSIS 003 — NIFTY deep OTM strangle · 10:00 entry · ₹2 combined target · DTE slabs

Question from Rohan:
  Sell NIFTY CE+PE at 10:00 on non-expiry days.  Three distance cases:
  3%, 4%, 5% from spot.  Exit both legs either when the COMBINED premium has
  decayed by ₹2 total, OR at 15:15.  Then add a variant with a ₹6 combined
  loss stop.  Weekly expiries only; break results down by days-to-expiry
  bucket (DTE = 1 / 2 / 3 / 4 / 5+).

Interpretation of "₹2" / "₹6":
  Combined premium means CE_price + PE_price.
  Target hit when  (entry_CE + entry_PE) − (now_CE + now_PE)  ≥ 2   (₹2 per share)
  SL hit     when  (now_CE + now_PE)     − (entry_CE + entry_PE) ≥ 6
  Per lot that's  target = +₹{2×LOT} gross,  stop = −₹{6×LOT} gross.

Friction (Rohan asked include):
  ₹100/leg entry + ₹100/leg exit = ₹200/leg = ₹400/day/lot round-trip.
  **Warning: ₹2 target = +₹130 gross/lot; ₹400 friction ≫ target → a hit-only
  strategy is net-negative by construction.  Both gross and net are reported.**

Outputs in results/003_10am_entry_rs2_target/:
  summary.md                headline + variant × distance × DTE table
  target_only.csv           per-day for target-only variant
  target_with_stop.csv      per-day for target+SL variant
  by_dte.csv                variant × distance × DTE slab metrics
  equity_curves.png         cumulative net for each (variant, distance) pair
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "parquet" / "instrument=NIFTY"
OUT   = ROOT / "results" / "003_10am_entry_rs2_target"
OUT.mkdir(parents=True, exist_ok=True)

# ── Params ────────────────────────────────────────────────────────────
LOT_SIZE   = 65
NIFTY_GRID = 50
ENTRY_AT   = time(10, 0)                 # first bar ≥ this
EXIT_AT    = time(15, 15)                # cutoff for time-exit
PT_POINTS  = 2.0                         # ₹2 combined decay
SL_POINTS  = 6.0                         # ₹6 combined adverse
FRICTION_PER_LEG = 200.0                 # ₹ round-trip per leg
EXCLUDE_EXPIRY_WEEKDAYS = {1, 3}         # Tue (current), Thu (legacy)
DISTANCES  = [3.0, 4.0, 5.0]
DTE_BUCKETS = [1, 2, 3, 4]               # others → "5+"

con = duckdb.connect()


# ── Data helpers (same style as 002) ──────────────────────────────────
def load_fut_spot() -> pd.DataFrame:
    path = str(STORE / "**" / "*.parquet")
    df = con.execute(f"""
        SELECT timestamp, close
        FROM read_parquet('{path}', union_by_name=True)
        WHERE option_type = 'FUT'
    """).fetchdf()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["date"] = df["timestamp"].dt.date
    df["time"] = df["timestamp"].dt.time
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def nearest_weekly_expiry(d: date) -> date | None:
    path = str(STORE / "**" / "*.parquet")
    row = con.execute(f"""
        SELECT MIN(expiry) AS exp
        FROM read_parquet('{path}', union_by_name=True)
        WHERE option_type IN ('CE','PE')
          AND expiry > DATE '{d.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
    """).fetchone()
    return row[0] if row and row[0] else None


def load_leg_bars(d: date, exp: date, ce_strike: int, pe_strike: int):
    path = str(STORE / "**" / "*.parquet")
    df = con.execute(f"""
        SELECT timestamp, strike, option_type, open, high, low, close
        FROM read_parquet('{path}', union_by_name=True)
        WHERE option_type IN ('CE','PE')
          AND expiry = DATE '{exp.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
          AND strike IN ({ce_strike}, {pe_strike})
    """).fetchdf()
    if df.empty:
        return df, df
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["time"] = df["timestamp"].dt.time
    df = df.sort_values("timestamp")
    ce = df[(df["option_type"]=="CE") & (df["strike"]==ce_strike)].reset_index(drop=True)
    pe = df[(df["option_type"]=="PE") & (df["strike"]==pe_strike)].reset_index(drop=True)
    return ce, pe


def pick_strikes(spot: float, dist_pct: float):
    ce = round((spot * (1 + dist_pct/100)) / NIFTY_GRID) * NIFTY_GRID
    pe = round((spot * (1 - dist_pct/100)) / NIFTY_GRID) * NIFTY_GRID
    return int(ce), int(pe)


def spot_at(fut: pd.DataFrame, d: date, at: time) -> float | None:
    g = fut[fut["date"] == d]
    if g.empty:
        return None
    after = g[g["time"] >= at]
    if after.empty:
        return float(g["close"].iloc[-1])
    return float(after["close"].iloc[0])


# ── Simulator: joint leg walk, combined target/stop ───────────────────
@dataclass
class DayTrade:
    date: date
    expiry: date
    dte: int
    distance_pct: float
    spot: float
    ce_strike: int
    pe_strike: int
    entry_time: pd.Timestamp | None = None
    ce_entry: float = np.nan
    pe_entry: float = np.nan
    exit_time: pd.Timestamp | None = None
    ce_exit: float = np.nan
    pe_exit: float = np.nan
    exit_reason: str = ""
    combined_entry: float = np.nan
    combined_exit:  float = np.nan
    gross_pnl: float = np.nan
    net_pnl: float = np.nan


def simulate(d: date, exp: date, dist_pct: float, spot: float,
             ce_bars: pd.DataFrame, pe_bars: pd.DataFrame,
             use_stop: bool) -> DayTrade | None:
    """Enter both legs at first bar ≥ ENTRY_AT where both have a quote.
    Walk forward minute-by-minute, computing combined premium.
    Target: combined drops by PT_POINTS → exit.
    Stop (if enabled): combined rises by SL_POINTS → exit.
    Else at last bar ≤ EXIT_AT.
    Exit uses each leg's bar close at that minute."""
    ce_e = ce_bars[ce_bars["time"] >= ENTRY_AT]
    pe_e = pe_bars[pe_bars["time"] >= ENTRY_AT]
    if ce_e.empty or pe_e.empty:
        return None
    # Align: use first common timestamp (should normally be the 10:00 bar for both)
    ce_first_ts = ce_e["timestamp"].iloc[0]
    pe_first_ts = pe_e["timestamp"].iloc[0]
    entry_ts = max(ce_first_ts, pe_first_ts)
    ce_row = ce_bars[ce_bars["timestamp"] == entry_ts]
    pe_row = pe_bars[pe_bars["timestamp"] == entry_ts]
    if ce_row.empty or pe_row.empty:
        # fallback: nearest later bar present in both
        merged = pd.merge(ce_bars[["timestamp","close","high","low"]],
                          pe_bars[["timestamp","close","high","low"]],
                          on="timestamp", suffixes=("_ce","_pe"))
        merged = merged[merged["timestamp"] >= pd.Timestamp.combine(d, ENTRY_AT).tz_localize("Asia/Kolkata")]
        if merged.empty:
            return None
        entry_ts = merged["timestamp"].iloc[0]
        ce_entry = merged["close_ce"].iloc[0]
        pe_entry = merged["close_pe"].iloc[0]
    else:
        ce_entry = float(ce_row["close"].iloc[0])
        pe_entry = float(pe_row["close"].iloc[0])

    if ce_entry <= 0 or pe_entry <= 0:
        return None

    combined_entry = ce_entry + pe_entry
    ce_strike = int(ce_bars["strike"].iloc[0])
    pe_strike = int(pe_bars["strike"].iloc[0])

    # Align forward bars on timestamp
    fwd = pd.merge(
        ce_bars[["timestamp","open","high","low","close"]].rename(
            columns={"open":"ce_o","high":"ce_h","low":"ce_l","close":"ce_c"}),
        pe_bars[["timestamp","open","high","low","close"]].rename(
            columns={"open":"pe_o","high":"pe_h","low":"pe_l","close":"pe_c"}),
        on="timestamp", how="inner"
    )
    fwd = fwd[fwd["timestamp"] > entry_ts]
    fwd = fwd[fwd["timestamp"].dt.time <= EXIT_AT].reset_index(drop=True)

    exit_ts = None
    ce_exit = np.nan
    pe_exit = np.nan
    reason = "time"

    for _, b in fwd.iterrows():
        # Check both target and stop within the bar using best/worst intrabar prices.
        # Most-favorable combined for short = ce_l + pe_l (both lowest) → target test
        # Most-adverse combined = ce_h + pe_h (both highest) → stop test
        # Within a minute we can't tell order; adopt conservative: test STOP first if enabled.
        fav = b["ce_l"] + b["pe_l"]
        adv = b["ce_h"] + b["pe_h"]
        stop_hit = use_stop and (adv - combined_entry) >= SL_POINTS
        target_hit = (combined_entry - fav) >= PT_POINTS

        if stop_hit and target_hit:
            # Tie-break conservatively → assume stop trips first
            ce_exit = b["ce_c"]                  # we don't know exact fill; use close as proxy
            pe_exit = b["pe_c"]
            # But guarantee SL is respected in P&L: worst case
            # approximate fill at the adverse side
            ce_exit_est = ce_entry + max(0, (b["ce_h"] - ce_entry))
            pe_exit_est = pe_entry + max(0, (b["pe_h"] - pe_entry))
            if (ce_exit_est + pe_exit_est) - combined_entry >= SL_POINTS:
                ce_exit, pe_exit = ce_exit_est, pe_exit_est
            exit_ts = b["timestamp"]; reason = "SL"; break
        if stop_hit:
            ce_exit_est = b["ce_h"]
            pe_exit_est = b["pe_h"]
            ce_exit, pe_exit = ce_exit_est, pe_exit_est
            exit_ts = b["timestamp"]; reason = "SL"; break
        if target_hit:
            ce_exit_est = b["ce_l"]
            pe_exit_est = b["pe_l"]
            ce_exit, pe_exit = ce_exit_est, pe_exit_est
            exit_ts = b["timestamp"]; reason = "PT"; break

    if exit_ts is None:
        # time exit
        if fwd.empty:
            exit_ts = entry_ts; ce_exit = ce_entry; pe_exit = pe_entry
        else:
            last = fwd.iloc[-1]
            exit_ts = last["timestamp"]
            ce_exit = float(last["ce_c"])
            pe_exit = float(last["pe_c"])
        reason = "time"

    combined_exit = ce_exit + pe_exit
    gross = (combined_entry - combined_exit) * LOT_SIZE
    net = gross - 2 * FRICTION_PER_LEG          # two legs, each ₹200 round-trip

    return DayTrade(
        date=d, expiry=exp, dte=(exp - d).days, distance_pct=dist_pct, spot=spot,
        ce_strike=ce_strike, pe_strike=pe_strike,
        entry_time=entry_ts, ce_entry=ce_entry, pe_entry=pe_entry,
        exit_time=exit_ts, ce_exit=ce_exit, pe_exit=pe_exit, exit_reason=reason,
        combined_entry=combined_entry, combined_exit=combined_exit,
        gross_pnl=gross, net_pnl=net,
    )


def dte_bucket_label(dte: int) -> str:
    return str(dte) if dte in DTE_BUCKETS else "5+"


def summarize_group(sub: pd.DataFrame) -> dict:
    if sub.empty:
        return {"days": 0, "win_pct": np.nan, "gross": 0, "net": 0,
                "avg_gross": np.nan, "avg_net": np.nan,
                "best": np.nan, "worst": np.nan,
                "pt_hits": 0, "sl_hits": 0, "time_exits": 0}
    return {
        "days": len(sub),
        "win_pct": round((sub["net_pnl"] > 0).mean() * 100, 1),
        "gross": round(sub["gross_pnl"].sum(), 0),
        "net":   round(sub["net_pnl"].sum(), 0),
        "avg_gross": round(sub["gross_pnl"].mean(), 0),
        "avg_net":   round(sub["net_pnl"].mean(), 0),
        "best":  round(sub["net_pnl"].max(), 0),
        "worst": round(sub["net_pnl"].min(), 0),
        "pt_hits":    int((sub["exit_reason"]=="PT").sum()),
        "sl_hits":    int((sub["exit_reason"]=="SL").sum()),
        "time_exits": int((sub["exit_reason"]=="time").sum()),
    }


def main():
    print("\n=== 003 — 10am entry · ₹2 target · DTE-bucketed ===\n")
    fut = load_fut_spot()
    all_days = sorted(fut["date"].unique())
    nonexp = [d for d in all_days if pd.Timestamp(d).weekday() not in EXCLUDE_EXPIRY_WEEKDAYS]
    print(f"[days] non-expiry: {len(nonexp)}")

    # Pre-compute (expiry, strikes) and cache leg bars per (day, distance)
    cache = {}   # keyed (day, distance_pct)
    day_meta = {}
    for i, d in enumerate(nonexp):
        exp = nearest_weekly_expiry(d)
        if not exp or exp == d:
            continue
        spot = spot_at(fut, d, ENTRY_AT)
        if spot is None:
            continue
        day_meta[d] = {"expiry": exp, "spot": spot, "dte": (exp - d).days}
        for dp in DISTANCES:
            ce_s, pe_s = pick_strikes(spot, dp)
            ce_bars, pe_bars = load_leg_bars(d, exp, ce_s, pe_s)
            if ce_bars.empty or pe_bars.empty:
                continue
            cache[(d, dp)] = (ce_s, pe_s, ce_bars, pe_bars)
        if (i+1) % 25 == 0 or i == len(nonexp)-1:
            print(f"  [{i+1}/{len(nonexp)}] days with ≥1 dist cached={len(day_meta)}")
    print(f"[cache] day_meta={len(day_meta)} · leg-pairs={len(cache)}")

    # ── Run both variants (target-only, target+stop) across distances ──
    results = {}        # results[(variant, dist)] = DataFrame
    for use_stop in [False, True]:
        label = "tgt_stop" if use_stop else "tgt_only"
        print(f"\n[simulate] variant={label}")
        for dp in DISTANCES:
            rows = []
            for d, meta in day_meta.items():
                if (d, dp) not in cache:
                    continue
                ce_s, pe_s, ce_bars, pe_bars = cache[(d, dp)]
                tr = simulate(d, meta["expiry"], dp, meta["spot"], ce_bars, pe_bars, use_stop)
                if tr is None:
                    continue
                rows.append(tr.__dict__)
            df = pd.DataFrame(rows)
            if not df.empty:
                df["dte_bucket"] = df["dte"].apply(dte_bucket_label)
                df = df.sort_values("date").reset_index(drop=True)
                df["cum_net"]   = df["net_pnl"].cumsum()
                df["cum_gross"] = df["gross_pnl"].cumsum()
            results[(label, dp)] = df

    # ── Save per-variant per-day files ──
    combined_to = {"tgt_only": "target_only.csv", "tgt_stop": "target_with_stop.csv"}
    for var_label, fname in combined_to.items():
        frames = []
        for dp in DISTANCES:
            df = results.get((var_label, dp))
            if df is None or df.empty: continue
            d2 = df.copy(); d2["variant"] = var_label
            frames.append(d2)
        if frames:
            pd.concat(frames, ignore_index=True).to_csv(OUT / fname, index=False)

    # ── Headline: by (variant × distance) ─────────────────────────────
    headline_rows = []
    for var_label in ["tgt_only", "tgt_stop"]:
        for dp in DISTANCES:
            df = results.get((var_label, dp))
            s = summarize_group(df) if df is not None else summarize_group(pd.DataFrame())
            headline_rows.append({"variant": var_label, "distance_pct": dp, **s})
    headline = pd.DataFrame(headline_rows)
    print("\n=== Headline: variant × distance (all non-expiry days, all DTE) ===")
    print(headline.to_string(index=False))

    # ── By DTE bucket ─────────────────────────────────────────────────
    by_dte_rows = []
    for var_label in ["tgt_only", "tgt_stop"]:
        for dp in DISTANCES:
            df = results.get((var_label, dp))
            if df is None or df.empty: continue
            for bucket in ["1","2","3","4","5+"]:
                sub = df[df["dte_bucket"] == bucket]
                s = summarize_group(sub)
                by_dte_rows.append({"variant": var_label, "distance_pct": dp, "dte": bucket, **s})
    by_dte = pd.DataFrame(by_dte_rows)
    by_dte.to_csv(OUT / "by_dte.csv", index=False)
    print("\n=== By DTE bucket (variant × distance × DTE) ===")
    print(by_dte.to_string(index=False))

    # ── Equity curves ─────────────────────────────────────────────────
    fig, axes = plt.subplots(1, 2, figsize=(15, 6), sharey=True)
    colors = {3.0:"#2563eb", 4.0:"#16a34a", 5.0:"#ea580c"}
    for ax, var_label, title in zip(axes, ["tgt_only","tgt_stop"], ["Target only (+₹2)","Target +₹2 / Stop −₹6"]):
        for dp in DISTANCES:
            df = results.get((var_label, dp))
            if df is None or df.empty: continue
            ax.plot(pd.to_datetime(df["date"]), df["cum_net"],
                    color=colors[dp], lw=1.8, label=f"{dp}% net")
            ax.plot(pd.to_datetime(df["date"]), df["cum_gross"],
                    color=colors[dp], lw=1.0, alpha=0.35, ls="--",
                    label=f"{dp}% gross")
        ax.axhline(0, color="gray", lw=0.5)
        ax.set_title(title); ax.grid(alpha=0.2)
        ax.legend(fontsize=8, ncol=2)
    axes[0].set_ylabel("Cumulative P&L (₹)")
    fig.suptitle("NIFTY 10:00 entry · exit ₹2 target or 15:15 · 1 lot/leg", y=0.99)
    fig.tight_layout()
    fig.savefig(OUT / "equity_curves.png", dpi=140)
    plt.close(fig)

    # ── Summary.md ─────────────────────────────────────────────────────
    def md_table(df, cols):
        out = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"]*len(cols)) + " |"]
        for _, r in df[cols].iterrows():
            out.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
        return "\n".join(out)

    md = f"""# 003 — 10:00 entry · ₹2 combined target · DTE slabs

**Rule (variant A, target-only):**
- Sell NIFTY CE & PE at first 1-min bar ≥ 10:00 IST
- Distance from spot: 3% / 4% / 5% (tested separately, nearest ₹50 strike)
- Exit BOTH legs when combined premium has decayed by **₹{PT_POINTS:.0f} (points)** — i.e. (CE_entry + PE_entry) − (CE_now + PE_now) ≥ {PT_POINTS:.0f}
- Otherwise square off at **{EXIT_AT.strftime('%H:%M')}**
- Non-expiry days only (skips Tue & Thu); nearest weekly expiry leg

**Rule (variant B, target + stop):** same as A but ALSO exit both legs when combined **loss** ≥ ₹{SL_POINTS:.0f} points.

**Sizing:** 1 lot per leg (lot={LOT_SIZE}).
**Friction:** ₹{FRICTION_PER_LEG:.0f} round-trip per leg, ₹{2*FRICTION_PER_LEG:.0f} total/day, subtracted in `net`.

> ⚠ **Economic reality check:** ₹2 combined decay × 65 lot = ₹{PT_POINTS*LOT_SIZE:.0f} gross per hit.  Friction is ₹{2*FRICTION_PER_LEG:.0f}/day.  Every target-hit day is gross-positive but **net-negative before any losses**.  Gross figures are shown alongside net so you can see what the decay-capture itself earns.

## Headline: variant × distance (all non-expiry days, all DTE)

"""
    md += md_table(headline, ["variant","distance_pct","days","win_pct","gross","net","avg_gross","avg_net","best","worst","pt_hits","sl_hits","time_exits"])

    md += "\n\n## By DTE slab (variant × distance × DTE)\n\nDTE = calendar days between trade date and expiry. Weekly expiries in data are Tue (current) and Thu (legacy); `5+` catches days 5-6 away from expiry (e.g. Wed before next-week's Tue expiry).\n\n"
    md += md_table(by_dte, ["variant","distance_pct","dte","days","win_pct","gross","net","avg_gross","avg_net","best","worst","pt_hits","sl_hits","time_exits"])

    md += f"""

## Files
- `target_only.csv`, `target_with_stop.csv` — per-day logs (all distances stacked)
- `by_dte.csv` — the DTE-slab table in CSV form
- `equity_curves.png` — gross (dashed) vs net (solid) for each distance, both variants

## Caveats
- Entry price = close of the first 1-min bar ≥ 10:00 IST (execution proxy).
- PT/SL triggers within a minute use intrabar high/low; when both could fire we assume the **stop** trips first (conservative for variant B).
- Spot proxied from NIFTY futures.
- Friction is a flat ₹{FRICTION_PER_LEG:.0f}/leg; real cost scales with broker, size, liquidity of the far-OTM strike.
"""
    (OUT / "summary.md").write_text(md)
    print(f"\n✓ Done. Results in: {OUT}")


if __name__ == "__main__":
    main()
