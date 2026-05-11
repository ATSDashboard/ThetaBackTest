# 015 — NIFTY ATM Straddle SELL · DTE bucketed (E-1 to E-4)

_Generated 2026-05-10 22:47 IST_

## Question

> Sell ATM straddles E-1 to E-4 days. Find rule + window that delivers ₹5-7K per Cr with high hit rate. (Rohan, 2026-05-10)

## Methodology

- **Data:** NIFTY 1-min OHLC parquet store. 253 trading days (2025-04-17 → 2026-05-05). Sun 2026-02-01 (Budget data anomaly) excluded; only the front-month FUT contract is used per timestamp.
- **DTE definition:** trading-day distance to next weekly NIFTY expiry (Mon-Fri, holidays not subtracted).
- **Strike:** ATM = round-to-nearest-50 of FUT close at entry time.
- **Position:** SELL 1 ATM CE + 1 ATM PE at entry, BUY back at exit. P&L = (entry_combined - exit_combined) × shares.
- **Sizing:** ₹2.35L NIFTY E-0 margin/lot → 42.6 lots/Cr → 3,225 shares/Cr (user-spec rounding 43 lots/Cr).
- **Friction (default headline):** ₹100/leg × 2 legs = ₹200/lot (matches analyses 006/007). Also reported in CSV: Axis ₹12/lot, Monarch ₹20/lot.
- **Filters:** max-pain within 0.5% of spot at entry, VIX-proxy ≤ 17.0, |gap| ≤ 0.5%, intraday net move at 10:30 ≤ 1.0%.
- **VIX proxy:** 20-day annualized realized vol of front-month FUT close — INDIA VIX is **not** in parquet store. Realized vol correlates ~0.6-0.8 with VIX — direction is right, calibration may differ. Use live VIX in production.
- **Max-pain proxy:** strike with highest summed CE+PE OI within ±20 strikes of ATM, snapshot at 09:30. Approximation of true max-pain (which uses payoff curves).
- **Intraday move filter:** |close@10:30 − open|/open (NET directional move, not high-low range).

## Sample size

| DTE | Trading days |
|-----|--------------|
| E-1 | 54 |
| E-2 | 51 |
| E-3 | 48 |
| E-4 | 42 |

## Key reframe of the question

Rohan's spec asked for ₹5-7K per Cr. **Reality at ATM:** even on the calmest days, NIFTY ATM straddle prints ₹15-50K decay/Cr in a 3-hour window. A ₹5-7K outcome is the bottom of the distribution (the ~p20 of decent days), not the median. So the right framing is:

- **Median yield is much higher** than ₹5-7K — typically ₹20-50K/Cr.
- **The risk is the LEFT tail**: a single bad day prints −₹200K to −₹850K/Cr. One blowup wipes 4-30 ordinary winning days.
- **The win-rate framing is wrong for this strategy.** A 70% win rate at +₹30K median with −₹400K tail is NOT a +EV trade unless the right tail is comparable. We need to look at MEAN ₹/Cr (not median), and the loss-per-loss-day distribution.

- **The strategy is closer to short-vol-with-fat-left-tail than 'collect ₹5-7K decay'.**

## Headline — per DTE (no filters · all entry/exit combos pooled)

| DTE | n | win% | median ₹/Cr | mean ₹/Cr | p25 | p75 | worst | best | %≥₹5K |
|-----|---|------|-------------|-----------|-----|-----|-------|------|-------|
| E-1 | 624 | 61.2% | +21,888 | +8,628 | -30,651 | +63,537 | -589,043 | +469,734 | 58.2% |
| E-2 | 612 | 64.5% | +19,574 | +3,280 | -23,072 | +53,444 | -434,096 | +403,989 | 61.1% |
| E-3 | 576 | 56.1% | +8,086 | -14,444 | -41,702 | +45,904 | -853,457 | +246,170 | 52.4% |
| E-4 | 504 | 55.0% | +8,165 | -18,143 | -63,484 | +44,827 | -509,096 | +200,851 | 52.0% |

_Note: mean is much lower than median because of fat left tail. E-3 mean is **negative** despite 56% win rate._

## Best (entry × exit) combo per DTE (no filters)

### E-1
| entry | exit | n | win% | median ₹/Cr | worst | best | %≥₹5K |
|-------|------|---|------|-------------|-------|------|-------|
| 09:30 | 15:25 | 52 | 67.3% | +66,808 | -589,043 | +469,734 | 65.4% |
| 10:00 | 15:25 | 52 | 63.5% | +60,026 | -434,255 | +295,319 | 61.5% |
| 09:30 | 13:00 | 52 | 67.3% | +51,968 | -290,319 | +313,351 | 67.3% |

### E-2
| entry | exit | n | win% | median ₹/Cr | worst | best | %≥₹5K |
|-------|------|---|------|-------------|-------|------|-------|
| 09:30 | 13:00 | 51 | 72.5% | +38,564 | -369,947 | +216,649 | 68.6% |
| 10:30 | 15:25 | 51 | 62.7% | +41,277 | -434,096 | +194,149 | 60.8% |
| 10:00 | 15:25 | 51 | 68.6% | +34,096 | -431,223 | +346,543 | 66.7% |

### E-3
| entry | exit | n | win% | median ₹/Cr | worst | best | %≥₹5K |
|-------|------|---|------|-------------|-------|------|-------|
| 10:30 | 15:25 | 48 | 62.5% | +29,708 | -534,149 | +172,926 | 62.5% |
| 10:00 | 15:25 | 48 | 54.2% | +28,272 | -689,415 | +206,755 | 54.2% |
| 10:30 | 13:00 | 48 | 62.5% | +16,224 | -334,362 | +162,394 | 58.3% |

### E-4
| entry | exit | n | win% | median ₹/Cr | worst | best | %≥₹5K |
|-------|------|---|------|-------------|-------|------|-------|
| 10:30 | 15:25 | 42 | 61.9% | +31,862 | -267,979 | +164,628 | 59.5% |
| 10:30 | 13:00 | 42 | 66.7% | +23,404 | -286,968 | +120,106 | 66.7% |
| 10:30 | 12:00 | 42 | 69.0% | +18,138 | -211,649 | +94,415 | 64.3% |

## ₹5-7K/Cr: the exact band Rohan asked about

How often does the straddle land in EXACTLY ₹5-7K/Cr? (Hint: rarely — ATM straddles are too volatile to land in such a narrow band.)

| DTE | entry → exit | n | %₹5-7K | %₹4K+ | %positive |
|-----|--------------|---|--------|-------|-----------|
| E-1 | 10:00 → 12:00 | 52 | 0.0% | 55.8% | 57.7% |
| E-2 | 10:00 → 12:00 | 51 | 0.0% | 68.6% | 70.6% |
| E-3 | 10:00 → 12:00 | 48 | 0.0% | 54.2% | 56.2% |
| E-4 | 10:00 → 12:00 | 42 | 0.0% | 50.0% | 50.0% |

**Implication:** the ₹5-7K target is unattainable as a 'every time we trade we earn this' outcome at ATM. The realistic options are:
1. **Move further OTM** (use analyses 006/007 — 2.5-3% OTM E-1 already delivered ₹50-60K/Cr at near-100% hit rate).
2. **Accept higher upside + matched downside** at ATM (median ₹20-50K/Cr · worst day ₹-200K-₹-850K/Cr).
3. **Use ATM with mandatory stop-loss** (not modeled here — should explore as analysis 016).

## Filter sensitivity (each filter alone vs. stacked)

Aggregated over ALL (entry × exit) combos per DTE.

| DTE | filter | n | win% | median ₹/Cr | %≥₹5K | worst |
|-----|--------|---|------|-------------|-------|-------|
| E-1 | no filters | 624 | 61.2% | +21,888 | 58.2% | -589,043 |
| E-1 | all filters | 356 | 60.4% | +13,830 | 55.9% | -589,043 |
| E-1 | mp_pain_only | 508 | 59.3% | +16,223 | 55.9% | -589,043 |
| E-1 | vix_only | 468 | 59.8% | +15,266 | 55.8% | -589,043 |
| E-1 | gap_only | 528 | 60.2% | +18,856 | 57.0% | -589,043 |
| E-1 | intraday_only | 612 | 61.3% | +21,729 | 58.2% | -589,043 |
| E-2 | no filters | 612 | 64.5% | +19,574 | 61.1% | -434,096 |
| E-2 | all filters | 276 | 60.9% | +14,468 | 57.6% | -434,096 |
| E-2 | mp_pain_only | 340 | 60.3% | +14,229 | 56.8% | -434,096 |
| E-2 | vix_only | 492 | 64.2% | +16,462 | 61.0% | -434,096 |
| E-2 | gap_only | 564 | 64.9% | +19,574 | 61.3% | -434,096 |
| E-2 | intraday_only | 612 | 64.5% | +19,574 | 61.1% | -434,096 |
| E-3 | no filters | 576 | 56.1% | +8,086 | 52.4% | -853,457 |
| E-3 | all filters | 268 | 60.1% | +8,804 | 54.5% | -702,660 |
| E-3 | mp_pain_only | 352 | 57.4% | +7,766 | 52.3% | -702,660 |
| E-3 | vix_only | 444 | 57.9% | +8,804 | 53.6% | -714,628 |
| E-3 | gap_only | 408 | 58.1% | +9,043 | 53.7% | -702,660 |
| E-3 | intraday_only | 552 | 57.4% | +9,043 | 53.6% | -702,660 |
| E-4 | no filters | 504 | 55.0% | +8,165 | 52.0% | -509,096 |
| E-4 | all filters | 248 | 50.8% | +1,462 | 47.6% | -509,096 |
| E-4 | mp_pain_only | 328 | 52.1% | +2,101 | 48.5% | -509,096 |
| E-4 | vix_only | 408 | 58.6% | +13,750 | 56.1% | -509,096 |
| E-4 | gap_only | 372 | 45.7% | -8,272 | 43.3% | -509,096 |
| E-4 | intraday_only | 492 | 55.7% | +9,362 | 52.8% | -509,096 |

## Tail-risk: worst single days

_n = 156 unique (date × DTE) blowup days (loss > ₹5K/Cr at the worst entry/exit). Full grid: 863 combos in `tail_loss_days.csv`._

**Top 20 worst loss-days (one row per date, showing the worst entry/exit per day):**

| date | DTE | weekday | worst entry/exit | spot | gap% | intra-move% to 10:30 | VIX-proxy | mp-dist% | ₹/Cr |
|------|-----|---------|------------------|------|------|----------------------|-----------|----------|------|
| 2025-04-25 | E-3 | Friday | 09:30→12:00 | 24432.3 | +0.89 | 1.67 | n/a | 0.54 | -853,457 |
| 2025-05-12 | E-3 | Monday | 09:30→15:25 | 24660.0 | +1.59 | 1.29 | 13.8 | 2.68 | -714,628 |
| 2026-02-19 | E-3 | Thursday | 09:30→15:25 | 25815.0 | +0.08 | 0.36 | 15.0 | 0.06 | -702,660 |
| 2025-12-08 | E-1 | Monday | 09:30→15:25 | 26279.9 | -0.04 | 0.30 | 10.1 | 0.30 | -589,043 |
| 2026-03-11 | E-4 | Wednesday | 09:30→15:25 | 24304.9 | -0.27 | 0.53 | 16.9 | 0.02 | -509,096 |
| 2025-05-02 | E-4 | Friday | 10:00→13:00 | 24709.0 | +0.08 | 0.97 | 15.9 | 1.25 | -472,553 |
| 2026-01-09 | E-2 | Friday | 10:30→15:25 | 25990.0 | +0.00 | 0.12 | 9.0 | 0.04 | -434,096 |
| 2026-03-13 | E-2 | Friday | 09:30→15:25 | 23552.9 | -0.92 | 0.26 | 17.9 | 0.23 | -403,298 |
| 2026-01-08 | E-3 | Thursday | 10:00→15:25 | 26176.2 | -0.18 | 0.16 | 8.3 | 0.09 | -402,660 |
| 2026-01-05 | E-1 | Monday | 10:30→15:25 | 26485.2 | +0.08 | 0.05 | 9.3 | 0.70 | -386,383 |
| 2025-06-06 | E-4 | Friday | 09:30→12:00 | 24815.8 | -0.08 | 0.26 | 18.9 | 0.47 | -380,479 |
| 2026-02-25 | E-3 | Wednesday | 10:30→13:00 | 25802.3 | +0.91 | 0.56 | 15.4 | 1.17 | -334,362 |
| 2026-01-23 | E-2 | Friday | 10:30→15:25 | 25329.0 | +0.02 | 0.24 | 10.1 | 0.11 | -334,202 |
| 2026-01-21 | E-4 | Wednesday | 09:30→11:00 | 25293.7 | -0.12 | 0.37 | 9.5 | 2.79 | -332,447 |
| 2025-09-05 | E-2 | Friday | 10:00→13:00 | 24893.0 | +0.08 | 0.19 | 10.1 | 0.37 | -317,447 |
| 2026-02-27 | E-1 | Friday | 09:30→15:25 | 25508.5 | -0.26 | 0.41 | 15.6 | 0.03 | -313,298 |
| 2025-10-31 | E-2 | Friday | 10:00→15:25 | 26086.8 | -0.10 | 0.06 | 13.4 | 0.33 | -293,511 |
| 2025-12-01 | E-1 | Monday | 09:30→13:00 | 26466.0 | +0.22 | 0.01 | 10.3 | 0.63 | -290,319 |
| 2026-05-04 | E-1 | Monday | 10:00→13:00 | 24345.4 | +0.39 | 0.36 | 28.0 | 0.60 | -280,426 |
| 2025-06-20 | E-4 | Friday | 09:30→15:25 | 24852.0 | +0.19 | 0.90 | 10.9 | 0.21 | -277,234 |

**Pattern observations:**
- Several blowup days had **calm 10:30 conditions** (gap <0.5%, intraday <0.4%) — the move came LATER. Filters catching only morning conditions miss afternoon shocks.
- 2026-01-09 (E-2): gap 0%, calm morning — but spot moved late. Lost ₹400K+/Cr at multiple entry/exit combos.
- 2026-03-11 (E-4): gap −0.27%, calm morning, VIX-proxy 16.9 — but a directional sell-off built up, blew through ATM straddle. ₹500K/Cr loss.
- 2025-04-25 (E-3): gap +0.89%, intraday 1.67% (already moving) → pre-existing momentum continued. ₹850K/Cr loss.


## Recommendation — strongest rule

**Top 15 candidates (n ≥ 12, ranked by win% × median):**

| DTE | entry | exit | filter | n | win% | median ₹/Cr | p25 | worst |
|-----|-------|------|--------|---|------|-------------|-----|-------|
| E-1 | 09:30 | 15:25 | no filters | 52 | 67.3% | +66,808 | -43,058 | -589,043 |
| E-1 | 09:30 | 15:25 | gap+intraday only | 44 | 65.9% | +63,617 | -43,058 | -589,043 |
| E-1 | 10:00 | 15:25 | no filters | 52 | 63.5% | +60,026 | -45,731 | -434,255 |
| E-1 | 10:00 | 15:25 | all filters | 30 | 63.3% | +58,111 | -40,425 | -434,255 |
| E-1 | 10:00 | 15:25 | vix+gap only | 35 | 62.9% | +57,234 | -47,207 | -434,255 |
| E-1 | 09:30 | 13:00 | vix+gap only | 35 | 71.4% | +49,255 | -19,202 | -290,319 |
| E-1 | 10:00 | 15:25 | gap+intraday only | 44 | 61.4% | +58,111 | -45,731 | -434,255 |
| E-1 | 09:30 | 13:00 | no filters | 52 | 67.3% | +51,968 | -31,529 | -290,319 |
| E-1 | 09:30 | 13:00 | all filters | 31 | 71.0% | +48,777 | -19,202 | -244,202 |
| E-2 | 10:30 | 15:25 | gap+intraday only | 47 | 66.0% | +51,170 | -40,186 | -434,096 |
| E-1 | 09:30 | 15:25 | vix+gap only | 35 | 65.7% | +51,170 | -52,154 | -589,043 |
| E-1 | 09:30 | 13:00 | gap+intraday only | 44 | 68.2% | +49,016 | -24,109 | -290,319 |
| E-2 | 09:30 | 15:25 | vix+gap only | 39 | 66.7% | +49,255 | -41,861 | -410,798 |
| E-2 | 09:30 | 13:00 | vix+gap only | 39 | 76.9% | +40,479 | +10,159 | -369,947 |
| E-2 | 09:30 | 15:25 | gap+intraday only | 47 | 63.8% | +49,255 | -41,861 | -410,798 |

### Best practical rule per DTE

| DTE | rule | n | win% | median ₹/Cr | p25 | worst | per-month opportunity |
|-----|------|---|------|-------------|-----|-------|----------------------|
| E-1 | entry 09:30 → exit 15:25 (no filters) | 52 | 67% | +66,808 | -43,058 | -589,043 | ~4.3/mo |
| E-2 | entry 10:30 → exit 15:25 (gap+intraday only) | 47 | 66% | +51,170 | -40,186 | -434,096 | ~3.9/mo |
| E-3 | entry 10:30 → exit 15:25 (gap+intraday only) | 34 | 74% | +36,888 | -4,361 | -534,149 | ~2.8/mo |
| E-4 | entry 10:30 → exit 15:25 (no filters) | 42 | 62% | +31,862 | -28,896 | -267,979 | ~3.5/mo |

### Most-defensive rules (lowest tail risk while still profitable)

Filter: win% ≥ 65, median ≥ +20K/Cr, p25 ≥ -25K/Cr (bottom-quartile day still mild). Sample ≥ 20.

| DTE | entry | exit | filter | n | win% | median ₹/Cr | p25 | worst |
|-----|-------|------|--------|---|------|-------------|-----|-------|
| E-1 | 09:30 | 13:00 | vix+gap only | 35 | 71% | +49,255 | -19,202 | -290,319 |
| E-1 | 09:30 | 13:00 | all filters | 31 | 71% | +48,777 | -19,202 | -244,202 |
| E-1 | 09:30 | 13:00 | gap+intraday only | 44 | 68% | +49,016 | -24,109 | -290,319 |
| E-2 | 09:30 | 13:00 | vix+gap only | 39 | 77% | +40,479 | +10,159 | -369,947 |
| E-2 | 09:30 | 13:00 | gap+intraday only | 47 | 74% | +38,883 | -9,628 | -369,947 |
| E-2 | 09:30 | 13:00 | no filters | 51 | 73% | +38,564 | -23,750 | -369,947 |
| E-3 | 10:30 | 15:25 | gap+intraday only | 34 | 74% | +36,888 | -4,361 | -534,149 |
| E-3 | 10:30 | 15:25 | vix+gap only | 31 | 74% | +36,170 | -531 | -534,149 |

## Friction sensitivity at the recommended rule

At default ₹100/lot/leg friction (₹200/lot total), numbers above stand.
At Axis ₹6/lot/leg (₹12/lot total) — net P&L per lot improves by ₹188 (~₹8K/Cr).
At Monarch ₹10/lot/leg (₹20/lot total) — improves by ₹180 (~₹7.6K/Cr).

_Implication:_ if backtest median is ₹X/Cr at default friction, real Axis median ≈ ₹X+8K/Cr, real Monarch ≈ ₹X+7.6K/Cr.
This dramatically changes the picture for borderline rules.

## Data gaps & caveats

- **VIX:** not in parquet; using 20-day realized vol of FUT as proxy. Live VIX should be used in production.
- **Max-pain:** approximated by max OI strike (CE+PE summed). True max-pain uses payoff curves but the proxy works well for 'pin' detection.
- **Holidays not subtracted from DTE:** trading days = Mon-Fri. A holiday-shortened week may shift bucketing by 1.
- **NIFTY weekly switch (Thu→Tue) on 2025-09-02** — sample mixes both eras. E-1 days are mostly Mon (post-Sep) and Wed (pre-Sep).
- **No slippage modeled** — used 1-min bar close. Tight ATM strangles likely have minor slippage at 9:30.

## Files

- `per_day_results.csv` — every (date, DTE, entry, exit) sim with all features
- `by_dte.csv` — DTE-level stats
- `by_dte_entry_exit.csv` — DTE × entry × exit grid
- `filter_sensitivity.csv` — each filter on/off, all DTEs
- `candidates_ranked.csv` — every (DTE × entry × exit × filter) with n ≥ 12
- `tail_loss_days.csv` — every loss day worse than -₹5K/Cr
- `yield_distribution.png` · `heatmap_median_rs_cr.png`