# Phase 1 Discovery Report

Run date: 2026-04-21 · Scripts: `scripts/discover.py` + manual archive inspection

## TL;DR
- **~1 year of 1-min NIFTY F&O + Spot data** in 45 monthly ZIPs (GFDL vendor format)
- **NO historical SENSEX** — only NIFTY
- **22 small TW-exported CSVs** for specific traded strikes (NIFTY 1 day + SENSEX 2 days)
- **3 distinct schemas** to handle in ingestion

---

## 1. Folder structure

```
Options Data/                                              (675 MB total)
├── NIFTY/
│   ├── Jan to March-26 NIFTY Expiry Days/               ← expiry-day only subset
│   │   ├── F&O/
│   │   └── Spot/
│   └── NIFTY F&O & Spot Data Apr 25 to Apr 26/          ← 1-year full set
│       ├── Nifty(Fut+Opt)_Contractwise_1 Min Data/
│       │   ├── 2025/     9 monthly ZIPs (APR,MAY,…DEC)
│       │   └── 2026/     4 monthly ZIPs + 1 unzipped (JAN,FEB,MAR,APR)
│       └── Nifty(Spot)_1 Min Data/
├── NIFTY_entry_timing_v{2,3}.xlsx                       ← user's prior analyses
├── NIFTY_safety_tiers.xlsx                              ← user's prior tier work
├── session_summary_16apr2026.md                         ← notes, not data
└── New Options Data from 13:4:26 Manually Pulled from TW/   (0.6 MB)
    ├── NIFTY/21-04-2026/                 6 CSVs
    └── SENSEX/
        ├── 05-03-2026/                   wide-format file
        └── 16-04-2026/                   ~14 narrow CSVs
```

## 2. Historical — GFDL format (the bulk of the data)

One ZIP per month. Each ZIP = ~20-22 daily CSVs (one per trading day).
Each daily CSV = every 1-min bar for every NIFTY option + future traded that day.

**Schema (uniform across all 45 ZIPs):**
```
Ticker,Date,Time,Open,High,Low,Close,Volume,Open Interest
NIFTY05JUN2522600PE.NFO,07/05/2025,10:00:59,91.1,91.1,91.1,91.1,75,3000
NIFTY05JUN2522600PE.NFO,07/05/2025,10:32:59,91.1,92.3,91.1,92.3,75,3000
```

**Ticker grammar:** `<UNDERLYING><DDMMMYY><STRIKE><CE|PE|FUT>.NFO`
- `NIFTY05JUN2522600PE.NFO` = NIFTY · 05 Jun 2025 expiry · strike 22600 · Put
- `NIFTY05JUN25FUT.NFO`      = NIFTY futures for 05 Jun 2025 expiry

**Date/time:** `DD/MM/YYYY` + `HH:MM:SS` (no timezone — assume IST).

**Notes:**
- Only rows with actual trades present — sparse chain, not every minute every strike.
- Volume in option contracts (not lots).
- Total historical rows (estimate): ~200-300M.

## 3. Historical Spot — `Nifty(Spot)_1 Min Data/`

Separate folder, one file per period. Need to inspect ZIPs here too — likely same GFDL format but instrument = NIFTY SPOT (no strike).

## 4. Incremental — TW (TradingView) dumps

Two sub-formats seen in the 22 CSVs:

### 4a. **Narrow** (most common, 16 files)
Filename encodes the symbol:
```
BSE_DLY_BSX260416P77500, 5 (1).csv
└── BSX · 26/04/16 expiry · P · strike 77500
```
Or for NIFTY:
```
NSE_DLY_NIFTY...
```

Inside:
```
time,open,high,low,close,Volume
2026-04-10T09:15:00+05:30,1141.45,1180,877.4,877.4,67740
```
Clean ISO-8601 IST timestamps. 5-min bars (note: coarser than historical 1-min).

### 4b. **Wide** (SENSEX / 05-03-2026 only)
Multiple instruments side-by-side in one file, ~27 columns:
```
[ts1,o,h,l,c,vol] | [ts2, CE strike, o,h,l,c, spot_close, vol] | [ts3, PE strike, …]
```
Header row is mostly empty strings; instrument labels like `"76500 PE"`, `"81700 CE"`, `"Sensex"` appear at column group headers. **Needs custom parser** that reads the first 2 rows together to identify instrument per column-group.

## 5. Schema normalization required

Ingestion needs 3 parsers:

| Source | Parser job | Canonical output |
|--------|-----------|------------------|
| GFDL daily CSV | Parse ticker → (instrument, expiry, strike, type). Combine Date+Time as IST. | timestamp, instrument, expiry, strike, option_type, o/h/l/c/volume, oi |
| TW narrow CSV | Parse filename → (instrument, expiry, strike, type). Timestamp already ISO. OI missing. | same schema with oi=NULL |
| TW wide CSV | Read first 2 rows together to map column→instrument. Unpivot into long format. | same schema with oi=NULL |

## 6. Gaps flagged

1. **No historical SENSEX** in the archive — only NIFTY. SENSEX backtests will only cover the ~2 TW manual-dump dates. → Decide: buy SENSEX historical data, or limit SENSEX analyses to manually-pulled days.
2. **Different bar sizes** — historical is 1-min, TW incremental is 5-min. Normalize to 1-min base (upsample not possible; keep raw granularity per source, reconcile at query time).
3. **No open interest on TW** — only on GFDL historical. Any OI-based filters (`OI_MIN`, `OI_WALL_BEHIND`) will only work on historical data.
4. **Noise files to skip** during ingestion:
   - `~$NIFTY_safety_tiers.xlsx` (Excel lock file)
   - `.tmp.drivedownload` (partial Drive download)
   - `session_summary_16apr2026.md` (notes)
   - `NIFTY_entry_timing_*.xlsx`, `NIFTY_safety_tiers.xlsx` (your prior analyses — inputs for `analyses/`, not raw data)

## 7. Proposed canonical schema (locking in for Phase 2)

```
timestamp        datetime (IST, timezone-aware)
source           str       ('GFDL_HISTORICAL' | 'TW_NARROW' | 'TW_WIDE')
instrument       str       ('NIFTY' | 'SENSEX' | 'BANKNIFTY')
expiry           date      NULL for SPOT
strike           int       NULL for FUT/SPOT
option_type      str       ('CE' | 'PE' | 'FUT' | 'SPOT')
open, high, low, close  float
volume           int
oi               int       NULL where absent
bar_minutes      int       (1 or 5)
-- derived at ingestion:
dte              int       (expiry - date(timestamp))
```

## 8. Proposed partitioning

```
data/parquet/
├── instrument=NIFTY/
│   ├── year=2025/month=05/file.parquet
│   └── year=2026/month=04/file.parquet
└── instrument=SENSEX/
    └── year=2026/month=04/file.parquet
```
Target 50-200 MB per Parquet. Manifest at `data/manifest.parquet` tracks
each ingested source file by path + SHA256 + row count.

---

## 🚦 Awaiting sign-off

Two decisions needed before Phase 2 starts:

1. **Confirm canonical schema** (Section 7) — OK as-is, or any field to add/rename?
2. **SENSEX historical** — accept current gap (only ~2 manually-pulled days), or buy historical data before starting backtests?

Reply "sign off + add X / drop Y" and ingestion builds next.
