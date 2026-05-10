# ThetaDesk Handbook · v0.2

Reference doc for the ThetaDesk trading dashboard. Captures *what was asked, what was built, how it works, and what's next*. Read this first when picking the project back up.

**Built across:** 7-May to 8-May 2026
**Last commit at time of writing:** `9dce9e3` on branch `feat/thetadesk-dashboard-v0.2`
**PR:** https://github.com/ATSDashboard/ThetaBackTest/pull/1

---

## 1. What this is

A self-hosted FastAPI + Tailwind/Alpine.js trading-cockpit web app for Indian index option sellers. Designed around Rohan's deep-OTM strangle + risk-tier overlay + manipulation-window harvest playbook (codified as Sections 9O-9T in `STRATEGY_LIVE.md`).

Runs locally on `http://localhost:8000`, pulls live market data via Kite Connect, persists daily snapshots to disk, accepts position uploads from Google Sheet / JSON / manual entry.

---

## 2. How to launch

```bash
cd "05 - Backtest Engine (separate)"

# Daily kite session (token expires at ~6 AM IST every day):
#   Either via terminal:
python3 scripts/kite_login.py
#   OR via the dashboard's built-in Kite Login flow (preferred — open the dashboard and click the amber Kite-expired banner)

# Then run the server:
python3 -m uvicorn dashboard.server:app --port 8000

# Optional: auto-open browser tab on launch (useful first-run-of-the-day, noisy during dev)
THETADESK_AUTOOPEN=1 python3 -m uvicorn dashboard.server:app --port 8000
```

Server logs to stdout. Health: `http://localhost:8000/api/health` returns `{kite_alive, ist_time, weekday}`.

---

## 3. Pages (sidebar order)

### 3.1 Report (`/report`)

Live + Historical positions tracker. **Primary tab** — what you open first every day.

**Live mode** (auto-refresh every 15s):
- 4-KPI strip: Live P&L · Max Profit @ Expiry · Margin Used · Yield/Cr (target ₹5K, floor ₹3K)
- Bucket-grouped MTM strip (A · B2 · B1 · Lottery)
- Positions table:
  - Aggregated view (default): same-strike fills weighted-avg combined into one row, click to drill down by broker/demat
  - "All fills" toggle to see every individual entry
  - Filters: bucket / instrument / status / sort by MTM/strike/lots/cushion/dist
  - Columns: Instrument · Expiry · Strike · CE/PE · Lots · Avg ₹ · LTP ₹ · Dist% · σ · MTM · Status · Note
  - **EXPIRED badge** for past-expiry positions (auto-detected, settled at avg×qty)
  - **Stale warning banner** if LTP/avg ratio is wildly off
- Side panel: Pin Map (SENSEX+NIFTY spot, max-pain, expiry, VIX), Action items, Stats breakdown
- Action buttons: + Add (single-leg modal) · 📥 Import (Sheet/JSON/Manual/Demo) · 💾 Save day · ↻ Refresh

**Historical mode**:
- Date dropdown showing all saved snapshots
- Same KPI strip + table but pricing frozen at snapshot time (no re-fetch from chain)
- Day Context card: O/H/L for both indices, VIX, save timestamp, note
- Delete button per snapshot

### 3.2 Strangle Strategy (`/recommend/{NIFTY|SENSEX}`)

Auto-generated 3-tier recommendations + 7-preset strategy generator. Live editable with up/down arrows.

**Top context bar:** spot · max-pain · VIX · regime · DTE · 1σ band · IST clock

**Verdict bar:** time-of-day verdict score 0-100 (e.g. "GO 100 — E-0 sweet spot 9:17-9:22") per Section 9O.

**Section 1 — 3 Tier Layered Recommendation** (Bucket A · B2 · B1, stacked):
- Each tier card has CE block · PE block · Combined block · Why+Actions block
- **CE/PE strikes are editable**: −/+ arrows step by 1 grid (50 NIFTY / 100 SENSEX), or type any strike
- **Per-tier expiry dropdown** (next 3 weeklies) — switching re-prices same strikes against new chain
- **↺ Reset button** restores API-recommended values
- Edits persist across the 15s auto-refresh (stored in `tierEdits` state)
- Live recompute on every change: combined premium, ₹/Cr, max profit, status badge

**Section 2 — Strategy Generator** with 7 presets:

| Preset | Distance | Capital | Hit% | Timing |
|---|---|---|---|---|
| 🛡 E-0 Bucket A · Deep OTM | 2.7% | 95% | 96% | E-0 9:17-9:22 |
| ⚖ E-0 Bucket B2 · Mid | 1.0% | 5% | 85% | E-0 9:17-9:22 |
| 🎯 E-0 Bucket B1 · ATM Straddle | 0.3% | 5% | 55% | E-0 9:45-10:15 only · close 12:00 |
| 📅 E-1 Early · 10 AM (legacy) | 4.0% | 5% | 92% | E-1 10:00 |
| 📅 E-1 Late · 2:45+ PM | 4.0% | 5% | 85% | E-1 14:45-15:15 only |
| 🧊 Iron Condor · capped risk | 2.5% sells, 4% wings | 10% | 85% | any |
| 🎟 Lottery Buy · Harvest | 4.5-6.5% | 1% | 30% | E-0 14:00-14:30 |

Each preset card shows: legs · combined premium · margin · **probability of profit** (color-coded) · max profit · yield/Cr (vs floor/ideal) · breakevens (lower/upper) · timing window. Same editable strike + expiry + reset controls. **One-click Send to Report** (append or replace).

**Section 3 — Visual price ladder:** SVG rendering of spot, max-pain, 1σ band, top 3 OI walls per side, all tier strikes color-coded.

### 3.3 Harvest Strategy (`/manipulation/{instrument}`)

SENSEX manipulation-window playbook (per Section 9M).

**Phase clock** at top: WAIT (<13:30) → PREP (13:30-14:00) → BUY (14:00-14:30) → LADDER (14:30-15:00) → CATCH (15:00-15:25, pulses red) → CLEANUP (15:25+) → off-day

**Playbook reminder card** with the 4-phase Section 9M rules

**Spike-ripe candidates table:** filtered to 4-6.5% OTM with OI < 200K and LTP < ₹5. Per row: side · strike · dist% · LTP · OI · vol · ripeness score (🔥 HIGH / MED / LOW) · Premium↑% (heuristic) · suggested buy lots @ ₹12.5K budget · sell-limit at 12× and 8× · expected payoff if hit

**Sell-Limit Ladder Calculator:** input buy LTP + lots + lot size + distribution (Even/Aggressive/Conservative) → outputs 3-tier ladder at 5×/8×/12× with per-tier payoff

### 3.4 Chain (`/chain/{NIFTY|SENSEX}`)

Sensibull-style option chain with strategy builder.

**Top:** SENSEX/NIFTY toggle pill + ±% distance input + refresh

**Left (chain table):** sticky header, all strikes ±5% from spot, columns CE Vol · CE OI · CE LTP · **B/S buttons** · Strike (bold) · Dist · **B/S buttons** · PE LTP · PE OI · PE Vol. ATM strike highlighted blue. Clicking `B` (BUY) or `S` (SELL) on any row adds a leg to the builder; clicking again on same leg bumps qty.

**Right side panel — Strategy Builder:**
- Presets: Strangle ±2.5% · Iron Condor · Straddle ATM
- Active legs list with editable qty per leg (in lots)
- Live aggregates: net premium, total credit/debit, margin (with strangle SPAN offset), **yield/Cr** (color-coded), max profit, lower/upper breakeven, risk label (limited/spread/naked), total lots
- "Append to Report" / "Replace & Send" buttons → pushes legs to localStorage with `broker: 'builder'`, `note: 'from chain builder'`

---

## 4. Strategy Rulebook (the source of truth)

Lives at `STRATEGY_LIVE.md`. Sections 9O-9T were added/locked this session — every recommendation in the dashboard derives from these.

| Section | What it codifies |
|---|---|
| **9O** | E-1 timing rules: NEWS-RISK window 9:15-14:45 = NO entry · optimal 14:45-15:15 only · ≥3.5% OTM · per-Cr ≥ ₹7.5K floor |
| **9P** | 6/7-May incident lesson notes (war-pause news spike) |
| **9Q** | "Ultra-safe" is an EARNED label · regime-graded distance (assess VIX/range/walls/events first, then call it ultra-safe) |
| **9R** | NEVER skip on tradable day · per-Cr floors (E-0 ₹3K floor / ₹5K ideal · E-1 ₹7.5K / ₹10K) |
| **9S** | Corrected tier capital allocation: 95% Bucket A / 5% Bucket B (with B1 ATM opportunistic only) |
| **9T** | **Canonical Navin Group rulebook hardcoded** — full section with all constants now in `dashboard/server.py` |

Constants encoded in `dashboard/server.py`:
```python
LOT_SIZE              = {"NIFTY": 75, "SENSEX": 20}
GRID                  = {"NIFTY": 50, "SENSEX": 100}
MARGIN_PER_LOT_E0     = {"NIFTY": 235000, "SENSEX": 250000}
LOTS_PER_CR           = {"NIFTY": 43, "SENSEX": 40}
PREM_PER_CR_E0_FLOOR  = 4000   # min — escalate below
PREM_PER_CR_E0_IDEAL  = 5000   # standard target
PREM_PER_CR_E0_FULL_QTY = 6000 # premium override → fire full quantity
PREM_PER_CR_FLOOR_MIN   = 7500 # E-1 carry minimum
PREM_PER_CR_FLOOR_IDEAL = 10000# E-1 carry ideal
SL_DISTANCE_PTS       = {"NIFTY": 150, "SENSEX": 500}
SL_HARD_CLOSE_PCT     = 0.5    # within 0.5% of strike → HARD CLOSE
SL_RETHINK_PCT        = 1.0    # within 1% → rethink
```

Margin uses SPAN strangle offset: `max(CE shorts, PE shorts) × ₹2.5L × 0.90` — calibrated against broker-shown ₹83 Cr on 7-May (was naked-summed to ₹176 Cr before fix).

---

## 5. Data model

### Position object (each fill is atomic)
```json
{
  "instrument": "SENSEX",
  "expiry":     "2026-05-07",     // ← REQUIRED (else defaults to next weekly)
  "strike":     80000,
  "side":       "CE",             // CE | PE
  "qty":        -21280,            // negative = SHORT, positive = LONG. Total shares.
  "avg_price":  2.41,
  "broker":     "Monarch",        // optional
  "demat":      "M-001",          // optional
  "time":       "2026-05-07 09:30",// optional
  "note":       "Bucket A"        // optional
}
```

Multiple fills on same `(instrument, expiry, strike, side, sign(qty))` → frontend aggregates with weighted avg, drill-down preserves individual fills.

### Snapshot file (Historical view)
`data/dashboard_snapshots/{YYYY-MM-DD}.json` (gitignored). Created via:
- 💾 Save day button (live → saves with current market context)
- Direct JSON write (used to seed historical days from screenshots)

Schema:
```json
{
  "date": "2026-05-07",
  "saved_at": "2026-05-07 15:35:00",
  "weekday": "Thursday",
  "note": "...",
  "market": {SENSEX: {spot, open, high, low, prev_close}, NIFTY: {...}, vix},
  "positions": [...],
  "analysis": [...],   // pre-computed (positions enriched with LTP, MTM, recommendation)
  "summary": {total_mtm, total_lots, n_short, n_long, n_action_needed, n_stale, n_expired,
              total_margin, total_max_profit, total_premium_paid,
              yield_per_cr, yield_per_cr_at_expiry, margin_per_cr, margin_breakdown}
}
```

### Sheet schema (Google Sheet ingestion)
| instrument | expiry | strike | side | qty | price | broker | demat | time | note |
|---|---|---|---|---|---|---|---|---|---|

See `dashboard/POSITIONS_INGEST_GUIDE.md` for full team-facing docs.

---

## 6. Backend endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Kite session status |
| GET | `/api/snapshot` | Live spot/VIX/E-0 flags for both indices |
| GET | `/api/chain/{instrument}` | Option chain ±X% around spot |
| GET | `/api/strategy/{instrument}` | Tiered strangle recommendations (legacy structure) |
| GET | `/api/recommend/{instrument}` | Asymmetric strangle picker (bias-aware) |
| GET | `/api/recommendations/{instrument}` | 3-tier card data (used by /recommend page) |
| GET | `/api/manipulation/{instrument}` | Harvest phase + spike candidates |
| GET | `/api/timing/{instrument}` | Trade-window verdict (per Section 9O/9R) |
| GET | `/api/holidays` | Upcoming market holidays |
| GET | `/api/expiries` | Next 3 weeklies for each index |
| GET | `/api/kite-login-url` | Kite OAuth URL |
| POST | `/api/kite-exchange` | Exchange request_token → access_token |
| POST | `/api/position-analysis` | **THE workhorse**: input positions array → output positions+aggregated+summary with live MTM, recommendations, expired/stale flags |
| POST | `/api/import/google-sheet` | Fetch CSV from URL, parse, return positions |
| POST | `/api/snapshot/save` | Save current state to dated JSON |
| GET | `/api/snapshots` | List all saved snapshots with metadata |
| GET | `/api/snapshot/{date}` | Get specific date |
| DELETE | `/api/snapshot/{date}` | Delete |

---

## 7. Design system

| Token | Value | Where |
|---|---|---|
| Body | Inter 13px, -0.01em tracking | global |
| Numbers | JetBrains Mono, tabular-nums | `.num` class |
| Spacing | 8px base grid | global |
| Background | `--bg` (#fafafa light / #0a0a0a dark) | base.html `:root` |
| Surface | `--surface` (#ffffff / #141414) | cards |
| Border | `--border` (#e5e5e5 / #262626) | hairlines |
| Text | `--text-1/2/3` (primary / secondary / muted) | hierarchy |
| Color signals | green/red ONLY for P&L · indigo for actions · amber for warn | strict |
| Radius | 6px controls / 8px cards | global |
| Active state | Solid `--text-1` background, inverted text (Linear-style) | sidebar |

Sidebar = 200px text-only (no icons). Top status strip Bloomberg-style — single line of always-visible market state. Dark mode toggle in sidebar bottom; persists in localStorage.

---

## 8. Session history — what was asked, what was built

In rough chronological order:

### Day 1 — Initial dashboard build
- **Asked:** "build a UI/UX to use on my local system. one with the live data pulled & giving me all the basic stuff we normally use. nice clean design - light & dark mode options. interactive"
- **Built:** FastAPI + Jinja2 + Tailwind/Alpine scaffolding. /api/snapshot, /api/chain, /api/strategy, /api/manipulation endpoints. Initial pages (home, chain, manipulation, strategy).

### Day 1 — Trade-Now verdict + premium-rise heuristic
- **Asked:** "may be somewhere add time suggestion & should you take trade right now or not suggestion. premium inc probability"
- **Built:** `_trade_verdict()` with score 0-100 + label + action mapped to time-of-day windows. `/api/timing/{instrument}` endpoint. Premium↑% heuristic per strike based on cushion / VIX / spot drift / manipulation window.

### Day 2 — Calibrated rulebook
- **Asked:** Various corrections — "5% is too far for E-1", "₹7,500/Cr min, ₹10K+ ideal", "ultra-safe is min 3.5%+ on stable days but further on volatile"
- **Built:** STRATEGY_LIVE.md Sections 9O (E-1 timing) and 9R (per-Cr floors). `/api/recommend/{instrument}` with bias-aware asymmetric distances.

### Day 2 — Lessons from 6/7-May war-spike incident
- **Asked:** Real trades on 6-May got whipsawed by news at 14:00. Need rulebook update.
- **Built:** Section 9P documenting the incident. Section 9O encoded: news-risk window 9:15-14:45 = no entry, optimal 14:45-15:15 only.

### Day 2 — Margin reality vs theory
- **Asked:** "margin used coming 180 something cr, we know that it was 83 something today"
- **Fixed:** SPAN strangle offset — max(CE shorts, PE shorts) × margin × 0.90. Now matches broker-shown margin within ~1%.

### Day 2 — Capture % → Yield/Cr
- **Asked:** "capture not required... add yield per CR. coz that is important that we get upto 5000 per cr avg"
- **Built:** Replaced Capture % KPI with Yield/Cr (now/at-expiry) color-coded vs ₹3K floor / ₹5K target.

### Day 2 — Aggregation + drill-down + Sheet ingestion
- **Asked:** "same strikes you can combine the qty & do weighted average. & then if i want i can click on it to drill down trade wise. also suggest entry system: screenshot, Google Sheet, broker, Sensibull"
- **Built:**
  - Aggregated view (group by instrument+strike+side+sign) with weighted avg
  - Click row to drill down to per-broker/per-demat fills
  - `/api/import/google-sheet` endpoint (auto-converts share URLs)
  - `dashboard/POSITIONS_INGEST_GUIDE.md` for the team
  - Manual entry tab (multi-row form)

### Day 2 — Live Dashboard tab + Kite login
- **Asked:** "add the dashboard / live trades section in our tool also"
- **Built:** `/live` page (later renamed `/report`) with positions tracker. Kite login flow built into UI (no terminal needed) — session token exchange via `/api/kite-exchange`.

### Day 2 — Visual redesign
- **Asked:** "tool feels amateurish, like a blog. make it nice & clean like [Dribbble EdgesPay reference]. become an expert UI/UX developer for finance web apps"
- **Built:** Stripe/Linear-inspired design system. Dropped emojis/orbs/gradients. 200px text-only sidebar. Bloomberg-style top status strip. Hairline borders, JetBrains Mono numbers, 8px grid. Iterated through 3 visual iterations.

### Day 2 — Report tab with Live + Historical
- **Asked:** "rename to Report & have a Live & historical view. store all days historical also. cna even date wise"
- **Built:** `/report` page with toggle. Snapshot save/list/delete endpoints. Date-picker for historical view. Day Context card for past days.

### Day 2 — Total margin + max profit + yield in main dashboard
- **Asked:** "in live - i need total margin used, max profit, live p/l etc also in the main dashboard"
- **Built:** 4-tile KPI strip on Report (Live P&L · Max Profit · Margin · Yield/Cr).

### Day 2 — Renaming for clarity
- **Asked:** "rename to Strangle Strategy & Harvest Strategy"
- **Done:** Sidebar tabs renamed.

### Day 2 — Manual entry option
- **Asked:** "& also option for manual entry in the report section"
- **Built:** Manual entry tab in Import modal — multi-row form with all fields including expiry.

### Day 2 — Stale + expired position detection
- **Asked:** Reported "report data became absolutely rubbish" the morning after expiry
- **Diagnosed:** positions from yesterday's expiry being priced against next-week's chain
- **Built:**
  - Stale detection (LTP/avg ratio ≥ 10) → amber warning banner + Clear All
  - Then full fix: positions now carry `expiry: YYYY-MM-DD`. Backend groups by (instrument, expiry), fetches per-expiry chain. Past expiries auto-settle at LTP=0 with full credit/loss.

### Day 2 — Editable strikes everywhere
- **Asked:** "in strategy section give up down arrows next to strike or even editable cell where i can change the strike & see the premium & other things. & also option to choose the expiry date & then refresh back to recommended or something type of option for each bucket"
- **Built:**
  - Strategy Generator preset card: editable strike + arrows + lots input + expiry + Reset
  - Then extended: 3 tier cards (Bucket A/B2/B1) all got the same controls
  - Edits persist across 15s auto-refresh

### Day 2 — Historical 7-May snapshot loaded
- **Asked:** "historical data is not loaded. whatever u have from my live trades. load here"
- **Built:** `data/dashboard_snapshots/2026-05-07.json` with 26 SENSEX positions, all marked EXPIRED, ~₹4.12L net day matching the actual ~₹4.17L

### Day 2 — Polish fixes
- "format properly. cant see the full strike. digits getting hidden behind arrows" → widened input + hid native number-spinner globally
- "so many decimals not required in points away" → rounded points to integer, % to 1 decimal
- "every time u make a change y does a new tab with the changes auto-open?" → made auto-open opt-in via `THETADESK_AUTOOPEN=1`

---

## 9. Files in the repo (this work)

```
05 - Backtest Engine (separate)/
├── STRATEGY_LIVE.md                              # canonical rulebook (9O-9T added)
├── FINDINGS_LOG.md                               # 7-May expiry day live results entry
├── dashboard/
│   ├── server.py                                 # all endpoints + verdict + recommendations + presets
│   ├── __init__.py
│   ├── POSITIONS_INGEST_GUIDE.md                 # team-facing data entry guide
│   ├── THETADESK_HANDBOOK.md                     # ← THIS FILE
│   └── templates/
│       ├── base.html                             # design system, sidebar, top strip
│       ├── report.html                           # Live + Historical positions
│       ├── recommend.html                        # 3-tier cards + 7-preset generator
│       ├── manipulation.html                     # Harvest Strategy
│       └── chain.html                            # SENSEX+NIFTY chain + builder
├── lib/
│   ├── expiry_calendar.py                        # NIFTY+SENSEX 2024-26 weekly calendar
│   ├── kite_live.py                              # Kite Connect live data adapter
│   └── kite_historical.py                        # historical data fetcher
├── scripts/
│   └── kite_login.py                             # daily token refresh (CLI fallback)
└── data/
    └── dashboard_snapshots/                      # gitignored — daily JSON files
        └── 2026-05-07.json                       # seeded from 7-May screenshots
```

---

## 10. Known issues / things that didn't ship

| Item | Status |
|---|---|
| Real intraday MTM curve in Report (replace placeholder bars) | placeholder still |
| Auto-poll Google Sheet every 60s | manual fetch only |
| Chain page: changeExpiry doesn't actually fetch a different expiry's chain (`/api/chain` doesn't accept expiry param yet) | uses default chain — workaround needed for non-current expiry |
| Spike-harvester research backtest | agent stalled with one critical insight: "Even ORACLE basket of ₹0.05-0.30 strikes only spikes 27.5% of expiries — the lottery thesis as designed doesn't match data; spikes happen on ₹0.40-1.50 LTP strikes" — needs re-launch with tighter scope |
| Kite Connect positions API (auto-pull holdings) | not wired — user trades on Axis/Monarch which don't have Kite-style API |
| Direct Sensibull integration | no public portfolio API |
| Auto-fire to broker | "💸 Fire" buttons disabled in UI — ticket-copy works |
| Greeks per leg + payoff curve in Chain Builder | basic premium/margin/breakeven only |
| Per-position drawer with full reasoning + close action on row-click | basic remove (×) only |
| Keyboard shortcuts (`a` add, `r` refresh, `/` filter focus) | not added |

---

## 11. Operations checklist

### Daily (every trading day)
1. **~9:00 AM** — refresh Kite session via dashboard's amber Kite-expired banner (or `python3 scripts/kite_login.py`)
2. **~9:15 AM** — open `/recommend/SENSEX` (or NIFTY on Tuesdays); read the verdict bar; pick a preset OR use the auto-recommended Bucket A
3. **As trades fire** — team updates Google Sheet OR Rohan adds via Manual entry tab. Each fill should have **expiry** populated.
4. **Watch `/report`** for live MTM, action items, stale warnings
5. **End of day** — click 💾 Save day to persist snapshot; clear positions (if expired) or carry to next day if held

### Weekly
- Review `STRATEGY_LIVE.md` for any rule amendments after the week's trades
- Append findings to `FINDINGS_LOG.md` (one line per analysis)

### Per significant trade event
- Add to STRATEGY_LIVE.md Section 9X if it teaches a new rule

---

## 12. Reference quotes from the user (canonical)

These are the rules that came directly from Rohan during the build, hardcoded into the system:

> "ultrasafe band for e0 on non-event days is 2.5%+"

> "we will only take e-1 trades for getting bare min rs 7500 per cr & ideally 10000+ per cr of margin (& that too not the e-1 day margin) - the e0 margin which is almost 1.5 to 1.7 times e1 margin. usually expiry day sensex 40 to 41 lots = 1 cr margin & nifty 42 to 43 lots equal 1 cr"

> "THERE IS NEVER AN OPTION OF NOT TAKING TRADE THAT DAY. its like losing free money. so we try & get the best possible premium (ideally atleast 90% of non spot moved peak) but if we fail (which we shouldnt) then we have to take the trade & get our absolute bare min of rs 3000 per cr. but ideally we should hget rs 5000 per & then additional kicker through riskier trades"

> "79100 & 79200 is our mid risk strategy- we have to take that with upto 10% capital"

> "for e-1 5% seems too far considering the vix & the relative stable market with no news round the corner & premium is very very low"

> "e-1 trade should be much further & also after 2:45 only" (post-6-May war-spike incident)

> "ultra ultra safe strikes ideally min 3.5% away but can be further on more volatile days"

> "dont just say ultrasafe for the sake of it. 2.5% is the min rule. if we feel market has some more volatility or event or other - go further. so ultrasafe has to really be almost zero chance of expiry (min 2.5% away) with reasonable premiums"

> "capture not required coz it will keep changing & you ar eonly sayiny 98.3 coz you have put last price as 0,05. but actually i let it expire so for me the price is actually 0. & instead of capture - add yield per CR"

> "ensure to take the full option na. as in with the expiry. the full option symbol or instrument. not jsyt the strike & sensex or nifty. as in take expiry date also so this mistake is not made"

---

*This handbook is the single source of truth for "what was built, why, and how to use it". Update it when significant features change.*
