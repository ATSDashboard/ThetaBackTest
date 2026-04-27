# 005 — E-0 (expiry-day) Deep OTM Premium Survey · NIFTY

Mirror of 004 but for DTE = 0 (the expiry day itself). Theta + gamma both peak;
no overnight risk. Same constraint frame from session 2026-04-22:

- Target premium per lot: **₹5,000 ⇒ ₹77/share combined CE+PE**
- Per-trade stop (if portfolio cap of ₹7K/Cr split across 55 lots): **₹1.96/share**
- Win-rate ambition: ≥ 98% expire worthless

## NIFTY E-0 days surveyed (weekly Tue + legacy Thu only)
48 days total · DOW breakdown:

```
dow
Tuesday     29
Thursday    19
```

## Central table

| distance_pct | days | median_entry | pct_entry_ge_77 | pct_worthless | pct_MAE_gt_stop | SD_net_sum | SD_win_pct | SD_avg_net | SD_worst | EX_net_sum | EX_win_pct | EX_avg_net | EX_worst |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1.0 | 48.0 | 32.4 | 33.3 | 50.0 | 89.6 | 10838.0 | 68.8 | 226.0 | -9594.0 | 10148.0 | 68.8 | 211.0 | -9926.0 |
| 1.5 | 48.0 | 9.5 | 10.4 | 87.5 | 68.8 | 28796.0 | 56.2 | 600.0 | -6201.0 | 28372.0 | 58.3 | 591.0 | -6585.0 |
| 2.0 | 48.0 | 4.35 | 0.0 | 95.8 | 39.6 | 6196.0 | 37.5 | 129.0 | -829.0 | 6340.0 | 37.5 | 132.0 | -1134.0 |
| 2.5 | 48.0 | 3.02 | 0.0 | 100.0 | 20.8 | -6709.0 | 10.4 | -140.0 | -322.0 | -6586.0 | 12.5 | -137.0 | -322.0 |
| 3.0 | 48.0 | 2.1 | 0.0 | 100.0 | 6.2 | -11466.0 | 4.2 | -239.0 | -342.0 | -11316.0 | 4.2 | -236.0 | -338.0 |
| 4.0 | 48.0 | 1.67 | 0.0 | 100.0 | 2.1 | -14157.0 | 0.0 | -295.0 | -358.0 | -14048.0 | 0.0 | -293.0 | -358.0 |
| 5.0 | 48.0 | 1.3 | 0.0 | 100.0 | 2.1 | -15352.0 | 0.0 | -320.0 | -371.0 | -15269.0 | 0.0 | -318.0 | -368.0 |


Columns:
- *SD_** — same-day exit at 15:15 (held intraday)
- *EX_** — held to 15:25 (expiry close); both legs ≤ ₹1 = worthless
- All ₹ figures are **net of ₹400/lot/day friction** (₹100/leg × 4)

## How to read this vs 004 (E-1)

E-0 has **massively higher target-hit rate** at near-money distances (theta is concentrated in the final hours), but also **much higher MAE** because gamma is peak. The 98%-worthless line should be even cleaner at deeper distances since there's no overnight gap risk.

(Open the table; the takeaway depends on Rohan's actual broker friction. With ₹400/lot/day placeholder this looks one way; at ₹100/lot real-world cost the picture shifts ₹300/lot in your favour at every distance.)

## Files
- `e0_per_day.csv` — every (day × distance) sample with entry, MAE, both exits, worthless flag
- `by_distance.csv` — aggregate above
- `premium_mae_by_distance.png`
