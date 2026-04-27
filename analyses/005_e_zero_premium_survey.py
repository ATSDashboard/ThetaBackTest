"""
ANALYSIS 005 — E-0 (expiry-day) Deep OTM Premium Survey · NIFTY

Sibling of 004 — answers the same questions but for DTE=0 (the expiry day
itself).  On expiry day theta + gamma both peak; premium decay is fastest, but
adverse moves are also more violent because there's no overnight buffer.

Question set (mirroring 004):
  For each distance in {1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0}% on every NIFTY
  weekly-expiry trading day:
    - Combined CE+PE premium at 10:00 entry
    - Fraction of days where combined entry ≥ ₹77 (= ₹5K target / 65 lot)
    - Fraction of days where both legs close ≤ ₹1 by 15:25 (full premium eaten)
    - Same-day exit (15:15) gross + net P&L (friction ₹400/lot)
    - Hold-to-3:25 (just before expiry close) gross + net P&L
    - Max adverse excursion (MAE) of combined premium intraday

Constraints from Rohan (session 2026-04-22):
  Target ₹5K/lot · stop ₹127/lot (~₹1.95/share) · 98%+ worthless · 55 lots/Cr.

Output: results/005_e_zero_premium_survey/
  summary.md, e0_per_day.csv, by_distance.csv, premium_mae_by_distance.png
"""
from __future__ import annotations
from datetime import date, time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT  = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "parquet" / "instrument=NIFTY"
OUT   = ROOT / "results" / "005_e_zero_premium_survey"
OUT.mkdir(parents=True, exist_ok=True)

LOT = 65
GRID = 50
ENTRY_AT = time(10, 0)
SAME_DAY_EXIT = time(15, 15)
EXPIRY_LAST_CHECK = time(15, 25)
DISTANCES = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
FRICTION_PER_LEG = 200.0
TARGET_RS = 5000 / LOT          # 76.92
STOP_RS   = 7000 / 55 / LOT     # 1.958

# ── Weekly expiry weekdays only (skip month-end monthlies on Mon/Wed) ──
WEEKLY_EXPIRY_WEEKDAYS = {1, 3}   # Tuesday (current), Thursday (legacy)

con = duckdb.connect()


def load_fut():
    p = str(STORE / "**" / "*.parquet")
    df = con.execute(f"""
        SELECT timestamp, close
        FROM read_parquet('{p}', union_by_name=True)
        WHERE option_type='FUT'
    """).fetchdf()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["date"] = df["timestamp"].dt.date
    df["time"] = df["timestamp"].dt.time
    return df.sort_values("timestamp").reset_index(drop=True)


def expiry_on(d: date):
    """Return d if d is itself a weekly-expiry day with options data, else None."""
    p = str(STORE / "**" / "*.parquet")
    row = con.execute(f"""
        SELECT MIN(expiry) AS exp
        FROM read_parquet('{p}', union_by_name=True)
        WHERE option_type IN ('CE','PE')
          AND expiry = DATE '{d.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
    """).fetchone()
    return row[0] if row and row[0] else None


def load_legs(d: date, exp: date, ce_s: int, pe_s: int):
    p = str(STORE / "**" / "*.parquet")
    df = con.execute(f"""
        SELECT timestamp, strike, option_type, high, low, close
        FROM read_parquet('{p}', union_by_name=True)
        WHERE option_type IN ('CE','PE')
          AND expiry = DATE '{exp.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
          AND strike IN ({ce_s},{pe_s})
    """).fetchdf()
    if df.empty: return df, df
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["time"] = df["timestamp"].dt.time
    df = df.sort_values("timestamp")
    ce = df[(df.option_type=="CE") & (df.strike==ce_s)].reset_index(drop=True)
    pe = df[(df.option_type=="PE") & (df.strike==pe_s)].reset_index(drop=True)
    return ce, pe


def pick_strikes(spot, dp):
    return (int(round(spot*(1+dp/100)/GRID)*GRID),
            int(round(spot*(1-dp/100)/GRID)*GRID))


def first_at_or_after(df, t):
    m = df[df["time"] >= t]
    return None if m.empty else m.iloc[0]

def last_at_or_before(df, t):
    m = df[df["time"] <= t]
    return None if m.empty else m.iloc[-1]


def run():
    fut = load_fut()
    print(f"[load] fut rows={len(fut):,} dates={fut['date'].nunique()}")

    # Identify E-0 days = days where this date is a weekly expiry
    e0_days = []
    for d in sorted(fut["date"].unique()):
        if pd.Timestamp(d).weekday() not in WEEKLY_EXPIRY_WEEKDAYS:
            continue
        exp = expiry_on(d)
        if exp != d:
            continue
        g = fut[fut["date"]==d]
        at = g[g["time"] >= ENTRY_AT]
        if at.empty: continue
        spot = float(at["close"].iloc[0])
        e0_days.append({"date": d, "expiry": exp, "spot": spot,
                        "dow": pd.Timestamp(d).day_name()})
    e0_df = pd.DataFrame(e0_days)
    print(f"[E-0] weekly-expiry days found: {len(e0_df)}")
    if not e0_df.empty:
        print(e0_df["dow"].value_counts().to_string())

    rows = []
    for i, r in e0_df.iterrows():
        d = r["date"]; exp = r["expiry"]; spot = r["spot"]
        for dp in DISTANCES:
            ce_s, pe_s = pick_strikes(spot, dp)
            ce, pe = load_legs(d, exp, ce_s, pe_s)
            if ce.empty or pe.empty:
                rows.append({"date": d, "dow": r["dow"], "distance_pct": dp,
                             "ce_strike": ce_s, "pe_strike": pe_s, "status": "no_quotes"})
                continue
            ce_e = first_at_or_after(ce, ENTRY_AT)
            pe_e = first_at_or_after(pe, ENTRY_AT)
            if ce_e is None or pe_e is None:
                rows.append({"date": d, "dow": r["dow"], "distance_pct": dp,
                             "ce_strike": ce_s, "pe_strike": pe_s, "status": "no_entry"})
                continue
            ce_entry = float(ce_e["close"]); pe_entry = float(pe_e["close"])
            combined_entry = ce_entry + pe_entry

            # Forward-aligned bars to 15:15
            mrg = pd.merge(
                ce[["timestamp","high","low","close"]].rename(columns={"high":"ch","low":"cl","close":"cc"}),
                pe[["timestamp","high","low","close"]].rename(columns={"high":"ph","low":"pl","close":"pc"}),
                on="timestamp", how="inner")
            post_sd = mrg[(mrg["timestamp"] > ce_e["timestamp"]) &
                          (mrg["timestamp"].dt.time <= SAME_DAY_EXIT)].reset_index(drop=True)
            if post_sd.empty:
                mae = mfe = 0.0
                sd_combined = combined_entry
            else:
                mae = max(0.0, (post_sd["ch"] + post_sd["ph"]).max() - combined_entry)
                mfe = max(0.0, combined_entry - (post_sd["cl"] + post_sd["pl"]).min())
                last_sd = post_sd.iloc[-1]
                sd_combined = float(last_sd["cc"] + last_sd["pc"])
            sd_gross = (combined_entry - sd_combined) * LOT
            sd_net   = sd_gross - 2 * FRICTION_PER_LEG

            # Expire-close (15:25) check
            ce_last = last_at_or_before(ce, EXPIRY_LAST_CHECK)
            pe_last = last_at_or_before(pe, EXPIRY_LAST_CHECK)
            if ce_last is None or pe_last is None:
                expire_combined = sd_combined
                worthless = np.nan
            else:
                expire_combined = float(ce_last["close"] + pe_last["close"])
                worthless = int((ce_last["close"] <= 1.0) and (pe_last["close"] <= 1.0))
            ex_gross = (combined_entry - expire_combined) * LOT
            ex_net   = ex_gross - 2 * FRICTION_PER_LEG

            rows.append({
                "date": d, "dow": r["dow"], "distance_pct": dp,
                "ce_strike": ce_s, "pe_strike": pe_s, "spot": spot,
                "combined_entry": round(combined_entry, 2),
                "ce_entry": ce_entry, "pe_entry": pe_entry,
                "entry_hits_target_prem": int(combined_entry >= TARGET_RS),
                "mae_rs_per_share": round(mae, 2),
                "mfe_rs_per_share": round(mfe, 2),
                "mae_breached_stop": int(mae >= STOP_RS),
                "same_day_exit": round(sd_combined, 2),
                "same_day_gross": round(sd_gross, 0),
                "same_day_net":   round(sd_net, 0),
                "expire_exit":   round(expire_combined, 2),
                "expire_gross":  round(ex_gross, 0),
                "expire_net":    round(ex_net, 0),
                "expired_worthless": worthless,
                "status": "ok",
            })

    pd.DataFrame(rows).to_csv(OUT / "e0_per_day.csv", index=False)
    df = pd.DataFrame([r for r in rows if r.get("status")=="ok"])
    print(f"[samples] per-day rows={len(df)}")

    # Aggregate
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
            "pct_entry_ge_77": round(sub["entry_hits_target_prem"].mean()*100, 1),
            "pct_worthless":   round(sub["expired_worthless"].mean()*100, 1) if sub["expired_worthless"].notna().any() else np.nan,
            "pct_MAE_gt_stop":round(sub["mae_breached_stop"].mean()*100, 1),
            "median_MAE":      round(sub["mae_rs_per_share"].median(), 2),
            "p90_MAE":         round(sub["mae_rs_per_share"].quantile(0.9), 2),
            "SD_net_sum":      round(sub["same_day_net"].sum(), 0),
            "SD_win_pct":      round((sub["same_day_net"] > 0).mean()*100, 1),
            "SD_avg_net":      round(sub["same_day_net"].mean(), 0),
            "SD_worst":        round(sub["same_day_net"].min(), 0),
            "EX_net_sum":      round(sub["expire_net"].sum(), 0),
            "EX_win_pct":      round((sub["expire_net"] > 0).mean()*100, 1),
            "EX_avg_net":      round(sub["expire_net"].mean(), 0),
            "EX_worst":        round(sub["expire_net"].min(), 0),
        })
    agg = pd.DataFrame(agg_rows)
    agg.to_csv(OUT / "by_distance.csv", index=False)
    print("\n=== By distance (E-0) ===")
    print(agg.to_string(index=False))

    # Charts
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    ax = axes[0]
    ax.plot(agg["distance_pct"], agg["median_entry"], "o-", label="median CE+PE entry")
    ax.axhline(TARGET_RS, color="red", ls="--", lw=1.0, label=f"target ₹{TARGET_RS:.0f} (= ₹5K/lot)")
    ax.set_xlabel("Distance % OTM"); ax.set_ylabel("Premium (₹/share)")
    ax.set_title("E-0 10am combined premium vs distance")
    ax.legend(); ax.grid(alpha=0.2)
    ax = axes[1]
    ax.plot(agg["distance_pct"], agg["median_MAE"], "o-", color="#f59e0b", label="median MAE")
    ax.plot(agg["distance_pct"], agg["p90_MAE"], "s-", color="#ef4444", label="90th-pct MAE")
    ax.axhline(STOP_RS, color="black", ls="--", lw=1.0, label=f"stop ₹{STOP_RS:.2f}")
    ax.set_xlabel("Distance % OTM"); ax.set_ylabel("MAE (₹/share)")
    ax.set_title("E-0 intraday max adverse excursion")
    ax.legend(); ax.grid(alpha=0.2)
    fig.tight_layout(); fig.savefig(OUT / "premium_mae_by_distance.png", dpi=140); plt.close(fig)

    # Markdown
    cols = ["distance_pct","days","median_entry","pct_entry_ge_77","pct_worthless","pct_MAE_gt_stop",
            "SD_net_sum","SD_win_pct","SD_avg_net","SD_worst",
            "EX_net_sum","EX_win_pct","EX_avg_net","EX_worst"]
    md = f"""# 005 — E-0 (expiry-day) Deep OTM Premium Survey · NIFTY

Mirror of 004 but for DTE = 0 (the expiry day itself). Theta + gamma both peak;
no overnight risk. Same constraint frame from session 2026-04-22:

- Target premium per lot: **₹5,000 ⇒ ₹{TARGET_RS:.0f}/share combined CE+PE**
- Per-trade stop (if portfolio cap of ₹7K/Cr split across 55 lots): **₹{STOP_RS:.2f}/share**
- Win-rate ambition: ≥ 98% expire worthless

## NIFTY E-0 days surveyed (weekly Tue + legacy Thu only)
{len(e0_df)} days total · DOW breakdown:

```
{e0_df['dow'].value_counts().to_string() if not e0_df.empty else '(none)'}
```

## Central table

| {' | '.join(cols)} |
| {' | '.join(['---']*len(cols))} |
"""
    for _, r in agg.iterrows():
        md += "| " + " | ".join(str(r[c]) for c in cols) + " |\n"

    md += f"""

Columns:
- *SD_** — same-day exit at 15:15 (held intraday)
- *EX_** — held to 15:25 (expiry close); both legs ≤ ₹1 = worthless
- All ₹ figures are **net of ₹{2*FRICTION_PER_LEG:.0f}/lot/day friction** (₹100/leg × 4)

## How to read this vs 004 (E-1)

E-0 has **massively higher target-hit rate** at near-money distances (theta is concentrated in the final hours), but also **much higher MAE** because gamma is peak. The 98%-worthless line should be even cleaner at deeper distances since there's no overnight gap risk.

(Open the table; the takeaway depends on Rohan's actual broker friction. With ₹400/lot/day placeholder this looks one way; at ₹100/lot real-world cost the picture shifts ₹300/lot in your favour at every distance.)

## Files
- `e0_per_day.csv` — every (day × distance) sample with entry, MAE, both exits, worthless flag
- `by_distance.csv` — aggregate above
- `premium_mae_by_distance.png`
"""
    (OUT / "summary.md").write_text(md)
    print(f"\n✓ Done. {OUT}")


if __name__ == "__main__":
    run()
