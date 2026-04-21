# ML Module — Expiry Day Options Prediction

Learns from historical tick + option chain + news data to predict:
- How Deep OTM / Mid Deep OTM option prices move intraday
- Best strikes to sell on expiry day
- Best times to enter and exit
- Calibrated probability of expiring OTM

## Data you upload

| Dataset kind | Format | Contents |
|---|---|---|
| `CANDLES_1M` | CSV/Parquet | 1-min OHLCV for NIFTY spot, NIFTY futures, SENSEX spot, SENSEX futures |
| `OPTION_CHAIN` | CSV/Parquet | 1-min snapshots of full option chain (strike, LTP, bid, ask, OI, volume, IV) |
| `NEWS` | JSON/CSV | Headlines + timestamp + optional sentiment label + affected tickers |
| `MACRO` | CSV | Fed decisions, RBI minutes, macro calendars |
| `FII_DII` | CSV | Daily FII/DII net cash + F&O positioning |
| `VIX` | CSV | 1-min India VIX |

Recommended window: 1+ year per dataset for meaningful training. Can grow as you feed more.

## Training pipeline

1. **Ingest** — files validated + stored (local or S3) + indexed in `ml_datasets`
2. **Feature engineering** (`features/`)
   - Market snapshot: spot, futures basis, VIX, VIX regime
   - Option chain: IV surface, OI concentration, PCR, Max Pain, OI-wall distances
   - Temporal: minute-of-day, DTE, day-of-week, distance-to-expiry-close
   - News: embedding + sentiment aligned to minute boundary
   - Greeks: approximate Δ, Γ, Θ, Vega per strike
3. **Target construction**
   - For each option sample, compute forward price at t+N minutes AND whether it expired OTM
4. **Split** — purged time-series split (no leakage)
5. **Train** — ensemble: gradient-boosted trees for price direction + separate probability model with isotonic calibration
6. **Evaluate** — Brier score, log loss, hit rate per tier, simulated P&L
7. **Register** — serialized model + metrics → `ml_training_runs`

## Live inference

`POST /ml/predict` — returns ranked Deep OTM / Mid Deep OTM strike recommendations:
```json
[
  {
    "underlying": "NIFTY",
    "strike": 25500,
    "option_type": "CE",
    "recommended_action": "SELL",
    "predicted_probability_otm": 0.947,
    "confidence_score": 0.91,
    "recommended_entry_window_start": "09:30",
    "recommended_entry_window_end":   "10:15",
    "recommended_exit_pct": 70,
    "reasoning": "Strike sits behind 2 top-3 OI walls. VIX in calm regime. Fresh writing on 25500 CE last 15 min. Historical base rate for this cushion+VIX regime: 94.2%."
  },
  ...
]
```

## Extension points

- New model types: add enum value + implement in `training/<new_kind>.py`
- New data kinds: add enum value + validator + feature extractor
- Live streaming: inference can subscribe to `/quote/stream` when available

## NOT included yet

- Actual model training code (awaits real data)
- News embedding model choice (suggestion: Indian-finance-tuned sentence transformer, e.g., `AnkurK/sentence-bert-financial` or a FinBERT variant)
- S3 storage wiring (uses local filesystem in dev)
- Walk-forward backtest runner UI
