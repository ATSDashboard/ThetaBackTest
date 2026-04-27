# 004 — E-1 Deep OTM Premium Survey (NIFTY)

## Your constraints (corrected)
- **Margin per lot:** ₹1.65L–₹1.8L non-expiry → **55 lots per ₹1Cr** at ₹1.8L
- **Entry premium target per lot:** ₹5,000 (range ₹4K–₹7K "very deep OTM") = **₹77/share combined CE+PE** (range ₹62–₹108)
- **"Eat the premium" ≥ 98%** (strikes expire worthless)
- **Max daily loss ₹7K/Cr** = **₹127 per lot** = **₹1.95/share adverse** — this is the HARD stop-loss cap if honored per-trade
- **Lot sizes:** NIFTY 65, SENSEX 20

## SENSEX — data gap
SENSEX parquet has only **8 days** (2026-02-27 → 2026-04-16). **Not enough** to backtest anything. Need weekly-expiry minute bars back to ~2024-04. Flag to whoever runs ingest; once present, change `STORE` path + `LOT=20, GRID=100` and re-run.

## NIFTY E-1 days in store
46 days of DTE=1: **28 Mon (→Tue expiry), 17 Wed (→Thu legacy), 1 Tue (month-end).**

## Central table — by distance, entry at 10:00

| Dist % | days | median entry ₹ | % entry ≥ ₹77 | % expire worthless | % MAE > ₹1.95 stop | no-stop SD net Σ | no-stop ON net Σ | ON avg/lot | ON worst/lot |
|---|---|---|---|---|---|---|---|---|---|
| **1.0** | 46 | 74.47 | 47.8% | 37.0% | 91.3% | ₹35,925 | ₹58,884 | ₹1,280 | **−₹15,555** |
| **1.5** | 46 | 30.58 | 17.4% | 76.1% | 87.0% | ₹22,952 | ₹74,048 | ₹1,610 | −₹10,267 |
| **2.0** | 46 | 13.32 | 10.9% | 95.7% | 67.4% | ₹8,663 | ₹62,128 | ₹1,351 | −₹3,081 |
| **2.5** | 46 | 7.48 | 6.5% | **100.0%** | 52.2% | −₹1,219 | ₹38,490 | ₹837 | −₹221 |
| **3.0** | 46 | 4.85 | 4.3% | **100.0%** | 34.8% | −₹6,857 | ₹21,058 | ₹458 | −₹254 |
| **4.0** | 46 | 3.32 | 0.0% | **100.0%** | 19.6% | −₹12,132 | ₹4,066 | ₹88 | −₹312 |
| **5.0** | 46 | 2.42 | 0.0% | **100.0%** | 13.0% | −₹14,672 | −₹3,664 | −₹80 | −₹328 |

*SD = same-day 10:00→15:15. ON = hold to expiry close 15:25 next day. Both net of ₹400/lot round-trip friction.*

## The uncomfortable truth

Your three goals are **incompatible at a single distance** on NIFTY E-1:

| Goal | Distances that satisfy it |
|---|---|
| Entry ≥ ₹77 (₹5K/lot target) | **1.0% only** — and only 48% of days |
| 98%+ expire worthless | **≥ 2.5% only** |
| MAE < ₹1.95 / day in ≥ 95% of days | **none** — even 5% OTM breaches 13% of days |

**The ₹1.95/share stop is too tight to be a per-trade stop** — NIFTY intraday noise alone breaches it. If you enforce it literally, every stop-out day loses ₹527/lot (stop + friction), and every distance flips net-negative (see `by_distance_corrected_stop.csv`).

## Four viable options

### Option A — 2.5% OTM, hold overnight, aggregate (not per-trade) stop
- Sell CE+PE 2.5% OTM at 10:00 E-1, square off at expiry 3:25 PM.
- Per-lot: avg **+₹837 net**, worst **−₹221**, **100% worthless**.
- Scaled to 55 lots/Cr: **avg ≈ ₹46K/Cr per E-1 event**; worst day **≈ ₹12K loss/Cr** (breaches the ₹7K cap ~3× in the 46-day sample).
- Trade-off: gives up the hard cap on a few tail days but otherwise hits the 98% profile.

### Option B — 3% OTM, hold overnight, aggregate stop
- Same as A but 3% OTM.
- Per-lot: avg **+₹458**, worst **−₹254**, 100% worthless.
- Scaled 55 lots/Cr: avg ₹25K/Cr/event, worst ≈ ₹14K/Cr.
- Lower income, still breaches cap on tail days. Slightly safer than A.

### Option C — Same-day 1% OTM scalping (NOT eat-the-premium)
- Sell CE+PE ~1% OTM at 10:00, exit 15:15 same day.
- **Only 37% expire worthless** — you're capturing intraday decay, not full expiry.
- Per-lot: avg +₹781 net, worst **−₹5,743**. Bigger income per lot but fails the 98% bar.
- Useful if you reconceive the strategy as "intraday income" rather than "eat the premium".

### Option D — Deeper + many lots, assuming lower friction
If your actual broker cost is ~₹20/leg (not ₹100 assumed here), friction drops from ₹400 to ₹80 per lot/day. Re-run estimate:
- 3% OTM ON: avg would rise from ₹458 → **₹778** net/lot → 55 lots × 46 days ≈ **₹19.7L/yr/Cr** (~20% annual).
- 4% OTM ON: avg flips from ₹88 → **₹408** net/lot → ~10% annual.
- **Tell me your real friction and I re-run — that one number changes the winning distance.**

## E-0 (expiry day itself) — not tested here
You specifically asked E-1. But the expiry day has maximum theta burn and might fit your profile better. Worth a follow-up survey (005) if interested.

## Files
- `e1_per_day.csv` — (46 × 7 distances) per-day samples (entry, MAE, same-day exit, overnight exit, worthless flag)
- `by_distance.csv` — original aggregate
- `by_distance_corrected_stop.csv` — with the ₹1.95/share stop enforced per-trade
- `premium_mae_by_distance.png`
