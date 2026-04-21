# Theta Gainers — Backtest Engine

Local, Parquet-based backtest engine for Indian index options. Separate
from the main trading tool. Entire project stays on disk — no cloud.

## Status
**Phase 1 — Discovery**

```bash
python scripts/discover.py
# → writes results/phase1_discovery.json
# → review findings before Phase 2 (pipeline build)
```

See `CLAUDE.md` for full context and the build protocol.
