"""
Trading Dashboard — Phase 1 MVP

Local FastAPI server. Run with:
    python3 -m uvicorn dashboard.server:app --reload --port 8000

Then open http://localhost:8000 in your browser.

Pulls live data via lib/kite_live.py — needs valid Kite session.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta
from pathlib import Path
import sys
from functools import lru_cache
import time as time_mod

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import pandas as pd
import pytz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── App setup ────────────────────────────────────────────────────────────
app = FastAPI(title="Trading Dashboard", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
# Disable Jinja2 template cache to avoid hashable-key bug in jinja2 3.1.6
templates.env.cache = None

IST = pytz.timezone("Asia/Kolkata")

# ── Trading constants ────────────────────────────────────────────────────
LOT_SIZE = {"NIFTY": 75, "SENSEX": 20}
GRID = {"NIFTY": 50, "SENSEX": 100}
# E-0 margin per lot for deep-OTM strikes (per Rohan's broker reality):
#   SENSEX expiry day: 40-41 lots/Cr → ~₹2.5L/lot
#   NIFTY  expiry day: 42-43 lots/Cr → ~₹2.35L/lot
# We size on E-0 margin (the larger one) so we don't over-size when E-1→E-0.
MARGIN_PER_LOT_E0 = {"NIFTY": 235000, "SENSEX": 250000}
LOTS_PER_CR = {"NIFTY": 43, "SENSEX": 40}
SHARES_PER_CR = {"NIFTY": 43*75, "SENSEX": 40*20}   # 3225 / 800
# Per-Cr capture floors per Navin Group Canonical Rulebook (Section 9T):
# Bucket A — Deep OTM E-0 main shot:
PREM_PER_CR_E0_FLOOR    = 4000     # absolute min — escalate below
PREM_PER_CR_E0_IDEAL    = 5000     # standard target
PREM_PER_CR_E0_FULL_QTY = 6000     # premium override → fire full quantity in one shot
# Bucket B2 — Mid-Deep range:
PREM_PER_CR_B2_MIN      = 10000
PREM_PER_CR_B2_MAX      = 20000
# Bucket B1 — ATM Straddle (opportunistic only):
PREM_PER_CR_B1_MIN      = 50000
# E-1 overnight carry (held to next-day expiry):
PREM_PER_CR_FLOOR_MIN   = 7500     # E-1 carry minimum
PREM_PER_CR_FLOOR_IDEAL = 10000    # E-1 carry ideal
# SL trigger: spot within X pts of strike → manual squareoff (Rulebook 2.2)
SL_DISTANCE_PTS         = {"NIFTY": 150, "SENSEX": 500}
# Discretionary squareoff thresholds (% of spot vs strike)
SL_HARD_CLOSE_PCT       = 0.5      # within 0.5% → hard close
SL_RETHINK_PCT          = 1.0      # within 1.0% → rethink
SL_SPOT_MOVED_PCT       = 1.0      # spot moved >1% from entry → rethink
# Backwards compat
MARGIN_PER_LOT = 175000          # legacy E-1 average; not used in new sizing

# ── Tiny in-memory cache (5-second TTL on heavy calls) ──────────────────
_cache = {}
def cached(key: str, ttl_sec: float, fn):
    now = time_mod.time()
    if key in _cache:
        ts, val = _cache[key]
        if now - ts < ttl_sec:
            return val
    val = fn()
    _cache[key] = (now, val)
    return val


def kite_alive():
    """Returns True if Kite session looks good."""
    try:
        from lib.kite_live import _kite
        _kite().profile()
        return True
    except Exception:
        return False


# ── API Endpoints ────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {
        "kite_alive": kite_alive(),
        "ist_time": datetime.now(IST).strftime("%H:%M:%S"),
        "ist_date": datetime.now(IST).strftime("%Y-%m-%d"),
        "weekday": datetime.now(IST).strftime("%A"),
    }


# ── Kite login: one-click flow ──────────────────────────────────────────
@app.get("/api/kite-login-url")
def kite_login_url():
    """Return the Kite login URL for opening in a new tab."""
    try:
        from kiteconnect import KiteConnect
        import json
        cred_path = Path.home() / ".config" / "kite_credentials.json"
        cred = json.loads(cred_path.read_text())
        k = KiteConnect(api_key=cred["api_key"])
        return {"url": k.login_url()}
    except Exception as e:
        raise HTTPException(500, f"Failed to build login URL: {e}")


@app.post("/api/kite-exchange")
async def kite_exchange(request: Request):
    """Exchange request_token (or full redirect URL) for access_token + save session."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    raw = (body.get("request_token") or body.get("url") or "").strip()
    if not raw:
        return JSONResponse({"success": False, "error": "Missing request_token or url"}, status_code=400)
    # Extract token from URL if present
    import re
    m = re.search(r"request_token=([^&\s]+)", raw)
    request_token = m.group(1) if m else raw
    try:
        from kiteconnect import KiteConnect
        import json
        cred_path = Path.home() / ".config" / "kite_credentials.json"
        cred = json.loads(cred_path.read_text())
        k = KiteConnect(api_key=cred["api_key"])
        s = k.generate_session(request_token, api_secret=cred["api_secret"])
        out = {
            "access_token": s["access_token"],
            "api_key": cred["api_key"],
            "user_id": s.get("user_id"),
        }
        sess = Path.home() / ".config" / "kite_session.json"
        sess.write_text(json.dumps(out))
        sess.chmod(0o600)
        _cache.clear()   # bust cached responses tied to old session
        return {"success": True, "user_id": s.get("user_id")}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.get("/api/snapshot")
def snapshot():
    """Top-level KPIs for both NIFTY + SENSEX + VIX."""
    if not kite_alive():
        return {"error": "Kite session expired. Run scripts/kite_login.py", "ist_time": datetime.now(IST).strftime("%H:%M:%S")}

    def _pull():
        from lib.kite_live import _kite
        from lib.expiry_calendar import is_e0, is_e1, nearest_weekly_expiry_after, MARKET_HOLIDAYS, is_market_holiday
        k = _kite()
        out = {"ist_time": datetime.now(IST).strftime("%H:%M:%S"),
               "ist_date": datetime.now(IST).strftime("%Y-%m-%d")}
        today = datetime.now(IST).date()
        out["holiday"] = is_market_holiday(today) or ""

        for inst in ["NIFTY", "SENSEX"]:
            sym = "NSE:NIFTY 50" if inst == "NIFTY" else "BSE:SENSEX"
            q = k.quote([sym])[sym]
            spot = q["last_price"]
            prev = q["ohlc"]["close"]
            opn = q["ohlc"]["open"]
            hi = q["ohlc"]["high"]
            lo = q["ohlc"]["low"]
            change_abs = spot - prev
            change_pct = change_abs / prev * 100 if prev else 0
            gap_pct = (opn - prev) / prev * 100 if prev else 0
            day_range_pct = (hi - lo) / opn * 100 if opn else 0
            out[inst] = {
                "spot": round(spot, 2),
                "prev": round(prev, 2),
                "open": round(opn, 2),
                "high": round(hi, 2),
                "low": round(lo, 2),
                "change_pct": round(change_pct, 2),
                "gap_pct": round(gap_pct, 2),
                "day_range_pct": round(day_range_pct, 2),
                "is_e0": is_e0(today, inst),
                "is_e1": is_e1(today, inst),
                "next_expiry": str(nearest_weekly_expiry_after(today, inst) or ""),
            }
        # VIX
        vix = k.quote(["NSE:INDIA VIX"])["NSE:INDIA VIX"]
        out["vix"] = round(vix["last_price"], 2)
        out["vix_change"] = round(vix["last_price"] - vix["ohlc"]["close"], 2)
        return out

    return cached("snapshot", 5, _pull)


@app.get("/api/chain/{instrument}")
def chain(instrument: str, distance_pct: float = 5.0):
    """Option chain ±distance_pct% around spot for nearest weekly expiry."""
    if not kite_alive():
        raise HTTPException(401, "Kite session expired")
    instrument = instrument.upper()
    if instrument not in ("NIFTY", "SENSEX"):
        raise HTTPException(400, "instrument must be NIFTY or SENSEX")

    def _pull():
        from lib.kite_live import _kite, _instruments
        from lib.expiry_calendar import nearest_weekly_expiry_after
        k = _kite()
        sym = "NSE:NIFTY 50" if instrument == "NIFTY" else "BSE:SENSEX"
        spot = k.quote([sym])[sym]["last_price"]
        grid = 50 if instrument == "NIFTY" else 100
        today = datetime.now(IST).date()
        next_exp = nearest_weekly_expiry_after(today, instrument)
        if not next_exp:
            return {"error": "no_expiry"}

        seg = "NFO" if instrument == "NIFTY" else "BFO"
        instr_dump = pd.DataFrame(_instruments() if instrument == "NIFTY" else k.instruments(seg))
        # filter chain
        def _td(x):
            if x in (None, '', '1970-01-01'): return None
            try: return pd.to_datetime(x).date()
            except: return None
        instr_dump['expiry'] = instr_dump['expiry'].apply(_td)
        chain_df = instr_dump[(instr_dump['name'] == instrument) &
                              (instr_dump['expiry'] == next_exp) &
                              (instr_dump['instrument_type'].isin(['CE','PE']))]
        # build strikes within ±distance
        lo_strike = round(spot * (1 - distance_pct/100) / grid) * grid
        hi_strike = round(spot * (1 + distance_pct/100) / grid) * grid
        chain_df = chain_df[(chain_df['strike'] >= lo_strike) & (chain_df['strike'] <= hi_strike)]
        # pull quotes in batches
        symbols = [f"{seg}:{r['tradingsymbol']}" for _, r in chain_df.iterrows()]
        prices = {}
        for i in range(0, len(symbols), 250):
            q = k.quote(symbols[i:i+250])
            for ts, v in q.items():
                base = ts.replace(f"{seg}:", "")
                row = chain_df[chain_df['tradingsymbol'] == base]
                if row.empty: continue
                s = int(row.iloc[0]['strike'])
                opt = row.iloc[0]['instrument_type']
                prices[(s, opt)] = {
                    'ltp': v['last_price'],
                    'oi': v.get('oi', 0),
                    'volume': v.get('volume', 0),
                    'open': v['ohlc']['open'],
                    'high': v['ohlc']['high'],
                    'low': v['ohlc']['low'],
                }
        # consolidate
        strikes = sorted(set(int(s) for s in chain_df['strike']))
        rows = []
        for s in strikes:
            ce = prices.get((s, 'CE'), {})
            pe = prices.get((s, 'PE'), {})
            rows.append({
                'strike': s,
                'dist_pct': round((s - spot) / spot * 100, 2),
                'ce_ltp': ce.get('ltp'),
                'ce_oi': ce.get('oi'),
                'ce_volume': ce.get('volume'),
                'pe_ltp': pe.get('ltp'),
                'pe_oi': pe.get('oi'),
                'pe_volume': pe.get('volume'),
            })
        # max pain
        pains = []
        for pin in strikes:
            p = sum((pin - s) * (prices.get((s,'CE'), {}).get('oi') or 0) for s in strikes if s < pin)
            p += sum((s - pin) * (prices.get((s,'PE'), {}).get('oi') or 0) for s in strikes if s > pin)
            pains.append((pin, p))
        max_pain = min(pains, key=lambda x: x[1])[0] if pains else None
        return {
            "instrument": instrument,
            "spot": round(spot, 2),
            "expiry": str(next_exp),
            "max_pain": max_pain,
            "max_pain_pct_from_spot": round((max_pain - spot)/spot*100, 2) if max_pain else None,
            "rows": rows,
        }

    return cached(f"chain_{instrument}_{distance_pct}", 5, _pull)


# ── Helper: market-hours + trade-window verdict ─────────────────────────
def _market_state():
    """Returns dict: market_open, status_label, minutes_to_close (or None)."""
    from datetime import time as _time
    from lib.expiry_calendar import is_market_holiday, is_trading_day
    now = datetime.now(IST)
    today = now.date()
    holiday = is_market_holiday(today)
    weekend = now.weekday() >= 5
    open_t = _time(9, 15)
    close_t = _time(15, 30)
    if holiday:
        return {"market_open": False, "status": "HOLIDAY", "label": f"Market closed — {holiday}", "minutes_to_close": None}
    if weekend:
        return {"market_open": False, "status": "WEEKEND", "label": "Market closed — weekend", "minutes_to_close": None}
    if now.time() < open_t:
        mins_until = (open_t.hour - now.hour)*60 + (open_t.minute - now.minute)
        return {"market_open": False, "status": "PRE_OPEN", "label": f"Pre-market · opens in {mins_until}m", "minutes_to_close": None}
    if now.time() > close_t:
        return {"market_open": False, "status": "AFTER_HOURS", "label": "After-hours · closed at 15:30", "minutes_to_close": None}
    mtc = (close_t.hour - now.hour)*60 + (close_t.minute - now.minute)
    return {"market_open": True, "status": "OPEN", "label": f"Open · {mtc}m to close", "minutes_to_close": mtc}


def _trade_verdict(instrument: str) -> dict:
    """Live answer to 'should I take a sell-strangle trade right now?'

    Returns: verdict, score (0-100), color, label, reason, recommended_action.
    Maps current IST to STRATEGY_LIVE.md windows.
    """
    from datetime import time as _time
    from lib.expiry_calendar import is_e0, is_e1
    ms = _market_state()
    now = datetime.now(IST)
    today = now.date()
    is_e0_today = is_e0(today, instrument)
    is_e1_today = is_e1(today, instrument)

    if not ms["market_open"]:
        return {"verdict": ms["status"], "score": 0, "color": "slate",
                "label": ms["label"], "reason": "Outside market hours",
                "action": "Plan tomorrow. Set alarms for 9:15.",
                "is_e0": is_e0_today, "is_e1": is_e1_today,
                "next_window": _next_trade_window(instrument, today)}

    t = now.time()
    if not (is_e0_today or is_e1_today):
        return {"verdict": "NO_TRADE", "score": 25, "color": "amber",
                "label": "Non-cycle day for this instrument",
                "reason": f"Today is not E-0 or E-1 for {instrument}",
                "action": "No mandate today (strategy fires E-0/E-1 only). Watch.",
                "is_e0": False, "is_e1": False,
                "next_window": _next_trade_window(instrument, today)}

    if is_e1_today:
        # Rohan's updated E-1 rule (post 6-May incident):
        # E-1 entry ONLY after 14:45 (news risk window 9:15-14:45 = no entry).
        # Distance ≥ 4% (was getting whipsawed at 3%). Per-Cr ≥ ₹7.5K still required.
        if t < _time(14, 45):
            return {"verdict": "WAIT", "score": 35, "color": "blue",
                    "label": "E-1 NEWS-RISK window — wait until 14:45",
                    "reason": "Rohan's rule: too much news risk before 14:45 (war/policy spikes whipsaw 3% strikes)",
                    "action": "Don't enter. Watch only. Window opens 14:45.",
                    "is_e0": False, "is_e1": True, "next_window": "14:45 today"}
        if t < _time(15, 15):
            return {"verdict": "GO", "score": 90, "color": "emerald",
                    "label": "E-1 ADVANCE — execute (≥4% OTM, per-Cr ≥ ₹7.5K)",
                    "reason": "14:45-15:15 = Rohan's preferred E-1 window: news digested + theta accelerated + 45 min residual risk",
                    "action": "Sell wide (≥4% OTM). Verify per-Cr ≥ ₹7,500 before placing. Hold overnight to tomorrow's E-0 close.",
                    "is_e0": False, "is_e1": True, "next_window": "now"}
        if t < _time(15, 25):
            return {"verdict": "MARGINAL", "score": 60, "color": "yellow",
                    "label": "E-1 last 10 min — only if premium meets floor",
                    "reason": "Tight window. Premium decayed further but still chargeable.",
                    "action": "Place limit only if per-Cr ≥ ₹7,500. Half-size acceptable.",
                    "is_e0": False, "is_e1": True, "next_window": "tomorrow 9:18 (E-0)"}
        return {"verdict": "SKIP_E1", "score": 15, "color": "slate",
                "label": "E-1 missed — go straight to E-0",
                "reason": "Past 15:25, market closing",
                "action": "Skip. Set alarm for E-0 tomorrow 9:18.",
                "is_e0": False, "is_e1": True, "next_window": "tomorrow 9:18 (E-0)"}

    # is_e0 — main day
    if t < _time(9, 17):
        return {"verdict": "WAIT", "score": 65, "color": "blue",
                "label": "E-0 — bid-ask too wide",
                "reason": "First 2 min open chaos. Section 9F: wait for 9:17.",
                "action": "Hold limits ready. Sweet spot 9:17-9:22.",
                "is_e0": True, "is_e1": False, "next_window": "9:17 today"}
    if t < _time(9, 22):
        return {"verdict": "GO", "score": 100, "color": "emerald",
                "label": "🔥 E-0 SWEET SPOT — execute T1+T2+T3 NOW",
                "reason": "9:17-9:22 = 100% worthless rate in 47-day backtest",
                "action": "Fire all 3 tiers. Limits at LTP × regime mult.",
                "is_e0": True, "is_e1": False, "next_window": "now"}
    if t < _time(9, 35):
        return {"verdict": "GO", "score": 90, "color": "green",
                "label": "E-0 — still within fill window",
                "reason": "9:25-9:35 limit-fill window (Section 9F)",
                "action": "Execute T1+T2+T3. Premium ~10% lower than peak.",
                "is_e0": True, "is_e1": False, "next_window": "now"}
    if t < _time(10, 30):
        return {"verdict": "MARGINAL", "score": 70, "color": "yellow",
                "label": "E-0 past optimal — Bucket A still viable",
                "reason": "Per Rulebook 2.3: full quantity if combined ≥ ₹6K/Cr",
                "action": "Take Bucket A if premium meets target. Bucket B2 9:45-10:15 if not yet placed.",
                "is_e0": True, "is_e1": False, "next_window": "SENSEX 11:00 secondary window"}
    if t < _time(11, 0):
        return {"verdict": "LATE", "score": 55, "color": "orange",
                "label": "E-0 LATE — close B-bucket, prep SENSEX secondary",
                "reason": "Past 10:30 — B-bucket profit booking starts",
                "action": "Close B1/B2. SENSEX secondary window opens at 11:00 (premium spike opportunity).",
                "is_e0": True, "is_e1": False, "next_window": "11:00 SENSEX secondary"}
    if t < _time(12, 0):
        return {"verdict": "SECONDARY_WINDOW", "score": 75, "color": "blue",
                "label": "🟦 SENSEX 11-12 SECONDARY — premium spikes possible",
                "reason": "Rulebook 2.3.3: SENSEX premium can spike with no spot move 11-12",
                "action": "If combined premium > morning levels with no spot move → take remaining Bucket A. ALL B-bucket MUST close by 12:00.",
                "is_e0": True, "is_e1": False, "next_window": "12:00 hard cutoff"}
    if t < _time(13, 0):
        return {"verdict": "POST_NOON", "score": 40, "color": "orange",
                "label": "E-0 post-noon — only Bucket A holds; prep harvest",
                "reason": "Rulebook: All B-bucket closed by 12:00. Only A remains.",
                "action": "Monitor A-bucket SL triggers (within 1% of strike = rethink, 0.5% = hard close). Prep harvest for 14:00.",
                "is_e0": True, "is_e1": False, "next_window": "harvest @ 14:00"}
    if t < _time(14, 0):
        return {"verdict": "EOD", "score": 30, "color": "orange",
                "label": "E-0 EOD — TIGHT strikes only (1-1.5%) hit ≥₹3K/Cr floor",
                "reason": "Sub-₹1 premium at 2%+. Section 9R: never skip — closer strikes still safe with DTE 0 final hour.",
                "action": "Place 1-1.5% strangles. Walls + max-pain pin protect on final hour. Then prep harvest.",
                "is_e0": True, "is_e1": False, "next_window": "harvest @ 14:00"}
    # 14:00+ on E-0 → harvest mode
    if t < _time(15, 25):
        return {"verdict": "HARVEST", "score": 60, "color": "purple",
                "label": "🎯 SWITCH TO HARVEST MODE",
                "reason": "14:00+ on E-0 = manipulation window per Section 9M",
                "action": "Go to /manipulation. Buy 5 deep-OTM × ₹10-15K. Sell-limit at 12×.",
                "is_e0": True, "is_e1": False, "next_window": "now (harvest)"}
    return {"verdict": "CLEANUP", "score": 10, "color": "slate",
            "label": "E-0 — cleanup phase",
            "reason": "15:25+ on expiry; close anything open at market",
            "action": "Cancel unfilled limits. Log results.",
            "is_e0": True, "is_e1": False, "next_window": "tomorrow"}


def _next_trade_window(instrument: str, today: date) -> str:
    """Lookahead: returns label of next E-0/E-1 window."""
    from lib.expiry_calendar import is_e0, is_e1, is_trading_day
    for offset in range(1, 10):
        d = today + timedelta(days=offset)
        if not is_trading_day(d): continue
        if is_e0(d, instrument): return f"{d.strftime('%a %d-%b')} 9:18 (E-0)"
        if is_e1(d, instrument): return f"{d.strftime('%a %d-%b')} 10:00 (E-1)"
    return "—"


# ── Helper: premium rise probability (heuristic) ────────────────────────
def _premium_rise_prob(cushion: float, dist_pct: float, vix_chg_pct: float,
                       hh_mm: int, oi: int, ltp: float, dte: int, side: str,
                       spot_chg_pct: float, market_open: bool) -> int | None:
    """Probability premium will be HIGHER in ~15 min. 2-95.

    Heuristic — not Black-Scholes. Calibrated against intuition:
      - Deep cushion (3+): premium decays, ~5-15% chance of rise
      - Tight cushion (<1): premium volatile, ~50-75% chance
      - VIX expanding intraday: bumps up
      - Spot drifting TOWARD strike: bumps up
      - Manipulation window (E-0 14:00-15:25 + low OI + 4-6% OTM): big bump
      - DTE 0 + cheap (<₹1) + EOD: pin volatility
    Returns None if market closed (number isn't meaningful).
    """
    if not market_open: return None
    p = 35  # baseline — theta tilts slightly against rise

    if cushion >= 3.0: p -= 22
    elif cushion >= 2.0: p -= 12
    elif cushion >= 1.5: p -= 5
    elif cushion < 1.0: p += 15

    p += int(vix_chg_pct * 2.0)

    # Spot drift effect
    if side == "CE":
        if spot_chg_pct > 0.5: p += 12
        elif spot_chg_pct > 0.2: p += 6
        elif spot_chg_pct < -0.5: p -= 8
    else:  # PE
        if spot_chg_pct < -0.5: p += 12
        elif spot_chg_pct < -0.2: p += 6
        elif spot_chg_pct > 0.5: p -= 8

    # Time-of-day theta progression
    if hh_mm < 9*60+30: p += 6
    elif hh_mm < 11*60: p += 0
    elif hh_mm < 14*60: p -= 6
    elif hh_mm > 15*60+15: p -= 12

    # Manipulation window on E-0 with low OI sweet spot
    if dte == 0 and 14*60 <= hh_mm < 15*60+25:
        if 4.0 <= abs(dist_pct) <= 6.0 and oi < 200000: p += 22
        if ltp < 1.0: p += 8

    # End-of-day pin volatility
    if dte == 0 and hh_mm > 15*60 and ltp < 2.0:
        p += 6

    return max(2, min(95, p))


@app.get("/api/timing/{instrument}")
def timing(instrument: str):
    """Live trade-window verdict for the instrument."""
    instrument = instrument.upper()
    if instrument not in ("NIFTY", "SENSEX"):
        raise HTTPException(400, "instrument must be NIFTY or SENSEX")
    return {
        "instrument": instrument,
        "ist_time": datetime.now(IST).strftime("%H:%M:%S"),
        "ist_date": datetime.now(IST).strftime("%Y-%m-%d"),
        "weekday": datetime.now(IST).strftime("%A"),
        "market": _market_state(),
        "verdict": _trade_verdict(instrument),
    }


# ── Helper: Black-Scholes delta approximation ──────────────────────────
def _bs_delta(spot: float, strike: float, vix_pct: float, dte_days: float, side: str) -> float:
    """Black-Scholes delta. CE: 0-1. PE: -1-0. dte_days can be fractional."""
    import math
    if dte_days <= 0: dte_days = 0.5   # intra-day fallback
    T = dte_days / 365.0
    sigma = max(vix_pct / 100.0, 0.05)
    r = 0.07
    sqrtT = T ** 0.5
    if sigma * sqrtT < 1e-9: return 0.0
    d1 = (math.log(spot / strike) + (r + sigma * sigma / 2) * T) / (sigma * sqrtT)
    cdf = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
    return round(cdf if side == "CE" else cdf - 1, 3)


# ── Helper: per-strike "why this strike" reasoning ─────────────────────
def _strike_reasoning(side: str, strike: int, ltp: float, oi: int, dist_pct: float,
                      cushion: float, ce_walls: list, pe_walls: list,
                      max_pain: int, spot: float, vix: float,
                      exp_move_pct: float) -> list[str]:
    """Returns 3-5 bullet reasons specific to this strike."""
    reasons = []
    walls = ce_walls if side == "CE" else pe_walls
    top_wall = walls[0] if walls else None

    # Wall positioning
    if top_wall:
        wall_strike = top_wall["strike"]
        wall_oi_l = top_wall["oi"] / 1e5
        if (side == "CE" and strike >= wall_strike) or (side == "PE" and strike <= wall_strike):
            if strike == wall_strike:
                reasons.append(f"At top {side} wall {wall_strike} ({wall_oi_l:.1f}L OI) — pin magnet")
            else:
                reasons.append(f"Beyond top {side} wall {wall_strike} ({wall_oi_l:.1f}L OI buffer)")
        else:
            reasons.append(f"⚠ Inside top {side} wall {wall_strike} — wall MUST hold")

    # Cushion vs expected move
    if cushion >= 3:
        reasons.append(f"Cushion {cushion} = far outside 1σ band")
    elif cushion >= 2:
        reasons.append(f"Cushion {cushion} = outside 1σ band")
    elif cushion >= 1.5:
        reasons.append(f"Cushion {cushion} = at edge of 1σ band")
    else:
        reasons.append(f"⚠ Cushion {cushion} < 1.5 = inside 1σ band")

    # Max-pain alignment
    if max_pain:
        if side == "CE" and max_pain <= spot:
            reasons.append(f"Max-pain {max_pain} pulls spot DOWN — favors CE side")
        elif side == "PE" and max_pain >= spot:
            reasons.append(f"Max-pain {max_pain} pulls spot UP — favors PE side")

    # VIX regime
    if vix < 14:
        reasons.append(f"VIX {vix} very low — minimal IV expansion risk")
    elif vix < 17:
        reasons.append(f"VIX {vix} cooling — IV crush helps")
    elif vix > 20:
        reasons.append(f"⚠ VIX {vix} elevated — IV expansion possible")

    # OI density
    if oi > 1_000_000:
        reasons.append(f"Strike OI {oi/1e5:.1f}L — well-traded, tight spreads")
    elif oi < 100_000:
        reasons.append(f"⚠ Strike OI {oi/1e5:.1f}L — thin, watch slippage")

    return reasons[:5]


# ── Helper: events calendar (manual stub for now, expand later) ────────
def _events_for_today() -> list[dict]:
    """Return list of today's market-relevant events with impact level."""
    today = datetime.now(IST).date()
    # Hardcoded known events. Expand as needed.
    events = []
    return events or [{"label": "No major events scheduled", "impact": "none"}]


# ── Helper: classical pivots from yesterday's HLC ──────────────────────
def _pivot_levels(prev_high: float, prev_low: float, prev_close: float) -> dict:
    p = (prev_high + prev_low + prev_close) / 3
    r1 = 2 * p - prev_low
    s1 = 2 * p - prev_high
    r2 = p + (prev_high - prev_low)
    s2 = p - (prev_high - prev_low)
    r3 = prev_high + 2 * (p - prev_low)
    s3 = prev_low - 2 * (prev_high - p)
    return {"pivot": round(p, 0), "r1": round(r1, 0), "r2": round(r2, 0), "r3": round(r3, 0),
            "s1": round(s1, 0), "s2": round(s2, 0), "s3": round(s3, 0)}


# ── /api/recommendations — the new tiered recommendation endpoint ──────
# ── Daily snapshot persistence (Report → Historical view) ───────────────
SNAPSHOT_DIR = ROOT / "data" / "dashboard_snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/snapshot/save")
async def save_snapshot(request: Request):
    """Persist a daily snapshot for the Report → Historical view.

    Body: {date?: 'YYYY-MM-DD' (default today), positions: [...], note?: '...'}
    Stores the positions plus a snapshot of current market context.
    """
    import json
    try:
        body = await request.json()
    except Exception:
        body = {}
    date_str = body.get("date") or datetime.now(IST).strftime("%Y-%m-%d")
    positions = body.get("positions") or []
    note = body.get("note") or ""

    # Capture current market context if Kite is alive
    market = {}
    try:
        if kite_alive():
            from lib.kite_live import _kite
            k = _kite()
            sn = k.quote(["NSE:NIFTY 50", "BSE:SENSEX", "NSE:INDIA VIX"])
            market = {
                "SENSEX": {
                    "spot": sn["BSE:SENSEX"]["last_price"],
                    "open": sn["BSE:SENSEX"]["ohlc"]["open"],
                    "high": sn["BSE:SENSEX"]["ohlc"]["high"],
                    "low": sn["BSE:SENSEX"]["ohlc"]["low"],
                    "prev_close": sn["BSE:SENSEX"]["ohlc"]["close"],
                },
                "NIFTY": {
                    "spot": sn["NSE:NIFTY 50"]["last_price"],
                    "open": sn["NSE:NIFTY 50"]["ohlc"]["open"],
                    "high": sn["NSE:NIFTY 50"]["ohlc"]["high"],
                    "low": sn["NSE:NIFTY 50"]["ohlc"]["low"],
                    "prev_close": sn["NSE:NIFTY 50"]["ohlc"]["close"],
                },
                "vix": sn["NSE:INDIA VIX"]["last_price"],
            }
    except Exception:
        pass

    # Run position analysis to capture LTPs + MTM at save time
    analysis = []
    summary = {"total_mtm": 0, "total_lots": 0, "n_short": 0, "n_long": 0}
    if positions and kite_alive():
        try:
            class _R:  # mock the request body for analysis
                async def json(self_): return {"positions": positions}
            r = await position_analysis(_R())
            if isinstance(r, dict):
                analysis = r.get("positions", [])
                summary = r.get("summary", summary)
        except Exception:
            pass

    snap = {
        "date": date_str,
        "saved_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
        "weekday": datetime.now(IST).strftime("%A"),
        "note": note,
        "market": market,
        "positions": positions,
        "analysis": analysis,
        "summary": summary,
    }
    fp = SNAPSHOT_DIR / f"{date_str}.json"
    fp.write_text(json.dumps(snap, indent=2, default=str))
    return {"success": True, "date": date_str, "n_positions": len(positions),
            "total_mtm": summary.get("total_mtm", 0), "path": str(fp.relative_to(ROOT))}


@app.get("/api/snapshots")
def list_snapshots():
    """Return list of saved snapshots (date, summary, file size)."""
    import json
    out = []
    for fp in sorted(SNAPSHOT_DIR.glob("*.json"), reverse=True):
        try:
            d = json.loads(fp.read_text())
            out.append({
                "date": d.get("date"),
                "weekday": d.get("weekday", ""),
                "saved_at": d.get("saved_at", ""),
                "note": d.get("note", "")[:100],
                "n_positions": len(d.get("positions") or []),
                "total_mtm": (d.get("summary") or {}).get("total_mtm", 0),
                "total_lots": (d.get("summary") or {}).get("total_lots", 0),
                "size_kb": round(fp.stat().st_size / 1024, 1),
            })
        except Exception as e:
            out.append({"date": fp.stem, "error": str(e)})
    return {"snapshots": out}


@app.get("/api/snapshot/{date}")
def get_snapshot(date: str):
    """Return a specific date's snapshot. Date format YYYY-MM-DD."""
    import json
    fp = SNAPSHOT_DIR / f"{date}.json"
    if not fp.exists():
        raise HTTPException(404, f"No snapshot for {date}")
    return json.loads(fp.read_text())


@app.delete("/api/snapshot/{date}")
def delete_snapshot(date: str):
    fp = SNAPSHOT_DIR / f"{date}.json"
    if fp.exists():
        fp.unlink()
        return {"deleted": date}
    raise HTTPException(404, f"No snapshot for {date}")


# ── Google Sheet ingestion ──────────────────────────────────────────────
@app.post("/api/import/google-sheet")
async def import_google_sheet(request: Request):
    """Import positions from a published Google Sheet CSV.

    Body: {"url": "https://docs.google.com/spreadsheets/d/.../export?format=csv&gid=0"}

    Sheet schema (case-insensitive headers):
      instrument | strike | side | qty   | price | broker  | demat  | time             | note
      SENSEX     | 80000  | CE   | -1064 | 2.41  | Monarch | M-001  | 2026-05-07 09:30 | Bucket A

    Returns: {positions: [...], count: N, errors: [...]}
    """
    import csv as _csv
    from io import StringIO
    try:
        import urllib.request
    except Exception:
        urllib = None

    try:
        body = await request.json()
    except Exception:
        body = {}
    url = (body.get("url") or "").strip()
    if not url:
        return JSONResponse({"error": "Missing 'url' in body"}, status_code=400)

    # Coerce common Google Sheet share URLs to CSV export
    if "docs.google.com/spreadsheets" in url and "/export?" not in url:
        # https://docs.google.com/spreadsheets/d/<ID>/edit#gid=<GID>  →  /export?format=csv&gid=<GID>
        import re as _re
        m = _re.search(r"/spreadsheets/d/([^/]+)", url)
        gid_m = _re.search(r"[?#&]gid=(\d+)", url)
        if m:
            sheet_id = m.group(1)
            gid = gid_m.group(1) if gid_m else "0"
            url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ThetaDesk/0.2"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return JSONResponse({"error": f"Fetch failed: {e}"}, status_code=400)

    rows = list(_csv.DictReader(StringIO(text)))
    # Normalize headers (case-insensitive)
    def n(d, key, default=None):
        for k in d.keys():
            if k and k.strip().lower() == key:
                v = d[k]
                return v.strip() if isinstance(v, str) else v
        return default

    positions = []
    errors = []
    for i, row in enumerate(rows, start=2):   # row 1 is header
        try:
            inst = (n(row, "instrument") or "").upper()
            if inst not in ("NIFTY", "SENSEX"): continue
            strike = int(float(n(row, "strike")))
            side = (n(row, "side") or "").upper()
            if side not in ("CE", "PE"): raise ValueError(f"side must be CE or PE, got {side!r}")
            qty_raw = n(row, "qty")
            qty = int(float(qty_raw))
            price = float(n(row, "price") or n(row, "avg_price") or 0)
            if not price:
                raise ValueError("missing price")
            positions.append({
                "instrument": inst, "strike": strike, "side": side,
                "qty": qty, "avg_price": price,
                "broker": n(row, "broker", ""),
                "demat":  n(row, "demat",  ""),
                "time":   n(row, "time",   ""),
                "note":   n(row, "note",   ""),
            })
        except Exception as e:
            errors.append({"row": i, "error": str(e), "data": dict(row)})

    return {"positions": positions, "count": len(positions), "errors": errors,
            "n_rows_in_sheet": len(rows), "fetched_url": url}


@app.post("/api/position-analysis")
async def position_analysis(request: Request):
    """Analyze a list of user-supplied open positions: live LTP, MTM, per-leg recommendation.

    Body: {"positions": [{"instrument":"SENSEX","strike":80000,"side":"CE","qty":-1000,"avg_price":2.55}, ...]}
    Negative qty = SHORT, positive = LONG.
    """
    if not kite_alive():
        raise HTTPException(401, "Kite session expired")
    try:
        body = await request.json()
    except Exception:
        body = {}
    positions = body.get("positions") or []
    if not positions:
        return {"positions": [], "summary": {"total_mtm": 0, "total_qty": 0, "n": 0}}

    # Group by instrument so we fetch chains once each
    by_inst = {}
    for p in positions:
        inst = (p.get("instrument") or "SENSEX").upper()
        by_inst.setdefault(inst, []).append(p)

    # Per-instrument context (for spot, walls, max_pain)
    contexts = {}
    for inst in by_inst:
        if inst not in ("NIFTY", "SENSEX"): continue
        try:
            d = _fetch_chain_full(inst, distance_pct=8.0)
            if "error" not in d:
                contexts[inst] = d
        except Exception:
            pass

    out = []
    total_mtm = 0
    for p in positions:
        inst = (p.get("instrument") or "SENSEX").upper()
        strike = int(p.get("strike", 0))
        side = (p.get("side") or "CE").upper()
        qty = int(p.get("qty", 0))
        avg_price = float(p.get("avg_price", 0))
        if not strike or not qty: continue
        ctx = contexts.get(inst)
        if not ctx:
            out.append({"instrument": inst, "strike": strike, "side": side, "qty": qty,
                        "avg_price": avg_price, "error": "no live context for instrument"})
            continue
        prices = ctx["prices"]
        spot = ctx["spot"]
        max_pain = ctx["max_pain"]
        dte = ctx["dte"]
        is_e0 = ctx["is_e0"]
        ltp = (prices.get((strike, side), {}) or {}).get("ltp")
        if ltp is None:
            out.append({"instrument": inst, "strike": strike, "side": side, "qty": qty,
                        "avg_price": avg_price, "error": "strike not in chain"})
            continue
        # MTM. SHORT (qty<0): profit = (avg - ltp) * |qty|
        if qty < 0:
            mtm_per_share = avg_price - ltp
        else:
            mtm_per_share = ltp - avg_price
        mtm_total = round(mtm_per_share * abs(qty), 2)
        total_mtm += mtm_total
        # Distance & cushion
        dist_pts = strike - spot
        dist_pct = dist_pts / spot * 100
        from lib.deep_otm import expected_move, cushion_ratio
        exp_mv = expected_move(spot, ctx["vix"], dte if dte > 0 else 1)
        cushion = round(cushion_ratio(abs(dist_pts), exp_mv), 2)
        # Determine recommendation
        # SHORT options recommendations:
        #   HOLD = cushion ok, theta winning OR theta will win
        #   WATCH = cushion thin, spot drifting toward strike, set mental stop
        #   CUT_PARTIAL = cushion < 0.5σ AND spot drifting toward strike
        #   CUT_ALL = ITM or near-ITM (cushion < 0.2σ)
        spot_chg = ctx.get("spot_chg_pct", 0)
        recommendation = "HOLD"
        rec_reason = []
        if qty < 0:  # SHORT (the typical case)
            # CANONICAL RULEBOOK 2.2: SL trigger = spot within X pts of strike
            sl_pts = SL_DISTANCE_PTS.get(inst, 500)
            spot_strike_dist_pts = abs(spot - strike)
            spot_strike_dist_pct = abs(dist_pct)
            # Hard close trigger: within 0.5% of strike
            if spot_strike_dist_pct <= SL_HARD_CLOSE_PCT:
                recommendation = "CUT_ALL"
                rec_reason.append(f"⚠ Rulebook 2.2: spot within {SL_HARD_CLOSE_PCT}% of strike ({int(spot_strike_dist_pts)} pts < {sl_pts} pts) — HARD CLOSE")
            elif (side == "CE" and spot >= strike) or (side == "PE" and spot <= strike):
                recommendation = "CUT_ALL"
                rec_reason.append(f"Strike ITM — cut at market")
            elif spot_strike_dist_pct <= SL_RETHINK_PCT:
                # Within 1% of strike → rethink (per Rulebook discretionary squareoff)
                recommendation = "CUT_PARTIAL" if spot_chg * (1 if side == "CE" else -1) > 0 else "WATCH"
                rec_reason.append(f"Rulebook: spot within 1% of strike ({int(spot_strike_dist_pts)} pts) — rethink. Confirm spike is real before SL.")
            elif cushion < 0.2:
                recommendation = "CUT_ALL"
                rec_reason.append(f"Cushion {cushion}σ — near ITM, cut at market")
            elif cushion < 0.5 and ((side == "CE" and spot_chg > 0.3) or (side == "PE" and spot_chg < -0.3)):
                recommendation = "CUT_PARTIAL"
                rec_reason.append(f"Cushion {cushion}σ + spot drifting your way ({spot_chg:+.2f}%) — cut 50%")
            elif cushion < 1.0 and ((side == "CE" and spot_chg > 0.3) or (side == "PE" and spot_chg < -0.3)):
                recommendation = "WATCH"
                rec_reason.append(f"Cushion {cushion}σ thin — set mental stop. Confirm spike is REAL before squareoff.")
            elif cushion >= 1.5 and ltp <= avg_price * 0.5:
                recommendation = "HOLD"
                rec_reason.append(f"Theta winning — premium decayed >50% from entry")
            elif cushion >= 1.0:
                recommendation = "HOLD"
                rec_reason.append(f"Cushion {cushion}σ ok per Rulebook")
            else:
                recommendation = "WATCH"
                rec_reason.append(f"Cushion {cushion}σ — monitor spot direction")
            # Add max-pain context
            if max_pain:
                if (side == "CE" and max_pain < spot) or (side == "PE" and max_pain > spot):
                    rec_reason.append("max-pain pull in your favor")
                elif (side == "CE" and max_pain > spot + 100) or (side == "PE" and max_pain < spot - 100):
                    rec_reason.append("⚠ max-pain pulls against you")
            # DTE-0 theta callout
            if dte == 0:
                if cushion >= 1.0 and ltp > 1:
                    rec_reason.append(f"DTE 0 — theta acceleration; expect ₹{round(ltp*0.3,2)}-{round(ltp*0.5,2)} in 30 min")
        else:  # LONG (e.g. lottery harvest buys)
            # For longs, recommendation is about whether to set sell-limit
            target_12x = round(avg_price * 12, 2)
            target_8x = round(avg_price * 8, 2)
            if ltp >= avg_price * 5:
                recommendation = "BOOK"
                rec_reason.append(f"Up {round(ltp/avg_price,1)}× from entry — book profit")
            elif ltp >= avg_price * 1.5:
                recommendation = "WATCH"
                rec_reason.append(f"Up {round(ltp/avg_price,1)}× — set sell-limit at {target_8x}-{target_12x}")
            else:
                recommendation = "HOLD"
                rec_reason.append(f"Lottery hold; sell-limit GTT at {target_12x} (12×)")

        # Suggested exit price
        exit_suggestion = None
        if recommendation in ("CUT_PARTIAL", "CUT_ALL"):
            exit_suggestion = round(ltp * 1.02, 2)   # tiny over LTP for fast fill
        elif recommendation == "BOOK":
            exit_suggestion = round(ltp * 0.95, 2)

        # Margin / max profit / premium paid (for portfolio-level metrics)
        lot_size = LOT_SIZE[inst]
        margin_per_lot = MARGIN_PER_LOT_E0[inst]
        qty_lots = abs(qty) // lot_size
        if qty < 0:
            margin_used = qty_lots * margin_per_lot
            max_profit_at_expiry = round(avg_price * abs(qty), 2)   # full credit if expires worthless
            premium_paid = 0
        else:
            margin_used = 0   # long premium positions don't lock margin
            max_profit_at_expiry = None   # theoretical unlimited
            premium_paid = round(avg_price * qty, 2)

        out.append({
            "instrument": inst,
            "strike": strike,
            "side": side,
            "qty": qty,
            "qty_lots": qty_lots,
            "avg_price": avg_price,
            "ltp": ltp,
            "spot": spot,
            "dist_pts": int(dist_pts),
            "dist_pct": round(dist_pct, 2),
            "cushion": cushion,
            "mtm_per_share": round(mtm_per_share, 2),
            "mtm_total": mtm_total,
            "margin_used": margin_used,
            "max_profit_at_expiry": max_profit_at_expiry,
            "premium_paid": premium_paid,
            "recommendation": recommendation,
            "rec_reason": "; ".join(rec_reason),
            "exit_suggestion": exit_suggestion,
            "max_pain": max_pain,
            "dte": dte,
            # Optional fill-level metadata (passed through from input):
            "broker": p.get("broker") or "",
            "demat":  p.get("demat") or "",
            "time":   p.get("time") or "",
            "note":   p.get("note") or "",
        })

    # Summary
    total_lots = sum(p.get("qty_lots", 0) for p in out)
    n_long = sum(1 for p in out if p.get("qty", 0) > 0)
    n_short = sum(1 for p in out if p.get("qty", 0) < 0)
    cuts = sum(1 for p in out if p.get("recommendation", "").startswith("CUT"))
    total_max_profit = sum((p.get("max_profit_at_expiry") or 0) for p in out)
    total_premium_paid = sum(p.get("premium_paid", 0) or 0 for p in out)

    # Margin accounting: brokers apply SPAN strangle offset — only one side can
    # be ITM at expiry, so they charge MAX(CE shorts, PE shorts) per instrument,
    # not the naked sum. We compute both for transparency.
    naked_margin_by_inst = {}      # {inst: {'CE': ₹, 'PE': ₹}}
    for p in out:
        if p.get("qty", 0) >= 0: continue   # only shorts use margin
        inst = p["instrument"]
        side = p["side"]
        m = p.get("margin_used", 0) or 0
        naked_margin_by_inst.setdefault(inst, {"CE": 0, "PE": 0})
        naked_margin_by_inst[inst][side] += m

    total_margin_naked = sum(v["CE"] + v["PE"] for v in naked_margin_by_inst.values())
    total_margin_netted = sum(max(v["CE"], v["PE"]) for v in naked_margin_by_inst.values())
    # Apply small additional E-0 expiry-day SPAN reduction (~10%) — empirical,
    # matches Rohan's broker-shown ₹83 Cr on 7-May vs my ₹93 Cr max-side estimate.
    total_margin = round(total_margin_netted * 0.90, 2)

    # ── Aggregate same-strike fills (combine multiple brokers/dematS into one logical position) ──
    # Group by (instrument, strike, side, sign(qty))
    from collections import defaultdict
    agg_buckets = defaultdict(list)
    for p in out:
        if "error" in p: continue
        sign = "SHORT" if p.get("qty", 0) < 0 else "LONG"
        key = (p["instrument"], p["strike"], p["side"], sign)
        agg_buckets[key].append(p)

    aggregated = []
    rec_priority = {"CUT_ALL": 0, "CUT_PARTIAL": 1, "WATCH": 2, "BOOK": 3, "HOLD": 4}
    for (inst, strike, side, sign), fills in agg_buckets.items():
        total_qty = sum(f["qty"] for f in fills)
        total_qty_abs = sum(abs(f["qty"]) for f in fills)
        weighted_avg = (sum(abs(f["qty"]) * f["avg_price"] for f in fills) / total_qty_abs) if total_qty_abs else 0
        worst_rec = min((f.get("recommendation", "HOLD") for f in fills), key=lambda r: rec_priority.get(r, 5))
        # Dedupe reasons; keep most informative
        reasons = list({f.get("rec_reason", "") for f in fills if f.get("rec_reason")})
        agg_row = {
            "instrument": inst, "strike": strike, "side": side,
            "qty": total_qty,
            "qty_lots": abs(total_qty) // LOT_SIZE[inst],
            "n_fills": len(fills),
            "n_brokers": len({f.get("broker", "") for f in fills if f.get("broker")}),
            "avg_price": round(weighted_avg, 4),
            "ltp": fills[0].get("ltp"),                 # same strike → same LTP
            "spot": fills[0].get("spot"),
            "dist_pts": fills[0].get("dist_pts"),
            "dist_pct": fills[0].get("dist_pct"),
            "cushion": fills[0].get("cushion"),
            "mtm_per_share": round((weighted_avg - (fills[0].get("ltp") or 0)) * (1 if sign == "SHORT" else -1), 2),
            "mtm_total": round(sum(f.get("mtm_total", 0) or 0 for f in fills), 2),
            "margin_used": sum(f.get("margin_used", 0) or 0 for f in fills),
            "max_profit_at_expiry": sum((f.get("max_profit_at_expiry") or 0) for f in fills),
            "premium_paid": sum(f.get("premium_paid", 0) or 0 for f in fills),
            "recommendation": worst_rec,
            "rec_reason": " · ".join(reasons[:2]),
            "max_pain": fills[0].get("max_pain"),
            "dte": fills[0].get("dte"),
            "fills": fills,                              # drill-down details
        }
        aggregated.append(agg_row)
    aggregated.sort(key=lambda r: -abs(r.get("mtm_total", 0)))

    # Yield per Cr — Rohan's primary KPI (₹5K/Cr target avg, ₹3K floor)
    yield_per_cr_now    = round(total_mtm        / total_margin * 1e7) if total_margin > 0 else 0
    yield_per_cr_at_exp = round(total_max_profit / total_margin * 1e7) if total_margin > 0 else 0

    return {
        "positions": out,
        "aggregated": aggregated,
        "summary": {
            "total_mtm": round(total_mtm, 2),
            "total_lots": total_lots,
            "n_short": n_short,
            "n_long": n_long,
            "n_action_needed": cuts,
            "total_margin": total_margin,                          # ← netted (broker-realistic)
            "total_margin_netted": round(total_margin_netted, 2),  # max-side estimate
            "total_margin_naked": round(total_margin_naked, 2),    # naked sum (worst-case)
            "total_max_profit": round(total_max_profit, 2),
            "total_premium_paid": round(total_premium_paid, 2),
            "yield_per_cr": yield_per_cr_now,            # ← live yield (target ₹5K/Cr avg)
            "yield_per_cr_at_expiry": yield_per_cr_at_exp,  # if all worthless
            "margin_per_cr": round(total_margin / 1e7, 2) if total_margin > 0 else 0,
            "margin_breakdown": naked_margin_by_inst,              # {inst: {CE: ₹, PE: ₹}}
        },
        "ist_time": datetime.now(IST).strftime("%H:%M:%S"),
    }


@app.get("/api/recommendations/{instrument}")
def recommendations(instrument: str, capital_cr: float = 100.0):
    """Layered LOW / MID / HIGH risk strangle recommendations with reasoning,
    deltas, technicals, walls, events, and full context — designed to drive the
    redesigned 3-card UI."""
    if not kite_alive():
        raise HTTPException(401, "Kite session expired")
    instrument = instrument.upper()
    if instrument not in ("NIFTY", "SENSEX"):
        raise HTTPException(400, "instrument must be NIFTY or SENSEX")

    def _build():
        from lib.deep_otm import expected_move, cushion_ratio
        d = _fetch_chain_full(instrument, distance_pct=8.0)
        if "error" in d: return d
        spot = d["spot"]; vix = d["vix"]; dte = d["dte"]; max_pain = d["max_pain"]
        prices = d["prices"]; strikes = d["strikes"]; grid = d["grid"]
        regime = vix_regime(vix)
        lot = LOT_SIZE[instrument]
        margin_lot = MARGIN_PER_LOT_E0[instrument]
        shares_per_cr = SHARES_PER_CR[instrument]
        exp_mv = expected_move(spot, vix, dte if dte > 0 else 1)

        # Determine bias
        bias_signals = []
        mp_pct = (max_pain - spot) / spot * 100 if max_pain else 0
        if mp_pct > 0.2: bias = "bullish"; bias_signals.append(f"max-pain +{mp_pct:.2f}%")
        elif mp_pct < -0.2: bias = "bearish"; bias_signals.append(f"max-pain {mp_pct:.2f}%")
        else: bias = "neutral"

        # Technicals from yesterday's snapshot — get prev OHLC from spot quote
        try:
            from lib.kite_live import _kite
            sym = "NSE:NIFTY 50" if instrument == "NIFTY" else "BSE:SENSEX"
            q = _kite().quote([sym])[sym]
            prev_high = q["ohlc"]["high"]
            prev_low = q["ohlc"]["low"]
            prev_close = q["ohlc"]["close"]
            today_open = q["ohlc"]["open"]
        except Exception:
            prev_high = prev_low = prev_close = today_open = spot
        # Pivots are based on previous day H/L/C; we have today's only — use as proxy
        pivots = _pivot_levels(prev_high, prev_low, prev_close)

        # Tier definitions per Navin Group Canonical Rulebook (Section 9T):
        #   Bucket A — Deep OTM strangles, 95% capital, 2.5%+ OTM, target ₹5K/Cr (min ₹4K)
        #   Bucket B2 — Mid-deep, 5% capital, mid-far OTM, ₹10-20K/Cr
        #   Bucket B1 — ATM straddle (opportunistic only on calm days), > ₹50K/Cr
        TIER_DEFS = [
            {"id": "A",  "label": "🛡️ Bucket A — Deep OTM",   "subtitle": "Ultra-safe · 2.5%+ (3%+ ideal) · 95% cap",
             "dist_target": 3.0, "dist_min": 2.5, "capital_pct": 95, "hit_floor": 0.96},
            {"id": "B2", "label": "⚖️ Bucket B2 — Mid-Deep",  "subtitle": "Default B · 5% cap · ₹10-20K/Cr target",
             "dist_target": 1.5, "dist_min": 1.0, "capital_pct": 5,  "hit_floor": 0.80},
            {"id": "B1", "label": "🎯 Bucket B1 — ATM Straddle (opportunistic)", "subtitle": "Calm days only · ₹50K+/Cr · 9:45-10:15 only · close 12:00",
             "dist_target": 0.3, "dist_min": 0.0, "capital_pct": 5,  "hit_floor": 0.55},
        ]
        # E-0 floors (per Section 9R)
        if d.get("is_e1"):
            floor_min, floor_ideal = PREM_PER_CR_FLOOR_MIN, PREM_PER_CR_FLOOR_IDEAL
        else:
            floor_min, floor_ideal = PREM_PER_CR_E0_FLOOR, PREM_PER_CR_E0_IDEAL

        def pick_strike(side: str, target_dist_pct: float, min_dist_pct: float) -> int | None:
            target_strike = spot * (1 + target_dist_pct/100) if side == "CE" else spot * (1 - target_dist_pct/100)
            min_strike    = spot * (1 + min_dist_pct/100)    if side == "CE" else spot * (1 - min_dist_pct/100)
            if side == "CE":
                cands = [s for s in strikes if s >= min_strike]
            else:
                cands = [s for s in strikes if s <= min_strike]
            if not cands: return None
            return min(cands, key=lambda s: abs(s - target_strike))

        tiers_out = []
        for td in TIER_DEFS:
            ce_strike = pick_strike("CE", td["dist_target"], td["dist_min"])
            pe_strike = pick_strike("PE", td["dist_target"], td["dist_min"])
            if not ce_strike or not pe_strike: continue
            ce_p = prices.get((ce_strike, "CE"), {})
            pe_p = prices.get((pe_strike, "PE"), {})
            ce_ltp = ce_p.get("ltp")
            pe_ltp = pe_p.get("ltp")
            if not ce_ltp or not pe_ltp: continue
            ce_dist_pts = ce_strike - spot
            pe_dist_pts = pe_strike - spot
            ce_dist_pct = ce_dist_pts / spot * 100
            pe_dist_pct = pe_dist_pts / spot * 100
            ce_cushion = round(cushion_ratio(abs(ce_dist_pts), exp_mv), 2)
            pe_cushion = round(cushion_ratio(abs(pe_dist_pts), exp_mv), 2)
            ce_delta = _bs_delta(spot, ce_strike, vix, max(dte, 0.5), "CE")
            pe_delta = _bs_delta(spot, pe_strike, vix, max(dte, 0.5), "PE")
            combined = round(ce_ltp + pe_ltp, 2)
            per_cr = round(combined * shares_per_cr)
            cap_inr = capital_cr * 1e7 * (td["capital_pct"] / 100)
            lots = int(cap_inr / margin_lot)
            max_profit = int(lots * lot * combined)
            limit_mult = regime["limit_mult"]
            ce_limit = round(ce_ltp * limit_mult, 2)
            pe_limit = round(pe_ltp * limit_mult, 2)
            ce_oi = int(ce_p.get("oi", 0) or 0)
            pe_oi = int(pe_p.get("oi", 0) or 0)
            # Status
            if per_cr >= floor_ideal: status = "IDEAL"
            elif per_cr >= floor_min: status = "MIN_MET"
            elif per_cr >= floor_min * 0.7: status = "CLOSE"
            else: status = "BELOW"
            # Reasoning per leg
            ce_reasons = _strike_reasoning("CE", ce_strike, ce_ltp, ce_oi, ce_dist_pct,
                                           ce_cushion, d["ce_walls"], d["pe_walls"],
                                           max_pain, spot, vix, exp_mv / spot * 100)
            pe_reasons = _strike_reasoning("PE", pe_strike, pe_ltp, pe_oi, pe_dist_pct,
                                           pe_cushion, d["ce_walls"], d["pe_walls"],
                                           max_pain, spot, vix, exp_mv / spot * 100)
            tiers_out.append({
                "id": td["id"], "label": td["label"], "subtitle": td["subtitle"],
                "capital_pct": td["capital_pct"], "lots": lots,
                "capital_inr": int(cap_inr),
                "ce": {
                    "strike": ce_strike, "delta": ce_delta, "ltp": ce_ltp, "limit": ce_limit,
                    "dist_pts": int(ce_dist_pts), "dist_pct": round(ce_dist_pct, 2),
                    "oi": ce_oi, "cushion": ce_cushion, "reasons": ce_reasons,
                },
                "pe": {
                    "strike": pe_strike, "delta": pe_delta, "ltp": pe_ltp, "limit": pe_limit,
                    "dist_pts": int(pe_dist_pts), "dist_pct": round(pe_dist_pct, 2),
                    "oi": pe_oi, "cushion": pe_cushion, "reasons": pe_reasons,
                },
                "combined_premium": combined,
                "per_cr_inr": per_cr,
                "status": status,
                "hit_rate": td["hit_floor"],
                "max_profit_inr": max_profit,
                "ticket": f"SELL CE {ce_strike} × {lots} lots @ ₹{ce_limit}\nSELL PE {pe_strike} × {lots} lots @ ₹{pe_limit}",
            })

        return {
            "context": {
                "instrument": instrument,
                "spot": spot,
                "spot_chg_pct": d.get("spot_chg_pct", 0),
                "today_open": today_open,
                "today_high": prev_high,   # actually today's
                "today_low": prev_low,
                "ist_time": datetime.now(IST).strftime("%H:%M:%S"),
                "expiry": d["expiry"],
                "dte": dte,
                "is_e0": d["is_e0"],
                "is_e1": d["is_e1"],
                "max_pain": max_pain,
                "max_pain_pct": round(mp_pct, 2),
                "vix": vix,
                "vix_chg_pct": d.get("vix_chg_pct", 0),
                "regime": regime,
                "expected_move_pct": round(exp_mv / spot * 100, 2),
                "expected_move_pts": int(exp_mv),
                "oi_pcr": d["oi_pcr"],
                "bias": bias, "bias_signals": bias_signals,
                "ce_walls": d["ce_walls"],
                "pe_walls": d["pe_walls"],
                "pivots": pivots,
                "events": _events_for_today(),
                "lot_size": lot,
                "margin_per_lot": margin_lot,
                "lots_per_cr": LOTS_PER_CR[instrument],
                "shares_per_cr": shares_per_cr,
                "floor_min": floor_min, "floor_ideal": floor_ideal,
                "capital_cr": capital_cr,
            },
            "verdict": _trade_verdict(instrument),
            "tiers": tiers_out,
        }

    return cached(f"recos_{instrument}_{capital_cr}", 5, _build)


# ── Helper: classify VIX regime ─────────────────────────────────────────
def vix_regime(vix: float) -> dict:
    """Returns regime dict: name, action, distance_adj, skip_t3, halve_t2, premium_mult."""
    if vix < 13:
        return {"name": "VERY_LOW", "label": "Very low vol", "distance_adj": -0.25,
                "skip_t3": False, "halve_t2": False, "limit_mult": 1.05,
                "action": "Tighten 0.25% on tiers — premiums small"}
    if vix < 16:
        return {"name": "LOW", "label": "Low vol — default regime", "distance_adj": 0,
                "skip_t3": False, "halve_t2": False, "limit_mult": 1.05,
                "action": "Standard distances. Limit at LTP×1.05"}
    if vix < 18:
        return {"name": "ELEVATED", "label": "Elevated vol", "distance_adj": 0.25,
                "skip_t3": False, "halve_t2": False, "limit_mult": 1.10,
                "action": "+0.25% to T1, T2. Limit LTP×1.10"}
    if vix < 22:
        return {"name": "HIGH", "label": "High vol", "distance_adj": 0.5,
                "skip_t3": True, "halve_t2": False, "limit_mult": 1.15,
                "action": "+0.5%; SKIP T3. Limit LTP×1.15. Delay T1 to 10:30"}
    return {"name": "EXTREME", "label": "Extreme vol", "distance_adj": 1.0,
            "skip_t3": True, "halve_t2": True, "limit_mult": 1.25,
            "action": "+1%; HALVE T2; SKIP T3. Limit LTP×1.25. Delay T1 to 11:00"}


# ── Helper: fetch chain in wider range with all data ────────────────────
def _fetch_chain_full(instrument: str, distance_pct: float = 8.0) -> dict:
    """Returns dict with spot, expiry, vix, max_pain, oi_pcr, walls, prices map."""
    from lib.kite_live import _kite, _instruments
    from lib.expiry_calendar import nearest_weekly_expiry_after, is_e0, is_e1
    k = _kite()
    sym = "NSE:NIFTY 50" if instrument == "NIFTY" else "BSE:SENSEX"
    spot_q = k.quote([sym])[sym]
    spot = spot_q["last_price"]
    spot_prev = spot_q["ohlc"]["close"]
    spot_chg_pct = (spot - spot_prev) / spot_prev * 100 if spot_prev else 0
    vix_q = k.quote(["NSE:INDIA VIX"])["NSE:INDIA VIX"]
    vix = vix_q["last_price"]
    vix_prev = vix_q["ohlc"]["close"]
    vix_chg_pct = (vix - vix_prev) / vix_prev * 100 if vix_prev else 0
    grid = GRID[instrument]
    today = datetime.now(IST).date()
    next_exp = nearest_weekly_expiry_after(today, instrument)
    if not next_exp:
        return {"error": "no_expiry"}
    dte = max((next_exp - today).days, 0)

    seg = "NFO" if instrument == "NIFTY" else "BFO"
    instr_dump = pd.DataFrame(_instruments() if instrument == "NIFTY" else k.instruments(seg))
    def _td(x):
        if x in (None, '', '1970-01-01'): return None
        try: return pd.to_datetime(x).date()
        except: return None
    instr_dump['expiry'] = instr_dump['expiry'].apply(_td)
    chain_df = instr_dump[(instr_dump['name'] == instrument) &
                          (instr_dump['expiry'] == next_exp) &
                          (instr_dump['instrument_type'].isin(['CE','PE']))]
    lo_strike = round(spot * (1 - distance_pct/100) / grid) * grid
    hi_strike = round(spot * (1 + distance_pct/100) / grid) * grid
    chain_df = chain_df[(chain_df['strike'] >= lo_strike) & (chain_df['strike'] <= hi_strike)]
    symbols = [f"{seg}:{r['tradingsymbol']}" for _, r in chain_df.iterrows()]
    prices = {}
    for i in range(0, len(symbols), 250):
        q = k.quote(symbols[i:i+250])
        for ts, v in q.items():
            base = ts.replace(f"{seg}:", "")
            row = chain_df[chain_df['tradingsymbol'] == base]
            if row.empty: continue
            s = int(row.iloc[0]['strike'])
            opt = row.iloc[0]['instrument_type']
            prices[(s, opt)] = {
                'ltp': v['last_price'],
                'oi': v.get('oi', 0) or 0,
                'volume': v.get('volume', 0) or 0,
            }
    strikes = sorted(set(int(s) for s in chain_df['strike']))
    # max pain
    pains = []
    for pin in strikes:
        p = sum((pin - s) * (prices.get((s,'CE'), {}).get('oi') or 0) for s in strikes if s < pin)
        p += sum((s - pin) * (prices.get((s,'PE'), {}).get('oi') or 0) for s in strikes if s > pin)
        pains.append((pin, p))
    max_pain = min(pains, key=lambda x: x[1])[0] if pains else None
    # PCR
    total_pe_oi = sum(prices.get((s, 'PE'), {}).get('oi', 0) or 0 for s in strikes)
    total_ce_oi = sum(prices.get((s, 'CE'), {}).get('oi', 0) or 0 for s in strikes)
    oi_pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else None
    # OI walls (top 3 each side)
    ce_walls = sorted([(s, prices.get((s,'CE'), {}).get('oi', 0) or 0) for s in strikes],
                      key=lambda x: x[1], reverse=True)[:3]
    pe_walls = sorted([(s, prices.get((s,'PE'), {}).get('oi', 0) or 0) for s in strikes],
                      key=lambda x: x[1], reverse=True)[:3]
    return {
        "instrument": instrument,
        "spot": round(spot, 2),
        "spot_chg_pct": round(spot_chg_pct, 2),
        "expiry": str(next_exp),
        "dte": dte,
        "is_e0": is_e0(today, instrument),
        "is_e1": is_e1(today, instrument),
        "vix": round(vix, 2),
        "vix_chg_pct": round(vix_chg_pct, 2),
        "max_pain": max_pain,
        "max_pain_pct_from_spot": round((max_pain - spot)/spot*100, 2) if max_pain else None,
        "oi_pcr": oi_pcr,
        "ce_walls": [{"strike": s, "oi": int(o)} for s, o in ce_walls],
        "pe_walls": [{"strike": s, "oi": int(o)} for s, o in pe_walls],
        "strikes": strikes,
        "prices": prices,
        "grid": grid,
    }


@app.get("/api/strategy/{instrument}")
def strategy(instrument: str, risk: str = "default", capital_cr: float = 100.0, bias: str = "neutral"):
    """Strike suggester with tier classification, cushion ratio, OI walls, recommended limits.

    risk: conservative | default | aggressive
    capital_cr: capital in ₹ Cr
    bias: neutral | bullish | bearish (shifts asymmetric distances)
    """
    if not kite_alive():
        raise HTTPException(401, "Kite session expired — run scripts/kite_login.py")
    instrument = instrument.upper()
    if instrument not in ("NIFTY", "SENSEX"):
        raise HTTPException(400, "instrument must be NIFTY or SENSEX")
    risk = risk.lower()
    bias = bias.lower()

    def _build():
        from lib.deep_otm import expected_move, cushion_ratio, classify_tier, Tier, TIER_LABELS

        d = _fetch_chain_full(instrument, distance_pct=8.0)
        if "error" in d: return d
        spot = d["spot"]; vix = d["vix"]; dte = d["dte"]; max_pain = d["max_pain"]
        prices = d["prices"]; strikes = d["strikes"]; grid = d["grid"]
        regime = vix_regime(vix)
        lot = LOT_SIZE[instrument]
        margin_lot = MARGIN_PER_LOT_E0[instrument]
        shares_per_cr = SHARES_PER_CR[instrument]
        ms = _market_state()
        verdict = _trade_verdict(instrument)
        now_ist = datetime.now(IST)
        hh_mm = now_ist.hour * 60 + now_ist.minute

        # Capital deployment per tier (per Section 9 + strategy_live)
        # T1 80%, T2 12%, T3 3%, E-1 5%
        capital_total = capital_cr * 1e7
        tier_capital_split = {"T1": 0.80, "T2": 0.12, "T3": 0.03, "E1": 0.05}

        # Risk profile overrides (stricter/looser cushion)
        # cushion thresholds: ALMOST_SURE ≥3, VERY_DEEP ≥2, BALANCED ≥1.5, AGGRESSIVE ≥1
        if risk == "conservative":
            allowed_tiers = {"T1", "T2"}     # skip T3 entirely
            min_cushion = {"T1": 3.5, "T2": 2.5}
        elif risk == "aggressive":
            allowed_tiers = {"T1", "T2", "T3"}
            min_cushion = {"T1": 2.5, "T2": 1.7, "T3": 1.2}
        else:  # default
            allowed_tiers = {"T1", "T2", "T3"}
            min_cushion = {"T1": 3.0, "T2": 2.0, "T3": 1.5}
        if regime["skip_t3"] and "T3" in allowed_tiers:
            allowed_tiers.discard("T3")

        # Distance adjustments per regime + bias
        dist_adj = regime["distance_adj"]
        ce_bias = 0.0; pe_bias = 0.0
        if bias == "bullish":  # spot drifting up → push CE further, pull PE closer
            ce_bias = 0.5; pe_bias = -0.25
        elif bias == "bearish":
            pe_bias = 0.5; ce_bias = -0.25

        # Expected move band
        exp_mv = expected_move(spot, vix, dte if dte > 0 else 1)

        # Tier base distances — Rohan's CORRECTED rules (post 7-May calibration):
        #
        #   E-0 NON-EVENT DAY (default):
        #     T1 ULTRA-SAFE  — 2.5% (floor) to 3.0% (default)
        #     T2 BALANCED    — 2.0%
        #     T3 AGGRESSIVE  — 1.5%
        #   E-0 EVENT DAY (Fed/RBI/Budget/wars/elections): wider via dist_adj.
        #
        #   E-1 OVERNIGHT carry: min 3.5% / target 4.0% (per-Cr ≥ ₹7.5K floor).
        #
        # The v2.0 doc said T1=3.0/T2=2.5/T3=2.0; Rohan's non-event tightens
        # T1 floor to 2.5% (more premium captured).
        # VIX regime adds dist_adj on volatile days (16-18 +0.25, 18-22 +0.5, 22+ +1).
        def tier_target_dist(tier_name: str, side: str) -> float:
            base = {"T1": 2.5, "T2": 2.0, "T3": 1.5, "E1": 4.0}[tier_name]
            return base + dist_adj + (ce_bias if side == "CE" else pe_bias)
        E1_MIN_DIST_PCT = 3.5   # E-1 overnight carry floor
        T1_MIN_DIST_PCT = 2.5   # E-0 ultra-safe floor (non-event)

        # OI wall lookup (top 1 each side as mandatory respect)
        top_ce_wall = d["ce_walls"][0]["strike"] if d["ce_walls"] else None
        top_pe_wall = d["pe_walls"][0]["strike"] if d["pe_walls"] else None

        # Build candidate rows: every strike on each side gets an analysis
        def analyze(strike: int, side: str) -> dict | None:
            p = prices.get((strike, side), {})
            ltp = p.get("ltp")
            oi = p.get("oi", 0) or 0
            vol = p.get("volume", 0) or 0
            if ltp is None: return None
            dist_pts = abs(strike - spot)
            dist_pct = (strike - spot) / spot * 100
            if side == "CE" and dist_pct < 0: return None  # ITM — skip
            if side == "PE" and dist_pct > 0: return None
            cush = cushion_ratio(dist_pts, exp_mv)
            tier_obj = classify_tier(cush)
            # Map deep_otm tiers → our T1/T2/T3 strategy tiers
            tier_tag = None
            if tier_obj == Tier.ALMOST_SURE: tier_tag = "T1"
            elif tier_obj == Tier.VERY_DEEP: tier_tag = "T2"
            elif tier_obj == Tier.BALANCED: tier_tag = "T3"

            # Max-pain alignment (for SELLING far-OTM)
            #   PE far below spot: prefer if pin pulls UP (max_pain > spot)
            #   CE far above spot: prefer if pin pulls DOWN (max_pain < spot)
            mp_aligned = None
            if max_pain:
                if side == "PE" and max_pain >= spot: mp_aligned = True
                elif side == "CE" and max_pain <= spot: mp_aligned = True
                else: mp_aligned = False

            # OI wall flag (top wall = strong support/resistance — selling near gets risky)
            is_wall = strike == (top_ce_wall if side == "CE" else top_pe_wall)

            # Manipulation risk for SELLING (selling at low-OI 4-6% strikes is trap-prone)
            mr = "LOW"
            if 4.0 <= abs(dist_pct) <= 6.0 and oi < 200000:
                mr = "HIGH"
            elif 3.5 <= abs(dist_pct) <= 6.5 and oi < 500000:
                mr = "MED"

            # Recommended limit price (for SELL)
            limit_price = round(ltp * regime["limit_mult"], 2)

            # Margin & sizing  (E-0 deep-OTM margin)
            premium_per_lot = ltp * lot
            return_per_cr = round(ltp * shares_per_cr, 0)  # ₹ captured if worthless, per Cr E-0 margin

            # Breakeven
            breakeven = strike + ltp if side == "CE" else strike - ltp

            # Backtest hit rate hint (rough mapping)
            hit_rate = None
            if cush >= 3.0: hit_rate = 0.96
            elif cush >= 2.0: hit_rate = 0.92
            elif cush >= 1.5: hit_rate = 0.85
            elif cush >= 1.0: hit_rate = 0.72

            # Premium rise probability (heuristic, only meaningful when market open)
            prem_rise = _premium_rise_prob(
                cushion=cush, dist_pct=dist_pct, vix_chg_pct=d.get("vix_chg_pct", 0),
                hh_mm=hh_mm, oi=oi, ltp=ltp, dte=dte, side=side,
                spot_chg_pct=d.get("spot_chg_pct", 0), market_open=ms["market_open"],
            )

            return {
                "strike": strike,
                "side": side,
                "ltp": ltp,
                "limit_price": limit_price,
                "oi": oi,
                "volume": vol,
                "dist_pct": round(dist_pct, 2),
                "dist_abs_pct": round(abs(dist_pct), 2),
                "cushion": round(cush, 2),
                "tier": tier_tag,
                "tier_label": TIER_LABELS[tier_obj] if tier_obj else None,
                "max_pain_aligned": mp_aligned,
                "is_top_wall": is_wall,
                "manipulation_risk": mr,
                "premium_per_lot": round(premium_per_lot, 0),
                "return_per_cr": return_per_cr,
                "breakeven": round(breakeven, 1),
                "hit_rate": hit_rate,
                "premium_rise_prob": prem_rise,
                "lot_size": lot,
            }

        all_rows = []
        for s in strikes:
            for side in ("CE", "PE"):
                r = analyze(s, side)
                if r: all_rows.append(r)

        # Group into tiers per side, then for each tier pick the best candidate near target distance
        def best_in_tier(tier: str, side: str) -> list[dict]:
            target = tier_target_dist(tier, side)
            min_c = min_cushion.get(tier, 1.0)
            # Filter: same tier OR cushion meets min
            cands = [r for r in all_rows if r["side"] == side and r["cushion"] >= min_c]
            if not cands: return []
            # Sort by closeness to target distance
            cands.sort(key=lambda r: abs(r["dist_abs_pct"] - target))
            # Top 3 candidates
            return cands[:3]

        tiers_out = {}
        for tier in ["T1", "T2", "T3"]:
            if tier not in allowed_tiers:
                tiers_out[tier] = {"skipped": True, "reason": f"VIX regime {regime['name']}" if tier == "T3" and regime["skip_t3"] else f"Risk profile {risk}"}
                continue
            cap_alloc = capital_total * tier_capital_split[tier]
            lots_budget = int(cap_alloc / margin_lot)
            ce_cands = best_in_tier(tier, "CE")
            pe_cands = best_in_tier(tier, "PE")
            for r in ce_cands + pe_cands:
                r["lots_in_tier_budget"] = lots_budget
                r["tier_capital_inr"] = int(cap_alloc)
                r["expected_pnl_at_expiry"] = int(r["premium_per_lot"] * lots_budget)
            tiers_out[tier] = {
                "skipped": False,
                "label": {"T1": "Tier 1 — Ultra-safe (80%)", "T2": "Tier 2 — Balanced (12%)", "T3": "Tier 3 — Aggressive (3%)"}[tier],
                "target_dist_ce_pct": round(tier_target_dist(tier, "CE"), 2),
                "target_dist_pe_pct": round(tier_target_dist(tier, "PE"), 2),
                "min_cushion": min_c if (min_c := min_cushion.get(tier)) else None,
                "capital_allocated_inr": int(cap_alloc),
                "lots_budget": lots_budget,
                "ce_candidates": ce_cands,
                "pe_candidates": pe_cands,
            }

        # E-1 advance — only relevant if today is E-1
        if d["is_e1"]:
            tier = "E1"
            cap_alloc = capital_total * tier_capital_split[tier]
            lots_budget = int(cap_alloc / margin_lot)
            target = tier_target_dist(tier, "CE")
            # Filter: enforce ≥4% OTM (Rohan's post-6-May rule) AND cushion ≥ 2.5
            ce_cands = sorted([r for r in all_rows if r["side"] == "CE"
                               and r["cushion"] >= 2.5
                               and r["dist_abs_pct"] >= E1_MIN_DIST_PCT],
                              key=lambda r: abs(r["dist_abs_pct"] - target))[:3]
            pe_cands = sorted([r for r in all_rows if r["side"] == "PE"
                               and r["cushion"] >= 2.5
                               and r["dist_abs_pct"] >= E1_MIN_DIST_PCT],
                              key=lambda r: abs(r["dist_abs_pct"] - target))[:3]
            for r in ce_cands + pe_cands:
                r["lots_in_tier_budget"] = lots_budget
                r["expected_pnl_at_expiry"] = int(r["premium_per_lot"] * lots_budget)
            tiers_out["E1"] = {
                "skipped": False,
                "label": "E-1 Advance — Day-before (5%)",
                "target_dist_ce_pct": round(target, 2),
                "target_dist_pe_pct": round(target, 2),
                "capital_allocated_inr": int(cap_alloc),
                "lots_budget": lots_budget,
                "ce_candidates": ce_cands,
                "pe_candidates": pe_cands,
            }

        # Build "symmetric strangles meeting per-Cr floor" — answers Rohan's
        # question: which strangle clears ₹7.5K/Cr min and ideally ₹10K+/Cr,
        # while still being wall-protected and within an acceptable cushion?
        strangle_options = []
        for ce_strike in strikes:
            ce = prices.get((ce_strike, "CE"), {}); ce_ltp = ce.get("ltp")
            if not ce_ltp or ce_strike <= spot: continue
            ce_dist = (ce_strike - spot) / spot * 100
            # Find approximately symmetric PE strike
            pe_target_dist = -ce_dist
            pe_target = spot * (1 + pe_target_dist/100)
            pe_strike = min((s for s in strikes if s <= pe_target), default=None,
                            key=lambda s: abs(s - pe_target) if s is not None else 1e18) if any(s <= pe_target for s in strikes) else None
            # simpler: pick closest PE to target on the down side
            pe_candidates = [s for s in strikes if s <= pe_target]
            if not pe_candidates: continue
            pe_strike = max(pe_candidates)
            pe = prices.get((pe_strike, "PE"), {}); pe_ltp = pe.get("ltp")
            if not pe_ltp: continue
            combined = round(ce_ltp + pe_ltp, 2)
            per_cr = round(combined * shares_per_cr)
            avg_dist = round((abs((ce_strike - spot) / spot * 100) + abs((pe_strike - spot) / spot * 100)) / 2, 2)
            avg_cushion = round(((ce_strike - spot) + (spot - pe_strike)) / 2 / exp_mv, 2) if exp_mv > 0 else 0
            # Wall protection: is CE strike beyond top CE wall, PE strike beyond top PE wall?
            top_ce_wall = d["ce_walls"][0]["strike"] if d["ce_walls"] else None
            top_pe_wall = d["pe_walls"][0]["strike"] if d["pe_walls"] else None
            ce_wall_protected = (top_ce_wall is not None) and (ce_strike >= top_ce_wall)
            pe_wall_protected = (top_pe_wall is not None) and (pe_strike <= top_pe_wall)
            # Status flag — context-aware floors:
            #   E-1 carry day: ideal ₹10K, min ₹7.5K
            #   E-0 same-day / other: ideal ₹5K, min ₹3K
            if d.get("is_e1"):
                _ideal = PREM_PER_CR_FLOOR_IDEAL; _min = PREM_PER_CR_FLOOR_MIN
            else:
                _ideal = PREM_PER_CR_E0_IDEAL; _min = PREM_PER_CR_E0_FLOOR
            if per_cr >= _ideal: status = "IDEAL"
            elif per_cr >= _min: status = "MIN_MET"
            elif per_cr >= _min * 0.7: status = "CLOSE"
            else: status = "BELOW"
            # Hit-rate proxy from cushion
            if avg_cushion >= 3: hit = 0.96
            elif avg_cushion >= 2: hit = 0.92
            elif avg_cushion >= 1.5: hit = 0.85
            elif avg_cushion >= 1: hit = 0.72
            else: hit = 0.50
            # Wall-bonus to hit rate (heuristic)
            if ce_wall_protected and pe_wall_protected: hit = min(0.97, hit + 0.05)
            strangle_options.append({
                "ce_strike": ce_strike, "pe_strike": pe_strike,
                "ce_ltp": ce_ltp, "pe_ltp": pe_ltp,
                "combined": combined,
                "ce_dist_pct": round((ce_strike - spot)/spot*100, 2),
                "pe_dist_pct": round((pe_strike - spot)/spot*100, 2),
                "avg_dist_pct": avg_dist,
                "avg_cushion": avg_cushion,
                "per_cr_inr": per_cr,
                "status": status,
                "hit_rate_est": hit,
                "ce_wall_protected": ce_wall_protected,
                "pe_wall_protected": pe_wall_protected,
                "ce_limit": round(ce_ltp * regime["limit_mult"], 2),
                "pe_limit": round(pe_ltp * regime["limit_mult"], 2),
            })
        # Sort by status desc (IDEAL → MIN_MET → CLOSE → BELOW), then by avg_cushion desc within status
        status_order = {"IDEAL": 0, "MIN_MET": 1, "CLOSE": 2, "BELOW": 3}
        strangle_options.sort(key=lambda r: (status_order.get(r["status"], 9), -r["avg_cushion"]))

        # Pick "RECOMMENDED" T1 ultra-safe pair per Rohan's rules:
        #   1. Distance ≥ 2.5% (the ultra-safe floor — non-event days)
        #   2. Per-Cr ≥ ₹3,000 (E-0 absolute floor); ideally ≥ ₹5,000
        #   3. Wall-protected both sides preferred
        #   4. Among qualifying, pick the one closest to ₹5K/Cr ideal
        recommended = None
        # Tier 1 priority: distance ≥ 2.5% AND per-Cr ≥ ₹3K (floor)
        ultra_safe_pool = [r for r in strangle_options
                           if r["avg_dist_pct"] >= 2.5
                           and r["per_cr_inr"] >= PREM_PER_CR_E0_FLOOR]
        # Fallback 1: distance ≥ 2.0% (T2 zone) if no ultra-safe meets premium floor
        t2_pool = [r for r in strangle_options
                   if r["avg_dist_pct"] >= 2.0
                   and r["per_cr_inr"] >= PREM_PER_CR_E0_FLOOR]
        # Fallback 2: ANY IDEAL (last resort — late-day, never skip)
        any_ideal = [r for r in strangle_options if r["status"] == "IDEAL"]
        pool = ultra_safe_pool or t2_pool or any_ideal or strangle_options[:5]
        if pool:
            recommended = max(pool, key=lambda r: (
                int(r["ce_wall_protected"] and r["pe_wall_protected"]),  # both walls
                -abs(r["per_cr_inr"] - PREM_PER_CR_E0_IDEAL) / 1000,    # prefer near ₹5K ideal
                r["avg_cushion"],                                         # then cushion
            ))

        return {
            "instrument": instrument,
            "spot": spot,
            "spot_chg_pct": d.get("spot_chg_pct", 0),
            "expiry": d["expiry"],
            "dte": dte,
            "is_e0": d["is_e0"],
            "is_e1": d["is_e1"],
            "vix": vix,
            "vix_chg_pct": d.get("vix_chg_pct", 0),
            "regime": regime,
            "max_pain": max_pain,
            "max_pain_pct_from_spot": d["max_pain_pct_from_spot"],
            "oi_pcr": d["oi_pcr"],
            "ce_walls": d["ce_walls"],
            "pe_walls": d["pe_walls"],
            "expected_move_pts": round(exp_mv, 1),
            "expected_move_pct": round(exp_mv / spot * 100, 2),
            "lot_size": LOT_SIZE[instrument],
            "margin_per_lot": margin_lot,
            "lots_per_cr": LOTS_PER_CR[instrument],
            "shares_per_cr": shares_per_cr,
            "prem_per_cr_floor_min": PREM_PER_CR_FLOOR_MIN,
            "prem_per_cr_floor_ideal": PREM_PER_CR_FLOOR_IDEAL,
            "capital_cr": capital_cr,
            "risk": risk,
            "bias": bias,
            "tiers": tiers_out,
            "strangle_options": strangle_options[:12],   # top 12
            "recommended_strangle": recommended,
            "market": ms,
            "verdict": verdict,
            "ist_time": datetime.now(IST).strftime("%H:%M:%S"),
        }

    return cached(f"strategy_{instrument}_{risk}_{capital_cr}_{bias}", 5, _build)


# ── Asymmetric strangle picker (Rohan's bias-aware tool) ───────────────
def _pick_asymmetric_strangle(d: dict, instrument: str, bias: str,
                              capital_cr: float, size_pct: float,
                              regime: dict) -> dict | None:
    """Pick best CE/PE strangle with ASYMMETRIC distances based on bias.

    bias = 'auto' → infer from max-pain direction
         | 'neutral' → CE & PE equidistant
         | 'bullish' → CE further, PE closer (spot drifting up → PE buffer grows)
         | 'bearish' → PE further, CE closer (spot drifting down → CE buffer grows)

    Returns dict with chosen strikes, premiums, per-Cr captured, suggested lots,
    limit prices (with regime multiplier), and bias inference details.
    """
    from lib.deep_otm import expected_move, cushion_ratio
    spot = d["spot"]; vix = d["vix"]; dte = d["dte"]; max_pain = d["max_pain"]
    prices = d["prices"]; strikes = d["strikes"]
    shares_per_cr = SHARES_PER_CR[instrument]
    margin_lot = MARGIN_PER_LOT_E0[instrument]
    lot = LOT_SIZE[instrument]
    exp_mv = expected_move(spot, vix, dte if dte > 0 else 1)

    # Bias inference
    bias_in = bias
    auto_signals = []
    if bias == "auto":
        # Use max-pain direction + intraday spot move + PCR
        mp_signal = 0
        if max_pain and max_pain > spot * 1.002:
            mp_signal = 1; auto_signals.append(f"max-pain {max_pain} > spot {spot} (pin↑)")
        elif max_pain and max_pain < spot * 0.998:
            mp_signal = -1; auto_signals.append(f"max-pain {max_pain} < spot {spot} (pin↓)")
        spot_signal = 0
        sc = d.get("spot_chg_pct", 0)
        if sc > 0.5: spot_signal = 1; auto_signals.append(f"intraday +{sc}%")
        elif sc < -0.5: spot_signal = -1; auto_signals.append(f"intraday {sc}%")
        pcr = d.get("oi_pcr") or 1.0
        pcr_signal = 0
        if pcr > 1.2: pcr_signal = 1; auto_signals.append(f"PCR {pcr} (put-heavy → bullish lean)")
        elif pcr < 0.7: pcr_signal = -1; auto_signals.append(f"PCR {pcr} (call-heavy → bearish lean)")
        score = mp_signal + spot_signal + pcr_signal
        if score >= 1: bias = "bullish"
        elif score <= -1: bias = "bearish"
        else: bias = "neutral"

    # Distance targets per bias (asymmetric)
    # Rohan's hard floor: 3.5% MIN on either side, even when biased.
    # Ultra-safe = ≥3.5%; volatile days push wider via regime dist_adj on caller side.
    targets = {
        "neutral":  {"ce": 3.5, "pe": 3.5},
        "bullish":  {"ce": 4.0, "pe": 3.5},   # CE further (risk side), PE at 3.5 floor
        "bearish":  {"ce": 3.5, "pe": 4.0},   # PE further (risk side), CE at 3.5 floor
    }[bias]

    # Volatility regime push (HIGH/EXTREME VIX → wider both)
    vix_v = d["vix"]
    if vix_v >= 22: targets = {k: v + 1.0 for k, v in targets.items()}
    elif vix_v >= 18: targets = {k: v + 0.5 for k, v in targets.items()}
    elif vix_v >= 16: targets = {k: v + 0.25 for k, v in targets.items()}

    # On E-1 day: enforce ≥ 4% on both sides (Rohan's post-6-May rule)
    if d.get("is_e1"):
        targets = {"ce": max(targets["ce"], 4.0), "pe": max(targets["pe"], 4.0)}

    # Generate candidate (CE, PE) pairs around targets (±0.5% search grid)
    cands = []
    for ce_off in [-0.5, -0.25, 0, 0.25, 0.5, 0.75, 1.0]:
        for pe_off in [-0.5, -0.25, 0, 0.25, 0.5, 0.75, 1.0]:
            ce_target_pct = targets["ce"] + ce_off
            pe_target_pct = targets["pe"] + pe_off
            if ce_target_pct < 1.5 or pe_target_pct < 1.5: continue   # safety floor
            ce_target = spot * (1 + ce_target_pct/100)
            pe_target = spot * (1 - pe_target_pct/100)
            ce_options = [s for s in strikes if s >= ce_target]
            pe_options = [s for s in strikes if s <= pe_target]
            if not ce_options or not pe_options: continue
            ce_strike = min(ce_options)
            pe_strike = max(pe_options)
            ce_p = prices.get((ce_strike, "CE"), {})
            pe_p = prices.get((pe_strike, "PE"), {})
            ce_ltp = ce_p.get("ltp")
            pe_ltp = pe_p.get("ltp")
            if not ce_ltp or not pe_ltp or ce_ltp <= 0 or pe_ltp <= 0: continue
            ce_dist = (ce_strike - spot) / spot * 100
            pe_dist = (pe_strike - spot) / spot * 100
            combined = round(ce_ltp + pe_ltp, 2)
            per_cr = round(combined * shares_per_cr)
            ce_cush = round(cushion_ratio(ce_strike - spot, exp_mv), 2)
            pe_cush = round(cushion_ratio(spot - pe_strike, exp_mv), 2)
            avg_cush = round((ce_cush + pe_cush) / 2, 2)
            ce_oi = ce_p.get("oi", 0) or 0
            pe_oi = pe_p.get("oi", 0) or 0
            top_ce_wall = d["ce_walls"][0]["strike"] if d["ce_walls"] else None
            top_pe_wall = d["pe_walls"][0]["strike"] if d["pe_walls"] else None
            ce_wall_ok = top_ce_wall is not None and ce_strike >= top_ce_wall
            pe_wall_ok = top_pe_wall is not None and pe_strike <= top_pe_wall
            if per_cr >= PREM_PER_CR_FLOOR_IDEAL: status = "IDEAL"
            elif per_cr >= PREM_PER_CR_FLOOR_MIN: status = "MIN_MET"
            else: status = "BELOW"
            cands.append({
                "ce_strike": ce_strike, "pe_strike": pe_strike,
                "ce_ltp": ce_ltp, "pe_ltp": pe_ltp,
                "ce_dist_pct": round(ce_dist, 2), "pe_dist_pct": round(pe_dist, 2),
                "combined_premium": combined,
                "per_cr_inr": per_cr,
                "ce_cushion": ce_cush, "pe_cushion": pe_cush, "avg_cushion": avg_cush,
                "ce_oi": int(ce_oi), "pe_oi": int(pe_oi),
                "ce_wall_protected": ce_wall_ok, "pe_wall_protected": pe_wall_ok,
                "status": status,
            })

    if not cands: return None

    # Pick recommendation:
    # 1. Prefer IDEAL, then MIN_MET, then BELOW
    # 2. Within tier: prefer wall-protected both sides
    # 3. Then prefer cushion ≥ 1.5 (T3 floor)
    # 4. Then minimize over-tightening from base targets
    def score(c):
        s = {"IDEAL": 0, "MIN_MET": 1, "BELOW": 2}[c["status"]]
        wp = -2 if (c["ce_wall_protected"] and c["pe_wall_protected"]) else (-1 if (c["ce_wall_protected"] or c["pe_wall_protected"]) else 0)
        cu = -1 if c["avg_cushion"] >= 1.5 else 0
        # Stretch penalty: how far from clean target distances
        stretch = abs(c["ce_dist_pct"] - targets["ce"]) + abs(c["pe_dist_pct"] - targets["pe"])
        # Penalty for over-loaded (too premium-heavy → too close to spot)
        overload = max(0, (c["per_cr_inr"] - 18000) / 5000)
        return (s, wp + cu, stretch + overload)
    cands.sort(key=score)
    chosen = cands[0]

    # Sizing
    cap_used = capital_cr * 1e7 * (size_pct / 100)
    lots = int(cap_used / margin_lot)
    max_profit = int(lots * lot * chosen["combined_premium"])
    # Limit prices with regime multiplier
    ce_limit = round(chosen["ce_ltp"] * regime["limit_mult"], 2)
    pe_limit = round(chosen["pe_ltp"] * regime["limit_mult"], 2)

    # Hit-rate proxy (cushion + wall bonus)
    avg_cush = chosen["avg_cushion"]
    if avg_cush >= 3: hit = 0.96
    elif avg_cush >= 2: hit = 0.92
    elif avg_cush >= 1.5: hit = 0.85
    elif avg_cush >= 1.0: hit = 0.72
    else: hit = 0.50
    if chosen["ce_wall_protected"] and chosen["pe_wall_protected"]: hit = min(0.97, hit + 0.05)

    return {
        "bias_used": bias,
        "bias_requested": bias_in,
        "auto_signals": auto_signals,
        "ce_strike": chosen["ce_strike"], "pe_strike": chosen["pe_strike"],
        "ce_ltp": chosen["ce_ltp"], "pe_ltp": chosen["pe_ltp"],
        "ce_limit": ce_limit, "pe_limit": pe_limit,
        "ce_dist_pct": chosen["ce_dist_pct"], "pe_dist_pct": chosen["pe_dist_pct"],
        "ce_cushion": chosen["ce_cushion"], "pe_cushion": chosen["pe_cushion"],
        "avg_cushion": chosen["avg_cushion"],
        "combined_premium": chosen["combined_premium"],
        "per_cr_inr": chosen["per_cr_inr"],
        "status": chosen["status"],
        "ce_oi": chosen["ce_oi"], "pe_oi": chosen["pe_oi"],
        "ce_wall_protected": chosen["ce_wall_protected"],
        "pe_wall_protected": chosen["pe_wall_protected"],
        "lots": lots,
        "capital_used_inr": int(cap_used),
        "capital_used_cr": round(cap_used / 1e7, 2),
        "max_profit_inr": max_profit,
        "max_profit_per_cr": int(max_profit / max(capital_cr, 0.01)),
        "hit_rate_est": hit,
        "lot_size": lot,
        "alternative_pairs": [{
            "ce_strike": c["ce_strike"], "pe_strike": c["pe_strike"],
            "ce_dist_pct": c["ce_dist_pct"], "pe_dist_pct": c["pe_dist_pct"],
            "combined_premium": c["combined_premium"],
            "per_cr_inr": c["per_cr_inr"],
            "avg_cushion": c["avg_cushion"],
            "status": c["status"],
        } for c in cands[1:6]],   # top 5 alternatives
    }


@app.get("/api/recommend/{instrument}")
def recommend(instrument: str, bias: str = "auto", capital_cr: float = 100.0, size_pct: float = 5.0):
    """Asymmetric strangle recommendation with bias-aware distances + per-Cr floor.

    Args:
      bias: auto | neutral | bullish | bearish
      capital_cr: total capital in ₹Cr (default 100)
      size_pct: % of capital for THIS trade (default 5 = E-1 size)
    """
    if not kite_alive():
        raise HTTPException(401, "Kite session expired")
    instrument = instrument.upper()
    if instrument not in ("NIFTY", "SENSEX"):
        raise HTTPException(400, "instrument must be NIFTY or SENSEX")
    bias = bias.lower()
    if bias not in ("auto", "neutral", "bullish", "bearish"):
        raise HTTPException(400, "bias must be auto/neutral/bullish/bearish")

    def _build():
        d = _fetch_chain_full(instrument, distance_pct=8.0)
        if "error" in d: return d
        regime = vix_regime(d["vix"])
        rec = _pick_asymmetric_strangle(d, instrument, bias, capital_cr, size_pct, regime)
        if rec is None:
            return {"error": "no_eligible_strangle"}
        verdict = _trade_verdict(instrument)
        return {
            "instrument": instrument,
            "spot": d["spot"],
            "spot_chg_pct": d.get("spot_chg_pct", 0),
            "max_pain": d["max_pain"],
            "max_pain_pct_from_spot": d["max_pain_pct_from_spot"],
            "vix": d["vix"], "vix_chg_pct": d.get("vix_chg_pct", 0),
            "regime": regime,
            "expiry": d["expiry"], "dte": d["dte"],
            "is_e0": d["is_e0"], "is_e1": d["is_e1"],
            "oi_pcr": d["oi_pcr"],
            "ce_walls": d["ce_walls"], "pe_walls": d["pe_walls"],
            "verdict": verdict,
            "recommendation": rec,
            "size_pct": size_pct,
            "shares_per_cr": SHARES_PER_CR[instrument],
            "margin_per_lot_e0": MARGIN_PER_LOT_E0[instrument],
            "lots_per_cr": LOTS_PER_CR[instrument],
            "prem_per_cr_floor_min": PREM_PER_CR_FLOOR_MIN,
            "prem_per_cr_floor_ideal": PREM_PER_CR_FLOOR_IDEAL,
            "ist_time": datetime.now(IST).strftime("%H:%M:%S"),
        }

    return cached(f"recommend_{instrument}_{bias}_{capital_cr}_{size_pct}", 5, _build)


@app.get("/api/manipulation/{instrument}")
def manipulation(instrument: str):
    """Manipulation harvest panel — Thursday SENSEX / Tuesday NIFTY E-0 spike opportunity.

    Phases (IST):
      <13:30  WAIT  — pre-window, monitor only
      13:30-14:00 PREP — identify candidates, ready capital
      14:00-14:30 BUY  — execute deep OTM cheap buys (₹10-15K per strike)
      14:30-15:00 LADDER — place sell-limits at 12× LTP
      15:00-15:25 CATCH — spike window, watch for fills
      15:25+ CLEANUP — square remaining
    Off-day → not_e0
    """
    if not kite_alive():
        raise HTTPException(401, "Kite session expired")
    instrument = instrument.upper()
    if instrument not in ("NIFTY", "SENSEX"):
        raise HTTPException(400, "instrument must be NIFTY or SENSEX")

    def _build():
        from lib.deep_otm import expected_move, cushion_ratio
        d = _fetch_chain_full(instrument, distance_pct=8.0)
        if "error" in d: return d
        spot = d["spot"]; vix = d["vix"]; dte = d["dte"]
        prices = d["prices"]; strikes = d["strikes"]
        is_e0 = d["is_e0"]
        now = datetime.now(IST)
        hh_mm = now.hour * 60 + now.minute
        ms = _market_state()
        exp_mv = expected_move(spot, vix, dte if dte > 0 else 1)

        # Phase determination
        if not is_e0:
            phase = "off"
            phase_label = f"Not E-0 — {instrument} expiry on {d['expiry']}"
            phase_action = "Manipulation harvest is E-0-only. Watch market dynamics; come back on expiry day."
        elif hh_mm < 13*60 + 30:
            phase = "wait"
            phase_label = "WAIT — pre-window"
            phase_action = "Too early. Manipulation typically starts 14:00 onwards. Don't enter yet."
        elif hh_mm < 14*60:
            phase = "prep"
            phase_label = "PREP — ready capital"
            phase_action = "Identify candidates, close 20% existing shorts at ₹0.05-0.10. Buy budget ₹50-75K (5 strikes × ₹10-15K)."
        elif hh_mm < 14*60 + 30:
            phase = "buy"
            phase_label = "BUY phase — execute deep OTM purchases"
            phase_action = "Buy 5 deep-OTM strikes at MARKET. Budget ₹10-15K per strike. Pick from candidate list."
        elif hh_mm < 15*60:
            phase = "ladder"
            phase_label = "LADDER — place sell-limit orders"
            phase_action = "Place SELL LIMITs at 12× your buy price across all 5 strikes. Use take-profit GTT."
        elif hh_mm < 15*60 + 25:
            phase = "catch"
            phase_label = "CATCH window — spike monitor active"
            phase_action = "Watch for spikes. 73% of historical SENSEX manipulation hits 15:00-15:25. Hands off the wheel — limits do the work."
        else:
            phase = "cleanup"
            phase_label = "CLEANUP — close remaining"
            phase_action = "Square any unsold positions at market. Log what worked. Cancel unfilled limits."

        # Spike-ripe candidates: 4.5-6.0% OTM, OI < 2L, LTP < ₹3 (the cheap deep ones)
        # Per analysis 014: this is the sweet spot for SENSEX manipulation
        candidates = []
        for s in strikes:
            for side in ("CE", "PE"):
                p = prices.get((s, side), {})
                ltp = p.get("ltp")
                oi = p.get("oi", 0) or 0
                vol = p.get("volume", 0) or 0
                if ltp is None or ltp <= 0: continue
                dist_pct = (s - spot) / spot * 100
                if side == "CE" and dist_pct < 0: continue
                if side == "PE" and dist_pct > 0: continue
                ad = abs(dist_pct)
                # Spike-ripe filter
                if not (4.0 <= ad <= 6.5): continue
                if ltp > 5: continue       # too expensive — already noticed
                # Score: high score = ripe for spike
                score = 0
                if 4.5 <= ad <= 6.0: score += 2     # sweet spot
                if oi < 100000: score += 2
                elif oi < 200000: score += 1
                if vol > 0 and vol < 500: score += 1   # nearly untraded → big move possible
                if ltp < 1.0: score += 2
                elif ltp < 2.0: score += 1
                # Recommended buy qty: ₹12.5K mid budget per strike
                lot = LOT_SIZE[instrument]
                budget = 12500
                qty_lots = max(1, int(budget / (ltp * lot))) if ltp * lot > 0 else 1
                # If lot is bigger than budget allows, still buy 1 lot if cost <₹15K
                cost = qty_lots * ltp * lot
                if cost > 15000 and qty_lots > 1:
                    qty_lots -= 1
                    cost = qty_lots * ltp * lot
                # Targets
                target_sell_limit = round(ltp * 12, 2)   # 12× — Section 9M
                conservative_tp = round(ltp * 8, 2)      # 8× — alternate
                payoff_at_12x = int((target_sell_limit - ltp) * lot * qty_lots)
                payoff_at_8x = int((conservative_tp - ltp) * lot * qty_lots)
                # Premium-rise probability: HIGH = bullish for the harvest BUYER
                cush = cushion_ratio(abs(s - spot), exp_mv)
                prem_rise = _premium_rise_prob(
                    cushion=cush, dist_pct=dist_pct, vix_chg_pct=d.get("vix_chg_pct", 0),
                    hh_mm=hh_mm, oi=oi, ltp=ltp, dte=dte, side=side,
                    spot_chg_pct=d.get("spot_chg_pct", 0), market_open=ms["market_open"],
                )
                candidates.append({
                    "strike": s,
                    "side": side,
                    "dist_pct": round(dist_pct, 2),
                    "ltp": ltp,
                    "oi": oi,
                    "volume": vol,
                    "score": score,
                    "ripeness": "🔥 HIGH" if score >= 5 else ("MED" if score >= 3 else "LOW"),
                    "rec_lots": qty_lots,
                    "rec_cost": int(cost),
                    "target_sell_12x": target_sell_limit,
                    "target_sell_8x": conservative_tp,
                    "payoff_12x": payoff_at_12x,
                    "payoff_8x": payoff_at_8x,
                    "premium_rise_prob": prem_rise,
                    "lot_size": lot,
                })
        # Sort by score desc, then by lower OI
        candidates.sort(key=lambda r: (-r["score"], r["oi"]))
        candidates = candidates[:20]   # top 20

        return {
            "instrument": instrument,
            "spot": spot,
            "expiry": d["expiry"],
            "dte": dte,
            "is_e0": is_e0,
            "vix": vix,
            "max_pain": d["max_pain"],
            "phase": phase,
            "phase_label": phase_label,
            "phase_action": phase_action,
            "current_time": now.strftime("%H:%M:%S"),
            "candidates": candidates,
            "total_candidates": len(candidates),
            "analysis_note": "Per backtest 014: 212 SENSEX manipulation spikes found in 1 yr. 75% of expiry days have ≥1 spike. 73% in 15:00-15:25 window. Sweet spot 4.5-6.0% OTM with OI <2L.",
            "lot_size": LOT_SIZE[instrument],
            "market": ms,
        }

    return cached(f"manip_{instrument}", 5, _build)


@app.get("/api/holidays")
def holidays():
    """Upcoming market holidays."""
    from lib.expiry_calendar import MARKET_HOLIDAYS
    today = datetime.now(IST).date()
    upcoming = sorted([(d, name) for d, name in MARKET_HOLIDAYS.items() if d >= today])[:5]
    return {"upcoming": [{"date": str(d), "name": name, "weekday": pd.Timestamp(d).day_name()} for d, name in upcoming]}


@app.get("/api/expiries")
def expiries():
    """Next weekly expiries."""
    from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, SENSEX_WEEKLY_EXPIRIES
    today = datetime.now(IST).date()
    nifty_next = [d for d in NIFTY_WEEKLY_EXPIRIES if d >= today][:3]
    sensex_next = [d for d in SENSEX_WEEKLY_EXPIRIES if d >= today][:3]
    return {
        "NIFTY": [{"date": str(d), "weekday": pd.Timestamp(d).day_name()} for d in nifty_next],
        "SENSEX": [{"date": str(d), "weekday": pd.Timestamp(d).day_name()} for d in sensex_next],
    }


# ── Pages ────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def page_dashboard(request: Request):
    # Redirect home to /live (the redesigned primary view)
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/live", status_code=307)


@app.get("/chain/{instrument}", response_class=HTMLResponse)
def page_chain(request: Request, instrument: str):
    return templates.TemplateResponse(request, "chain.html", {"instrument": instrument.upper()})


@app.get("/strategy/{instrument}", response_class=HTMLResponse)
def page_strategy(request: Request, instrument: str):
    """Legacy alias — strategy.html was superseded by recommend.html."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/recommend/{instrument.upper()}", status_code=307)


@app.get("/manipulation/{instrument}", response_class=HTMLResponse)
def page_manipulation(request: Request, instrument: str):
    return templates.TemplateResponse(request, "manipulation.html", {"instrument": instrument.upper()})


@app.get("/recommend/{instrument}", response_class=HTMLResponse)
def page_recommend(request: Request, instrument: str):
    """The new redesigned 3-tier strategy command center."""
    return templates.TemplateResponse(request, "recommend.html", {"instrument": instrument.upper()})


@app.get("/recommend", response_class=HTMLResponse)
def page_recommend_default(request: Request):
    return templates.TemplateResponse(request, "recommend.html", {"instrument": "SENSEX"})


@app.get("/live", response_class=HTMLResponse)
def page_live(request: Request):
    """Legacy alias — redirects to /report."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/report", status_code=307)


@app.get("/report", response_class=HTMLResponse)
def page_report(request: Request):
    """Report — Live + Historical positions tracker."""
    return templates.TemplateResponse(request, "report.html", {})


# ── Auto-open in browser ─────────────────────────────────────────────────
@app.on_event("startup")
def open_browser_on_start():
    import webbrowser, threading
    def _open():
        time_mod.sleep(1.5)
        webbrowser.open("http://localhost:8000")
    threading.Thread(target=_open, daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("dashboard.server:app", host="127.0.0.1", port=8000, reload=False)
