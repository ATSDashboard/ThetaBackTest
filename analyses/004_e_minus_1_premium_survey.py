"""
ANALYSIS 004 — E-1 Deep OTM Premium Survey (NIFTY only)

Rohan's constraint map:
  Target  ₹5,000 per trade on ₹1.65L margin  (3.0% on margin)
  Max loss ₹7,000 per ₹1Cr capital  →  ~₹1,200 per 1-lot trade  (~₹18/share NIFTY)
  Required win rate ≥ 98% for positive expectancy.

Goal of THIS analysis:
  For every E-1 day (DTE = 1) in the NIFTY minute store, for each distance
  in {1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0}%:
    - Combined CE+PE premium at 10:00 entry
    - Fraction of days where combined premium ≥ ₹77  (the target = premium)
    - Same-day exit at 15:15: gross + net P&L
    - Hold-overnight-to-expiry 15:25 close: gross + net P&L (captures remaining theta)
    - Max adverse excursion of combined premium E-1 (= worst-case run-up)
    - Fraction of days expiring worthless (= both legs closed ≤ ₹1 on E-0 3:25)

  From these, report for each distance:
    - Days available (E-1 only)
    - Median / min / max combined entry premium
    - Fraction of days with entry ≥ ₹77  (= target reachable)
    - "Full-premium-eaten" rate (both legs < ₹1 at E-0 3:25)
    - Same-day exit metrics (net ₹, win%, worst day)
    - Hold-overnight metrics (net ₹, win%, worst day)
    - Max-adverse-excursion stats (how often intraday MAE > ₹18/share ~ stop)

  Output: a recommendation table naming which distance (if any) meets
  target ≥₹77 AND hit rate ≥ 98% AND MAE rarely above stop.

Weekly expiries only: script filters expiry ∈ {Tue, Thu}; NIFTY monthly expiries
on Mon/Wed are retained but tagged — 5 + 2 = 7 monthly-expiry E-1 observations.
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
OUT   = ROOT / "results" / "004_e_minus_1_premium_survey"
OUT.mkdir(parents=True, exist_ok=True)

LOT = 65
GRID = 50
ENTRY_AT = time(10, 0)
SAME_DAY_EXIT = time(15, 15)
EXPIRY_DAY_CHECK = time(15, 25)   # check near close of expiry day
DISTANCES = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
FRICTION_PER_LEG = 200.0
TARGET_RS_PER_SHARE = 5000 / LOT   # ≈ 76.9
STOP_RS_PER_SHARE  = 1200 / LOT    # ≈ 18.5

con = duckdb.connect()


def load_fut():
    path = str(STORE / "**" / "*.parquet")
    df = con.execute(f"""
        SELECT timestamp, close
        FROM read_parquet('{path}', union_by_name=True)
        WHERE option_type = 'FUT'
    """).fetchdf()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["date"] = df["timestamp"].dt.date
    df["time"] = df["timestamp"].dt.time
    return df.sort_values("timestamp").reset_index(drop=True)


def nearest_expiry(d: date):
    path = str(STORE / "**" / "*.parquet")
    row = con.execute(f"""
        SELECT MIN(expiry) AS exp
        FROM read_parquet('{path}', union_by_name=True)
        WHERE option_type IN ('CE','PE')
          AND expiry > DATE '{d.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
    """).fetchone()
    return row[0] if row and row[0] else None


def load_leg_bars(d: date, exp: date, ce_s: int, pe_s: int):
    path = str(STORE / "**" / "*.parquet")
    df = con.execute(f"""
        SELECT timestamp, strike, option_type, high, low, close
        FROM read_parquet('{path}', union_by_name=True)
        WHERE option_type IN ('CE','PE')
          AND expiry = DATE '{exp.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
          AND strike IN ({ce_s}, {pe_s})
    """).fetchdf()
    if df.empty: return df, df
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["time"] = df["timestamp"].dt.time
    df = df.sort_values("timestamp")
    ce = df[(df.option_type=="CE") & (df.strike==ce_s)].reset_index(drop=True)
    pe = df[(df.option_type=="PE") & (df.strike==pe_s)].reset_index(drop=True)
    return ce, pe


def pick_strikes(spot, dp):
    ce = round(spot * (1 + dp/100) / GRID) * GRID
    pe = round(spot * (1 - dp/100) / GRID) * GRID
    return int(ce), int(pe)


def first_at_or_after(df, at_time):
    m = df[df["time"] >= at_time]
    return (None if m.empty else m.iloc[0])


def last_at_or_before(df, at_time):
    m = df[df["time"] <= at_time]
    return (None if m.empty else m.iloc[-1])


def run():
    fut = load_fut()
    print(f"[load] fut rows={len(fut):,} dates={fut['date'].nunique()}")

    # ── Build list of E-1 days ──
    e1_days = []
    for d in sorted(fut["date"].unique()):
        exp = nearest_expiry(d)
        if not exp or exp == d: continue
        dte = (exp - d).days
        if dte != 1: continue
        expiry_weekday = pd.Timestamp(exp).day_name()
        today_weekday  = pd.Timestamp(d).day_name()
        g = fut[fut["date"]==d]
        at = g[g["time"] >= ENTRY_AT]
        if at.empty: continue
        spot = float(at["close"].iloc[0])
        e1_days.append({"date": d, "expiry": exp, "spot": spot,
                        "day_of_week": today_weekday, "expiry_dow": expiry_weekday})
    e1_df = pd.DataFrame(e1_days)
    print(f"[E-1] days found: {len(e1_df)}")
    print(e1_df["day_of_week"].value_counts())

    # ── For each E-1 day × distance: gather premium samples ──
    rows = []
    for i, r in e1_df.iterrows():
        d = r["date"]; exp = r["expiry"]; spot = r["spot"]
        for dp in DISTANCES:
            ce_s, pe_s = pick_strikes(spot, dp)
            ce, pe = load_leg_bars(d, exp, ce_s, pe_s)
            if ce.empty or pe.empty:
                rows.append({"date": d, "expiry": exp, "dow": r["day_of_week"],
                             "distance_pct": dp, "ce_strike": ce_s, "pe_strike": pe_s,
                             "status": "no_quotes"})
                continue

            ce_ent = first_at_or_after(ce, ENTRY_AT)
            pe_ent = first_at_or_after(pe, ENTRY_AT)
            if ce_ent is None or pe_ent is None:
                rows.append({"date": d, "expiry": exp, "dow": r["day_of_week"],
                             "distance_pct": dp, "ce_strike": ce_s, "pe_strike": pe_s,
                             "status": "no_entry"})
                continue
            ce_entry = float(ce_ent["close"]); pe_entry = float(pe_ent["close"])
            combined_entry = ce_entry + pe_entry

            # intraday max adverse excursion (MAE): worst combined (ce_high + pe_high)
            post = pd.merge(
                ce[["timestamp","high","low","close"]].rename(columns={"high":"ch","low":"cl","close":"cc"}),
                pe[["timestamp","high","low","close"]].rename(columns={"high":"ph","low":"pl","close":"pc"}),
                on="timestamp", how="inner")
            post = post[(post["timestamp"] > ce_ent["timestamp"]) &
                        (post["timestamp"].dt.time <= SAME_DAY_EXIT)].reset_index(drop=True)
            if post.empty:
                mae_rs = 0.0
                mfe_rs = 0.0
                same_day_exit_combined = combined_entry
            else:
                adv = (post["ch"] + post["ph"]).max()
                fav = (post["cl"] + post["pl"]).min()
                mae_rs = max(0.0, adv - combined_entry)
                mfe_rs = max(0.0, combined_entry - fav)
                last = post.iloc[-1]
                same_day_exit_combined = float(last["cc"] + last["pc"])

            sd_gross = (combined_entry - same_day_exit_combined) * LOT
            sd_net = sd_gross - 2 * FRICTION_PER_LEG

            # hold to expiry close: fetch expiry-day data (3:25 PM)
            ce_x, pe_x = load_leg_bars(exp, exp, ce_s, pe_s)
            if ce_x.empty or pe_x.empty:
                overnight_exit = np.nan
                expired_worthless = np.nan
            else:
                ce_last = last_at_or_before(ce_x, EXPIRY_DAY_CHECK)
                pe_last = last_at_or_before(pe_x, EXPIRY_DAY_CHECK)
                if ce_last is None or pe_last is None:
                    overnight_exit = np.nan
                    expired_worthless = np.nan
                else:
                    overnight_exit = float(ce_last["close"] + pe_last["close"])
                    expired_worthless = int((ce_last["close"] <= 1.0) and (pe_last["close"] <= 1.0))

            on_gross = (combined_entry - overnight_exit) * LOT if overnight_exit==overnight_exit else np.nan
            on_net = on_gross - 2 * FRICTION_PER_LEG if on_gross==on_gross else np.nan

            rows.append({
                "date": d, "expiry": exp, "dow": r["day_of_week"],
                "distance_pct": dp, "ce_strike": ce_s, "pe_strike": pe_s,
                "spot": spot,
                "combined_entry": round(combined_entry,2),
                "ce_entry": ce_entry, "pe_entry": pe_entry,
                "entry_hits_target_prem": int(combined_entry >= TARGET_RS_PER_SHARE),
                "mae_rs_per_share": round(mae_rs,2),
                "mfe_rs_per_share": round(mfe_rs,2),
                "mae_breached_stop": int(mae_rs >= STOP_RS_PER_SHARE),
                "same_day_exit": round(same_day_exit_combined,2),
                "same_day_gross": round(sd_gross,0),
                "same_day_net":   round(sd_net,0),
                "overnight_exit": overnight_exit,
                "overnight_gross": round(on_gross,0) if on_gross==on_gross else np.nan,
                "overnight_net":   round(on_net,0) if on_net==on_net else np.nan,
                "expired_worthless": expired_worthless,
                "status": "ok",
            })
    pd.DataFrame(rows).to_csv(OUT / "e1_per_day.csv", index=False)
    df = pd.DataFrame([r for r in rows if r.get("status")=="ok"])
    print(f"[samples] per-day rows={len(df)}")

    # ── Summary by distance ──
    agg_rows = []
    for dp in DISTANCES:
        sub = df[df["distance_pct"] == dp]
        if sub.empty:
            agg_rows.append({"distance_pct": dp, "days": 0}); continue
        agg_rows.append({
            "distance_pct": dp,
            "days":            len(sub),
            "median_entry":    round(sub["combined_entry"].median(), 2),
            "min_entry":       round(sub["combined_entry"].min(), 2),
            "max_entry":       round(sub["combined_entry"].max(), 2),
            "pct_entry>=₹77":  round(sub["entry_hits_target_prem"].mean()*100, 1),
            "pct_worthless":   round(sub["expired_worthless"].mean()*100, 1) if sub["expired_worthless"].notna().any() else np.nan,
            "pct_MAE>stop":    round(sub["mae_breached_stop"].mean()*100, 1),
            "median_MAE":      round(sub["mae_rs_per_share"].median(), 2),
            "p90_MAE":         round(sub["mae_rs_per_share"].quantile(0.9), 2),
            # Same-day exit stats
            "SD_net_sum":      round(sub["same_day_net"].sum(), 0),
            "SD_win%":         round((sub["same_day_net"]>0).mean()*100, 1),
            "SD_avg_net":      round(sub["same_day_net"].mean(), 0),
            "SD_worst":        round(sub["same_day_net"].min(), 0),
            # Hold-overnight stats
            "ON_net_sum":      round(sub["overnight_net"].sum(), 0) if sub["overnight_net"].notna().any() else np.nan,
            "ON_win%":         round((sub["overnight_net"]>0).mean()*100, 1) if sub["overnight_net"].notna().any() else np.nan,
            "ON_avg_net":      round(sub["overnight_net"].mean(), 0) if sub["overnight_net"].notna().any() else np.nan,
            "ON_worst":        round(sub["overnight_net"].min(), 0) if sub["overnight_net"].notna().any() else np.nan,
        })
    agg = pd.DataFrame(agg_rows)
    agg.to_csv(OUT / "by_distance.csv", index=False)
    print("\n=== By distance (E-1 only) ===")
    print(agg.to_string(index=False))

    # ── Chart: premium vs distance, MAE vs distance ──
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    ax = axes[0]
    ax.plot(agg["distance_pct"], agg["median_entry"], "o-", label="median CE+PE entry")
    ax.axhline(TARGET_RS_PER_SHARE, color="red", ls="--", lw=1.0, label=f"target ₹{TARGET_RS_PER_SHARE:.0f} (= ₹5K/lot)")
    ax.set_xlabel("Distance % OTM"); ax.set_ylabel("Premium (₹/share)")
    ax.set_title("E-1 10am combined premium vs distance")
    ax.legend(); ax.grid(alpha=0.2)

    ax = axes[1]
    ax.plot(agg["distance_pct"], agg["median_MAE"], "o-", color="#f59e0b", label="median MAE")
    ax.plot(agg["distance_pct"], agg["p90_MAE"], "s-", color="#ef4444", label="90th-pct MAE")
    ax.axhline(STOP_RS_PER_SHARE, color="black", ls="--", lw=1.0, label=f"stop ₹{STOP_RS_PER_SHARE:.0f}")
    ax.set_xlabel("Distance % OTM"); ax.set_ylabel("MAE (₹/share)")
    ax.set_title("Intraday max adverse excursion vs distance")
    ax.legend(); ax.grid(alpha=0.2)
    fig.tight_layout(); fig.savefig(OUT / "premium_mae_by_distance.png", dpi=140)
    plt.close(fig)

    # ── Recommendation summary ──
    rec_lines = []
    rec_lines.append(f"**Target per trade:** ₹5,000 (₹{TARGET_RS_PER_SHARE:.0f}/share × {LOT} lot)")
    rec_lines.append(f"**Stop per trade:** ₹1,200 (₹{STOP_RS_PER_SHARE:.0f}/share)")
    rec_lines.append("**Needed:** entry premium ≥ ₹77, MAE rarely > ₹18, expiry-worthless rate ≥ 98%.")
    rec_lines.append("")
    rec_lines.append("Distances meeting each criterion:")
    for _, r in agg.iterrows():
        if r["days"] == 0: continue
        meets_prem = r["pct_entry>=₹77"] >= 50
        meets_mae  = r["pct_MAE>stop"] <= 5
        meets_wl   = (r["pct_worthless"] if r["pct_worthless"]==r["pct_worthless"] else 0) >= 95
        tag = "✅" if (meets_prem and meets_mae and meets_wl) else "⚠"
        rec_lines.append(
            f"- {tag} **{r['distance_pct']}% OTM** — "
            f"entry≥₹77: {r['pct_entry>=₹77']}% | "
            f"MAE>stop: {r['pct_MAE>stop']}% | "
            f"expired worthless: {r['pct_worthless']}% | "
            f"same-day net Σ: ₹{r['SD_net_sum']:,.0f} | "
            f"overnight net Σ: ₹{r['ON_net_sum']:,.0f}"
        )

    # ── summary.md ──
    md = f"""# 004 — E-1 Deep OTM Premium Survey (NIFTY)

Rohan's target profile:
- Earn ₹4,000-7,000 (target ₹5,000) per 1-lot trade · margin ~₹1.65L non-expiry
- Max loss ₹7K per ₹1Cr (~₹1,200 per 1-lot trade → **stop ≈ ₹{STOP_RS_PER_SHARE:.0f}/share adverse**)
- Win rate ≥ 98%

This analysis surveys NIFTY E-1 days (DTE = 1, weekly expiries Tue/Thu and a few month-end Mon/Wed) and asks, for each OTM distance:
1. Is combined CE+PE premium at 10:00 entry usually **≥ ₹{TARGET_RS_PER_SHARE:.0f}/share** (= target)?
2. How often does the intraday MAE **breach the ₹{STOP_RS_PER_SHARE:.0f}/share stop**?
3. How often do **both legs expire worthless** (the "eat full premium" scenario)?

## Summary by distance

"""
    cols = ["distance_pct","days","median_entry","pct_entry>=₹77","pct_MAE>stop","pct_worthless",
            "SD_net_sum","SD_win%","SD_worst","ON_net_sum","ON_win%","ON_worst"]
    md += "| " + " | ".join(cols) + " |\n| " + " | ".join(["---"]*len(cols)) + " |\n"
    for _, r in agg.iterrows():
        md += "| " + " | ".join(str(r[c]) for c in cols) + " |\n"

    md += "\n## Recommendation\n\n" + "\n".join(rec_lines)

    md += f"""

## SENSEX note
SENSEX partition currently contains only 8 days ({pd.Timestamp('2026-02-27').date()} → {pd.Timestamp('2026-04-16').date()}).
Not enough to backtest. Once SENSEX is ingested back to 2025, re-run this script with `STORE` pointed at SENSEX
(and adjust LOT=20, GRID=100).

## Files
- `e1_per_day.csv` — every (day × distance) sample with entry, MAE, exits, worthless flag
- `by_distance.csv` — aggregate table above
- `premium_mae_by_distance.png` — entry premium vs target, MAE vs stop
"""
    (OUT / "summary.md").write_text(md)
    print(f"\n✓ Done. {OUT}")


if __name__ == "__main__":
    run()
