# 006 — Portfolio-Scale Simulation + Friction Sensitivity

Lift the per-1-lot per-day samples produced by 004 (E-1) and 005 (E-0) to
**₹1 Cr capital = 55 lots** and stress-test against your risk envelope:

- **Per-trade stop (= ₹7K/Cr aggregate ÷ 55 lots ÷ 65):** **₹1.96 / share** adverse
- **Aggregate cap:** ₹7,000 / Cr per loss day
- **Friction sweep:** ₹40 / 100 / 200 / 400 per lot / day
  (= ₹10 / 25 / 50 / 100 per leg one-way; full round-trip × 4 legs counted)

Sample window 2025-04-17 → 2026-04-21 ≈ 1 year, so the `pf_total` column is
already an approximate annual return. `annualized_pct` = pf_total ÷ ₹1Cr × 100.

## Top combinations (breach rate ≤ 5%, no aggregate stop)

| event | distance % | friction | days | win % | pf_total ₹ | pf_mean | pf_worst | %breach ₹7K | annualized % |
|---|---|---|---|---|---|---|---|---|---|
| E-1 | 2.0 | ₹40/lot (₹10/leg) | 46 | 95.7 | ₹4,327,840 | ₹94,083 | ₹-149,655 | 4.3% | 43.28% |
| E-1 | 2.0 | ₹100/lot (₹25/leg) | 46 | 95.7 | ₹4,176,040 | ₹90,783 | ₹-152,955 | 4.3% | 41.76% |
| E-1 | 2.0 | ₹200/lot (₹50/leg) | 46 | 95.7 | ₹3,923,040 | ₹85,283 | ₹-158,455 | 4.3% | 39.23% |
| E-1 | 2.5 | ₹40/lot (₹10/leg) | 46 | 100.0 | ₹3,027,750 | ₹65,821 | ₹7,645 | 0.0% | 30.28% |
| E-1 | 2.5 | ₹100/lot (₹25/leg) | 46 | 100.0 | ₹2,875,950 | ₹62,521 | ₹4,345 | 0.0% | 28.76% |
| E-1 | 2.5 | ₹200/lot (₹50/leg) | 46 | 95.7 | ₹2,622,950 | ₹57,021 | ₹-1,155 | 0.0% | 26.23% |
| E-1 | 3.0 | ₹40/lot (₹10/leg) | 46 | 100.0 | ₹2,068,990 | ₹44,978 | ₹5,830 | 0.0% | 20.69% |
| E-1 | 3.0 | ₹100/lot (₹25/leg) | 46 | 100.0 | ₹1,917,190 | ₹41,678 | ₹2,530 | 0.0% | 19.17% |
| E-1 | 3.0 | ₹200/lot (₹50/leg) | 46 | 78.3 | ₹1,664,190 | ₹36,178 | ₹-2,970 | 0.0% | 16.64% |
| E-0 | 2.0 | ₹40/lot (₹10/leg) | 48 | 97.9 | ₹1,299,100 | ₹27,065 | ₹-42,570 | 2.1% | 12.99% |
| E-0 | 2.0 | ₹100/lot (₹25/leg) | 48 | 97.9 | ₹1,140,700 | ₹23,765 | ₹-45,870 | 2.1% | 11.41% |
| E-1 | 4.0 | ₹40/lot (₹10/leg) | 46 | 100.0 | ₹1,134,430 | ₹24,662 | ₹2,640 | 0.0% | 11.34% |
| E-1 | 4.0 | ₹100/lot (₹25/leg) | 46 | 97.8 | ₹982,630 | ₹21,362 | ₹-660 | 0.0% | 9.83% |
| E-0 | 2.0 | ₹200/lot (₹50/leg) | 48 | 64.6 | ₹876,700 | ₹18,265 | ₹-51,370 | 2.1% | 8.77% |
| E-1 | 4.0 | ₹200/lot (₹50/leg) | 46 | 52.2 | ₹729,630 | ₹15,862 | ₹-6,160 | 0.0% | 7.3% |


## Friction is the dominant lever

For every event-type × distance combo, here's how annualized return on ₹1Cr varies with friction (no stop, no filtering):

See `annualized_return_by_friction.png` — the curves shift roughly **+200 to +500 bps per ₹100/lot of friction reduction**.  Even at deep distances where placeholder friction makes things look unprofitable, real-world ₹40-100/lot cost flips many strategies into double-digit annualized.

## Per-event distribution (4 candidate strategies)

`distribution_per_event.png` shows the chronological per-event P&L bars at ₹100/lot friction for four representative combos.  Bars below the dashed line breach your ₹7K/Cr cap; the goal is to see the cap rarely violated.

## How to use this

1. **Decide your real friction** (₹/lot/day, both legs round-trip).
2. **Look up the annualized %** in `portfolio_pl_matrix.csv` filtered to your friction bucket.
3. **Check the breach rate** — anything > 5% means the aggregate cap will get hit too often; drop deeper or apply per-trade stop.
4. **Rule of thumb from this run:**
   - At ₹400/lot (placeholder friction): nothing meaningfully positive.
   - At ₹200/lot: 2-3% OTM E-0 / E-1 turn marginal positive (~5-12% ann.).
   - At ₹100/lot: 2.5-3% OTM E-1 hits 18-25% ann. with sub-3% breach rate.
   - At ₹40/lot: 2.5% OTM E-1 ~30%+ ann.; even 4-5% OTM gives 5-10% with near-zero breach.

## Caveats

- **Aggregate-stop rows in the matrix (`stop=with_stop`) treat the per-trade ₹1.95-stop as an immediate exit.**  This is conservative — real-world fills slip.  When MAE > stop, per-lot loss = ₹127 + friction.
- Sample is ~1 calendar year — the annualization is therefore the realized total over that window, not a proper cross-validated estimate.
- 55-lots/Cr assumes uniform distance — same exposure 55× over.  No diversification benefit; aggregate stop = per-trade stop in this model.
- Real laddering across distances/expiries would reduce intra-day correlation and improve the breach rate.  See the 'Laddered strangle' backlog item.

## Files
- `portfolio_pl_matrix.csv` — full grid (event × distance × friction × stop)
- `annualized_return_by_friction.png`
- `distribution_per_event.png`
