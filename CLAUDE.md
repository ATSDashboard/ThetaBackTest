# Backtest Engine — Context & Protocol

Local, Parquet-based backtest engine for Indian index options. **Builds on
modules already written** in the main Theta Gainers Algo project — does NOT
rewrite them.

## Current state
**Phase 1 — Discovery (pending user sign-off)**

Run:
```bash
python scripts/discover.py
# → writes results/phase1_discovery.json
```
Then present findings to user. **Do not build the pipeline until user confirms.**

## What's reused from the main project (copied into `lib/` and `ml/`)

| File | Origin | What it does |
|------|--------|-------------|
| `lib/deep_otm.py` | main project → analytics module | Tier classification, cushion ratio, OI wall scoring, Black-Scholes-lite, exit-trigger logic |
| `lib/strike_filters.py` | main project → strike selector | 16 composable filter classes (distance, delta, premium/₹1Cr margin, IV rank, VIX regime, etc.) |
| `lib/strike_rules.py` | main project → strike selector | AllOf / AnyOf / Not combinators + rule evaluator |
| `lib/technicals.py` | main project → analytics | Classical pivots, Fibonacci, weekly pivot |
| `ml/` | main project → ml module | Training run registry, predictor scaffold, data ingest |

**Design principle:** the backtest runs IDENTICAL rule expressions to what
goes live. A `{all_of: [...]}` rule behaves the same here as in the trade
engine. Apples-to-apples comparison.

## What's new in this folder

| File | Purpose |
|------|---------|
| `scripts/discover.py` | Phase 1 inventory of your data folders |
| `ingest/build_store.py` | (Phase 2) full historical → Parquet store |
| `ingest/update_store.py` | (Phase 2) incremental TW dumps → append |
| `lib/replay.py` | Replay engine — load chain at T, evaluate rule, realize P&L at exit |
| `lib/duckdb_queries.py` | DuckDB helpers over Parquet |
| `analyses/*.py` | Parameterized research scripts (each committed, each writes to `results/`) |

## Data sources (read-only, NEVER modify)
- **Historical archive:** `/Users/rohanshah/Desktop/AI Instructions/Trading Developments/Options Data`
- **Incremental (manual TW dumps):** `/Users/rohanshah/Desktop/AI Instructions/Trading Developments/Options Data/New Options Data from 13:4:26 Manually Pulled from TW`

The incremental folder contains ONLY strikes actually traded that day.
Schema may differ. Handle both, flag unknown schemas.

## Strategy context (what you'll backtest)
Far-OTM index selling. Intraday only. NIFTY expiries Tuesdays, SENSEX
Thursdays. Entries post-open. Market hours 09:15–15:30 IST.

Typical questions:
- "With rule X, what's the hit rate on non-expiry days over 2025?"
- "How does premium decay by distance bucket (2%, 3%, 5%+)?"
- "Which entry time window gives best P/L after fees?"
- "VIX-regime bucketed win rate for Tier 1 vs Tier 2 setups?"

## Canonical schema (TARGET — confirm after Phase 1 report)
```
timestamp         datetime (IST)
instrument        str       NIFTY / SENSEX
expiry            date
strike            int
option_type       str       CE / PE / FUT / SPOT
open, high, low, close  float
volume            int
oi                int (nullable)
dte               int       derived
distance_pct      float     derived (strike vs spot at same timestamp)
moneyness_bucket  str       derived (ATM / OTM_near / OTM_deep / OTM_far)
```

## Partitioning (TARGET)
```
data/parquet/
├── instrument=NIFTY/year=2025/month=04/...
└── instrument=SENSEX/year=2025/month=04/...
```
One Parquet file per (instrument, month), 50-200 MB. Tracked in `data/manifest.parquet`.

## Ground rules
1. Never write to either data source folder
2. Never upload data anywhere (local only)
3. Every analysis = committed parameterized script in `analyses/`. No inline one-offs.
4. Every run writes to `results/YYYY-MM-DD_<slug>/` with summary.md + data.csv + chart.png
5. Append findings to Findings Log below after each analysis
6. If a new CSV has unseen schema, FLAG IT and ask. Don't guess.
7. IST timezone throughout.
8. Reuse `lib/strike_filters.py` rules, don't re-implement.

## Findings log
_(appended after each analysis — empty until Phase 2 ingestion + first run)_

---

## Protocol for answering a new research question
1. Note the question under "Open questions"
2. Create `analyses/NNN_<slug>.py` with @params block at top
3. DuckDB query + pandas transform + chart
4. Run; write `results/YYYY-MM-DD_<slug>/summary.md`
5. Append one-line finding to "Findings log" above
6. `git commit -am "analysis NNN: <short takeaway>"`

## Deliverable order
- [ ] Phase 1 discovery → **user sign-off**
- [ ] Canonical schema + partitioning locked → **user sign-off**
- [ ] Phase 2 ingestion built, full historical loaded, row counts + reject log shown
- [ ] Phase 3 scaffolding + one end-to-end analysis run
- [ ] Ready for research questions
