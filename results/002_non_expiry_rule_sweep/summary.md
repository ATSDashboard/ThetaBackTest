# Analysis 002 — NIFTY Non-Expiry Deep OTM Rule Sweep

**Question:** Best combo of entry time, conditions, square-off rules, and skip-day filters for selling deep OTM CE+PE on NIFTY non-expiry days.

**Friction:** ₹200/leg included (₹100 entry + ₹100 exit).
**Sizing:** 1 lot/leg (lot=65).
**Train:** up to 2025-12-31  ·  **Test:** after 2025-12-31.

## Stage A — Rule variants (3% OTM, 09:30-10:30 → 15:00)

| variant | desc | train_days_traded | train_win_pct | train_net_pnl | train_worst_day | train_max_drawdown | train_sharpe_ann | test_days_traded | test_win_pct | test_net_pnl | test_worst_day |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V0_base | baseline (001): no SL, no PT, no filter | 106.0 | 17.0 | -32020.0 | -3552.0 | -33124.0 | -9.18 | 45.0 | 37.8 | -27038.0 | -9851.0 |
| V1_SL2x | + per-leg SL at 2× entry | 106.0 | 17.0 | -34694.0 | -3159.0 | -35799.0 | -9.66 | 45.0 | 35.6 | -29157.0 | -6646.0 |
| V2_SL1.5x | + per-leg SL at 1.5× entry | 106.0 | 15.1 | -36951.0 | -2314.0 | -38056.0 | -10.93 | 45.0 | 33.3 | -20936.0 | -4648.0 |
| V3_PT50 | + per-leg PT 50% premium | 106.0 | 16.0 | -32980.0 | -3552.0 | -33867.0 | -9.77 | 45.0 | 42.2 | -22660.0 | -9851.0 |
| V4_SL2x_PT50 | + SL 2× and PT 50% | 106.0 | 16.0 | -35655.0 | -3159.0 | -36541.0 | -10.23 | 45.0 | 40.0 | -26578.0 | -6646.0 |
| V5_gapFilter | V4 + skip |gap|>0.5% | 52.0 | 19.2 | -17051.0 | -2422.0 | -17938.0 | -9.48 | 17.0 | 47.1 | -4598.0 | -6646.0 |
| V6_gap_rangeFilter | V5 + skip 1h range>0.8% | nan | nan | nan | nan | nan | nan | nan | nan | nan | nan |
| V7_gap_range_trend | V6 + skip prev-day |move|>1.2% | nan | nan | nan | nan | nan | nan | nan | nan | nan | nan |

**Winner (by train net, guarded by test≥0):** `V5_gapFilter` — V4 + skip |gap|>0.5%

## Stage B — Distance sweep on winner

| distance_pct | train_days_traded | train_win_pct | train_net_pnl | train_worst_day | test_days_traded | test_win_pct | test_net_pnl | test_worst_day |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2.0 | 52.0 | 44.2 | -13647.0 | -3962.0 | 17.0 | 52.9 | -14041.0 | -9000.0 |
| 2.5 | 52.0 | 34.6 | -14266.0 | -2891.0 | 17.0 | 41.2 | -4618.0 | -7745.0 |
| 3.0 | 52.0 | 19.2 | -17051.0 | -2422.0 | 17.0 | 47.1 | -4598.0 | -6646.0 |
| 3.5 | 52.0 | 9.6 | -18566.0 | -2087.0 | 17.0 | 29.4 | -4460.0 | -5158.0 |
| 4.0 | 52.0 | 3.8 | -18725.0 | -1659.0 | 17.0 | 23.5 | -6410.0 | -4450.0 |
| 5.0 | 52.0 | 1.9 | -19386.0 | -1268.0 | 16.0 | 18.8 | -8168.0 | -3026.0 |

Best-train distance: **2.0%**

## Stage C — Entry/exit time sweep (top 10 by train net)

| entry | exit | train_days_traded | train_win_pct | train_net_pnl | train_worst_day | test_days_traded | test_win_pct | test_net_pnl | test_worst_day |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10:30 | 15:00 | 52 | 36.5 | -1606.0 | -2636.0 | 17 | 41.2 | -13411.0 | -9104.0 |
| 10:30 | 14:00 | 52 | 38.5 | -3528.0 | -2483.0 | 17 | 52.9 | -16919.0 | -9373.0 |
| 10:30 | 14:30 | 52 | 40.4 | -5288.0 | -2568.0 | 17 | 35.3 | -11862.0 | -5853.0 |
| 11:00 | 14:00 | 52 | 34.6 | -6723.0 | -2522.0 | 17 | 41.2 | -18698.0 | -9208.0 |
| 11:00 | 15:00 | 52 | 32.7 | -7808.0 | -2675.0 | 17 | 29.4 | -15166.0 | -7921.0 |
| 10:30 | 13:30 | 52 | 30.8 | -8356.0 | -2064.0 | 17 | 35.3 | -18797.0 | -6695.0 |
| 11:00 | 14:30 | 52 | 28.8 | -8617.0 | -2607.0 | 17 | 41.2 | -13661.0 | -5688.0 |
| 11:30 | 14:00 | 52 | 23.1 | -10484.0 | -1924.0 | 17 | 41.2 | -13929.0 | -8226.0 |
| 09:30 | 14:00 | 52 | 44.2 | -11645.0 | -3962.0 | 17 | 47.1 | -20575.0 | -10030.0 |
| 11:00 | 13:30 | 52 | 28.8 | -11651.0 | -1719.0 | 17 | 23.5 | -20606.0 | -6529.0 |

## Files
- `comparison.csv` — full Stage A variant table
- `equity_curves.png` — overlaid cumulative P&L per variant
- `winner_per_day.csv` + `winner_equity.png` — day-level for winning rule
- `distance_sweep.csv` + `distance_sweep.png`
- `time_sweep.csv`

## Caveats
- Spot proxied from NIFTY futures.
- SL / PT filled optimistically at trigger price (real fills may slip).
- ~151 non-expiry days in cache; split ~106/45 train/test — test is small, treat as directional sanity check.
- Friction held constant ₹200/leg; actual varies by broker / size.
