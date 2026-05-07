# ThetaDesk · Positions Ingestion Guide

How to feed daily fills into the Live Report tab.

There are **3 ways** to get positions into the dashboard, ranked by ease for the team:

---

## 1. 📊 Google Sheet (recommended for team workflow)

The team updates a single Sheet throughout the day. The dashboard fetches it on demand.

### Sheet template

Create a Google Sheet with **these exact headers in row 1** (case-insensitive):

| instrument | strike | side | qty   | price | broker  | demat  | time             | note            |
|------------|--------|------|-------|-------|---------|--------|------------------|-----------------|
| SENSEX     | 80000  | CE   | -21280| 2.41  | Monarch | M-001  | 2026-05-07 09:30 | Bucket A · Deep |
| SENSEX     | 80000  | CE   | -45000| 2.48  | Axis    | A-001  | 2026-05-07 09:32 | Bucket A · Deep |
| SENSEX     | 76000  | PE   | -32000| 2.42  | Axis    | A-001  | 2026-05-07 09:33 | Bucket A · Deep |
| SENSEX     | 79100  | CE   | -1000 | 8.35  | Axis    | A-002  | 2026-05-07 14:00 | Mid Risk        |
| SENSEX     | 82500  | CE   | +500  | 0.45  | Monarch | M-001  | 2026-05-07 14:25 | Lottery harvest |

### Field rules
- **instrument** — `SENSEX` or `NIFTY` (case-insensitive)
- **strike** — integer (e.g. `80000`)
- **side** — `CE` or `PE`
- **qty** — **negative for SHORT** (sells), **positive for LONG** (buys). Total shares, not lots.
- **price** — average fill price (₹/share)
- **broker** *(optional)* — `Axis` / `Monarch` / etc. Free text; used for drill-down grouping.
- **demat** *(optional)* — your account ID. Multiple dematS per broker supported (`A-001`, `A-002`, `A-003`).
- **time** *(optional)* — fill timestamp (`YYYY-MM-DD HH:MM` recommended)
- **note** *(optional)* — `Bucket A` / `Mid Risk` / `Lottery` / etc.

### How to share the sheet

**Option A — Anyone with link can view (simplest):**
1. Create Sheet, add headers + data
2. File → Share → "Anyone with the link" → Viewer
3. Copy the URL, paste into ThetaDesk Import → Google Sheet
4. The dashboard will auto-extract the CSV from the share URL

**Option B — Publish to web (more reliable, no auth issues):**
1. File → Share → Publish to web → "Comma-separated values (.csv)"
2. Copy the published URL
3. Paste into ThetaDesk Import

### How to import in dashboard
1. Click **📥 Import** in the Report header
2. Tab: **Google Sheet**
3. Paste URL
4. Choose **Append** (add to existing) or **Replace** (clear first)
5. Click **Fetch & import**

The URL is remembered in localStorage, so next time it's pre-filled. To re-pull intraday updates, just open Import → click "Fetch & import" again.

---

## 2. 📋 JSON Paste (for bulk one-off loads)

If you have positions in another tool (Sensibull export, broker download, etc.), convert to JSON:

```json
[
  {"instrument":"SENSEX","strike":80000,"side":"CE","qty":-21280,"avg_price":2.41,"broker":"Monarch","demat":"M-001"},
  {"instrument":"SENSEX","strike":76000,"side":"PE","qty":-32000,"avg_price":2.42,"broker":"Axis","demat":"A-001"}
]
```

Then: Import → Paste JSON → paste → Import.

`avg_price` (in JSON) maps to `price` (in Sheet). `broker`, `demat`, `time`, `note` all optional.

---

## 3. ✏️ Manual single-leg add

For one-off additions during the day:
- Click **+ Add** in Report header
- Fill the modal (Instrument, Strike, Side, Direction, Qty, Avg price, optional broker/demat/note)
- Click Add

---

## How aggregation works

When you have **multiple fills on the same strike+side** (e.g. 80,000 CE shorts across 3 brokers), the table shows them as **one combined row** with weighted average.

Example: above sample sheet shows 3 fills on 80,000 CE → combined as:
```
SENSEX  80,000 CE  3,464 lots @ ₹2.58 (weighted avg)  ▶
```

Click the row to expand and see individual fills:
```
└─ Monarch  M-001  09:30  -1,064 lots @ ₹2.41
└─ Axis     A-001  09:32  -2,250 lots @ ₹2.48
└─ Monarch  M-002         -150  lots @ ₹5.25  · 6-May carry
```

Toggle between **Combined** (default) and **All fills** views in the table header.

---

## Daily workflow for the team

1. **Morning** — at each fill, the team adds a row to the Google Sheet with broker/demat/time
2. **Throughout day** — Rohan opens dashboard, hits Import → Fetch
3. **Mid-day** — re-import to sync intraday additions/closes
4. **End of day** — Rohan clicks **💾 Save day** to persist the snapshot to the Historical view (browsable forever)
5. **Next day morning** — clear positions (or replace mode), fresh sheet

---

## Coming next

- **Auto-poll mode** — dashboard pings the sheet every 60s, updates positions automatically
- **Multi-sheet** — separate sheets per broker, dashboard merges them
- **Direct broker API** — Kite Connect for Zerodha, others as APIs become available
- **Sensibull import** — if Sensibull adds an export API, we'll plug in directly
