# 003 — 10:00 entry · ₹2 combined target · DTE slabs

**Rule (variant A, target-only):**
- Sell NIFTY CE & PE at first 1-min bar ≥ 10:00 IST
- Distance from spot: 3% / 4% / 5% (tested separately, nearest ₹50 strike)
- Exit BOTH legs when combined premium has decayed by **₹2 (points)** — i.e. (CE_entry + PE_entry) − (CE_now + PE_now) ≥ 2
- Otherwise square off at **15:15**
- Non-expiry days only (skips Tue & Thu); nearest weekly expiry leg

**Rule (variant B, target + stop):** same as A but ALSO exit both legs when combined **loss** ≥ ₹6 points.

**Sizing:** 1 lot per leg (lot=65).
**Friction:** ₹200 round-trip per leg, ₹400 total/day, subtracted in `net`.

> ⚠ **Economic reality check:** ₹2 combined decay × 65 lot = ₹130 gross per hit.  Friction is ₹400/day.  Every target-hit day is gross-positive but **net-negative before any losses**.  Gross figures are shown alongside net so you can see what the decay-capture itself earns.

## Headline: variant × distance (all non-expiry days, all DTE)

| variant | distance_pct | days | win_pct | gross | net | avg_gross | avg_net | best | worst | pt_hits | sl_hits | time_exits |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| tgt_only | 3.0 | 151 | 2.0 | 9204.0 | -51196.0 | 61.0 | -339.0 | 123.0 | -2578.0 | 86 | 0 | 65 |
| tgt_only | 4.0 | 151 | 0.7 | -546.0 | -60946.0 | -4.0 | -404.0 | 211.0 | -4729.0 | 57 | 0 | 94 |
| tgt_only | 5.0 | 149 | 1.3 | 2938.0 | -56662.0 | 20.0 | -380.0 | 221.0 | -2441.0 | 34 | 0 | 115 |
| tgt_stop | 3.0 | 151 | 1.3 | 3741.0 | -56659.0 | 25.0 | -375.0 | 107.0 | -1031.0 | 78 | 23 | 50 |
| tgt_stop | 4.0 | 151 | 0.0 | -1671.0 | -62070.0 | -11.0 | -411.0 | -153.0 | -3884.0 | 51 | 14 | 86 |
| tgt_stop | 5.0 | 149 | 0.7 | 1924.0 | -57676.0 | 13.0 | -387.0 | 19.0 | -920.0 | 31 | 9 | 109 |

## By DTE slab (variant × distance × DTE)

DTE = calendar days between trade date and expiry. Weekly expiries in data are Tue (current) and Thu (legacy); `5+` catches days 5-6 away from expiry (e.g. Wed before next-week's Tue expiry).

| variant | distance_pct | dte | days | win_pct | gross | net | avg_gross | avg_net | best | worst | pt_hits | sl_hits | time_exits |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| tgt_only | 3.0 | 1 | 45 | 0.0 | 2444.0 | -15556.0 | 54.0 | -346.0 | -26.0 | -634.0 | 12 | 0 | 33 |
| tgt_only | 3.0 | 2 | 2 | 0.0 | 267.0 | -533.0 | 133.0 | -267.0 | -267.0 | -267.0 | 2 | 0 | 0 |
| tgt_only | 3.0 | 3 | 22 | 4.5 | 1154.0 | -7646.0 | 52.0 | -348.0 | 3.0 | -933.0 | 15 | 0 | 7 |
| tgt_only | 3.0 | 4 | 29 | 0.0 | 864.0 | -10736.0 | 30.0 | -370.0 | -163.0 | -1866.0 | 16 | 0 | 13 |
| tgt_only | 3.0 | 5+ | 53 | 3.8 | 4475.0 | -16725.0 | 84.0 | -316.0 | 123.0 | -2578.0 | 41 | 0 | 12 |
| tgt_only | 4.0 | 1 | 45 | 0.0 | 1723.0 | -16278.0 | 38.0 | -362.0 | -163.0 | -602.0 | 9 | 0 | 36 |
| tgt_only | 4.0 | 2 | 2 | 0.0 | 341.0 | -459.0 | 171.0 | -229.0 | -224.0 | -234.0 | 2 | 0 | 0 |
| tgt_only | 4.0 | 3 | 22 | 0.0 | 1219.0 | -7581.0 | 55.0 | -345.0 | -172.0 | -706.0 | 10 | 0 | 12 |
| tgt_only | 4.0 | 4 | 29 | 0.0 | 864.0 | -10736.0 | 30.0 | -370.0 | -153.0 | -758.0 | 7 | 0 | 22 |
| tgt_only | 4.0 | 5+ | 53 | 1.9 | -4693.0 | -25893.0 | -89.0 | -489.0 | 211.0 | -4729.0 | 29 | 0 | 24 |
| tgt_only | 5.0 | 1 | 45 | 0.0 | 1245.0 | -16755.0 | 28.0 | -372.0 | -241.0 | -517.0 | 6 | 0 | 39 |
| tgt_only | 5.0 | 2 | 2 | 0.0 | 276.0 | -524.0 | 138.0 | -262.0 | -257.0 | -267.0 | 2 | 0 | 0 |
| tgt_only | 5.0 | 3 | 21 | 0.0 | 702.0 | -7698.0 | 33.0 | -367.0 | -257.0 | -543.0 | 4 | 0 | 17 |
| tgt_only | 5.0 | 4 | 28 | 0.0 | 897.0 | -10303.0 | 32.0 | -368.0 | -250.0 | -530.0 | 5 | 0 | 23 |
| tgt_only | 5.0 | 5+ | 53 | 3.8 | -182.0 | -21382.0 | -3.0 | -403.0 | 221.0 | -2441.0 | 17 | 0 | 36 |
| tgt_stop | 3.0 | 1 | 45 | 0.0 | 1953.0 | -16047.0 | 43.0 | -357.0 | -26.0 | -836.0 | 12 | 2 | 31 |
| tgt_stop | 3.0 | 2 | 2 | 0.0 | 267.0 | -533.0 | 133.0 | -267.0 | -267.0 | -267.0 | 2 | 0 | 0 |
| tgt_stop | 3.0 | 3 | 22 | 4.5 | -848.0 | -9648.0 | -39.0 | -439.0 | 3.0 | -933.0 | 12 | 7 | 3 |
| tgt_stop | 3.0 | 4 | 29 | 0.0 | 133.0 | -11467.0 | 5.0 | -395.0 | -163.0 | -1031.0 | 15 | 5 | 9 |
| tgt_stop | 3.0 | 5+ | 53 | 1.9 | 2236.0 | -18964.0 | 42.0 | -358.0 | 107.0 | -1011.0 | 37 | 9 | 7 |
| tgt_stop | 4.0 | 1 | 45 | 0.0 | 1723.0 | -16278.0 | 38.0 | -362.0 | -163.0 | -602.0 | 9 | 0 | 36 |
| tgt_stop | 4.0 | 2 | 2 | 0.0 | 341.0 | -459.0 | 171.0 | -229.0 | -224.0 | -234.0 | 2 | 0 | 0 |
| tgt_stop | 4.0 | 3 | 22 | 0.0 | 175.0 | -8624.0 | 8.0 | -392.0 | -172.0 | -1030.0 | 10 | 3 | 9 |
| tgt_stop | 4.0 | 4 | 29 | 0.0 | 660.0 | -10940.0 | 23.0 | -377.0 | -153.0 | -829.0 | 7 | 2 | 20 |
| tgt_stop | 4.0 | 5+ | 53 | 0.0 | -4570.0 | -25770.0 | -86.0 | -486.0 | -179.0 | -3884.0 | 23 | 9 | 21 |
| tgt_stop | 5.0 | 1 | 45 | 0.0 | 588.0 | -17412.0 | 13.0 | -387.0 | -241.0 | -917.0 | 5 | 1 | 39 |
| tgt_stop | 5.0 | 2 | 2 | 0.0 | 276.0 | -524.0 | 138.0 | -262.0 | -257.0 | -267.0 | 2 | 0 | 0 |
| tgt_stop | 5.0 | 3 | 21 | 0.0 | 325.0 | -8075.0 | 15.0 | -385.0 | -257.0 | -797.0 | 4 | 1 | 16 |
| tgt_stop | 5.0 | 4 | 28 | 0.0 | 897.0 | -10303.0 | 32.0 | -368.0 | -250.0 | -530.0 | 5 | 0 | 23 |
| tgt_stop | 5.0 | 5+ | 53 | 1.9 | -162.0 | -21362.0 | -3.0 | -403.0 | 19.0 | -920.0 | 15 | 7 | 31 |

## Files
- `target_only.csv`, `target_with_stop.csv` — per-day logs (all distances stacked)
- `by_dte.csv` — the DTE-slab table in CSV form
- `equity_curves.png` — gross (dashed) vs net (solid) for each distance, both variants

## Caveats
- Entry price = close of the first 1-min bar ≥ 10:00 IST (execution proxy).
- PT/SL triggers within a minute use intrabar high/low; when both could fire we assume the **stop** trips first (conservative for variant B).
- Spot proxied from NIFTY futures.
- Friction is a flat ₹200/leg; real cost scales with broker, size, liquidity of the far-OTM strike.
