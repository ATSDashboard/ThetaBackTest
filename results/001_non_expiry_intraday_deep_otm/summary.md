# NIFTY Non-Expiry Intraday Deep OTM — Results

**Rule:** Sell NIFTY CE + PE ~3.0% from spot · entry 09:30:00-10:30:00 IST · exit ≤ 15:00:00 IST · 1 lot each side · non-expiry days only · nearest weekly expiry.

**Period:** 2025-04-21 to 2026-04-17

## Headline
| Metric | Value |
|---|---:|
| Days traded | **151** |
| Days in profit | **98** (64.9%) |
| Days in loss | 52 |
| Breakeven / zero | 1 |
| **Net total P&L** | **₹-6,009** |
| Average per day | ₹-40 |
| Best day | ₹3,568 |
| Worst day | ₹-10,816 |

## Skipped days
99 days skipped. Reasons breakdown:
- `expiry_weekday`: 98
- `no_spot_proxy`: 1

## Files
- `per_day.csv` — every day's entry/exit/P&L
- `equity_curve.png` — cumulative chart

## Params used (edit script + re-run to try variants)
- DISTANCE_PCT = 3.0
- ENTRY_FROM = 09:30:00, ENTRY_TO = 10:30:00
- EXIT_AT = 15:00:00
- LOT_SIZE = 65 (per leg)
- Excluded weekdays (expiry days): [1, 3] (Mon=0..Sun=6)

## Caveats
- No brokerage / slippage modeled — subtract ~₹80-120/leg for real-world estimate
- Spot proxied from NIFTY futures (historical spot file not yet ingested)
- 1 lot per leg; P&L scales linearly with lots
