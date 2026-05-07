"""
Kite Connect live data adapter.

Used by the assistant when Rohan says "give me expiry levels".  Provides:
  get_spot()              NIFTY 50 LTP
  get_vix()               INDIA VIX LTP
  get_prev_close()        NIFTY 50 prev day's close
  get_open()              NIFTY 50 today's open
  get_chain(distances)    CE+PE LTPs at the requested distance %s, nearest weekly expiry
  detect_conditions()     gap_pct, vix, premium @ 2.5%, vol bucket — fully formatted

Requires:
  - ~/.config/kite_credentials.json  (api_key, api_secret)
  - ~/.config/kite_session.json      (access_token from kite_login.py — daily)
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
import json

try:
    from kiteconnect import KiteConnect
except ImportError:
    KiteConnect = None
import pandas as pd

CRED_PATH = Path.home() / ".config" / "kite_credentials.json"
SESS_PATH = Path.home() / ".config" / "kite_session.json"

NIFTY_GRID = 50
NIFTY_LOT = 65


def _kite() -> "KiteConnect":
    if KiteConnect is None:
        raise RuntimeError("kiteconnect not installed; pip install kiteconnect")
    if not CRED_PATH.exists():
        raise RuntimeError(f"Missing {CRED_PATH} — set up credentials first.")
    if not SESS_PATH.exists():
        raise RuntimeError(f"Missing {SESS_PATH} — run scripts/kite_login.py first (daily).")
    creds = json.loads(CRED_PATH.read_text())
    sess = json.loads(SESS_PATH.read_text())
    k = KiteConnect(api_key=creds["api_key"])
    k.set_access_token(sess["access_token"])
    return k


def get_spot() -> float:
    q = _kite().quote(["NSE:NIFTY 50"])
    return float(q["NSE:NIFTY 50"]["last_price"])


def get_vix() -> float:
    q = _kite().quote(["NSE:INDIA VIX"])
    return float(q["NSE:INDIA VIX"]["last_price"])


def get_prev_close() -> float:
    q = _kite().quote(["NSE:NIFTY 50"])
    return float(q["NSE:NIFTY 50"]["ohlc"]["close"])


def get_open() -> float:
    q = _kite().quote(["NSE:NIFTY 50"])
    return float(q["NSE:NIFTY 50"]["ohlc"]["open"])


def _nearest_weekly_expiry(instruments: pd.DataFrame, today: date) -> date:
    df = instruments[(instruments["name"] == "NIFTY") &
                     (instruments["instrument_type"].isin(["CE", "PE"]))]
    expiries = sorted(set(df["expiry"]))
    future = [e for e in expiries if e >= today]
    if not future:
        raise RuntimeError("No future NIFTY weekly expiry found in instrument dump.")
    return future[0]


_INSTR_CACHE: pd.DataFrame | None = None


def _instruments() -> pd.DataFrame:
    global _INSTR_CACHE
    if _INSTR_CACHE is not None:
        return _INSTR_CACHE
    instr = _kite().instruments("NFO")
    df = pd.DataFrame(instr)
    _INSTR_CACHE = df
    return df


def pick_strikes(spot: float, dist_pct: float) -> tuple[int, int]:
    ce = round(spot * (1 + dist_pct/100) / NIFTY_GRID) * NIFTY_GRID
    pe = round(spot * (1 - dist_pct/100) / NIFTY_GRID) * NIFTY_GRID
    return int(ce), int(pe)


def get_chain(distances: list[float] = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0],
               expiry: date | None = None) -> pd.DataFrame:
    """Return DataFrame with columns: distance_pct, side, strike, ltp.
    Defaults to nearest weekly expiry."""
    spot = get_spot()
    instr = _instruments()
    if expiry is None:
        expiry = _nearest_weekly_expiry(instr, date.today())
    nifty_chain = instr[(instr["name"] == "NIFTY") &
                        (instr["expiry"] == expiry) &
                        (instr["instrument_type"].isin(["CE", "PE"]))]

    targets = []
    for dp in distances:
        ce_s, pe_s = pick_strikes(spot, dp)
        targets.append((ce_s, "CE", dp))
        targets.append((pe_s, "PE", dp))

    symbols, key_back = [], {}
    for strike, side, dp in targets:
        m = nifty_chain[(nifty_chain["strike"] == strike) &
                        (nifty_chain["instrument_type"] == side)]
        if m.empty: continue
        ts = m.iloc[0]["tradingsymbol"]
        key = f"NFO:{ts}"
        symbols.append(key)
        key_back[key] = (strike, side, dp)

    if not symbols:
        return pd.DataFrame()
    quotes = _kite().quote(symbols)
    rows = []
    for k, v in quotes.items():
        strike, side, dp = key_back[k]
        rows.append({
            "distance_pct": dp,
            "side": side,
            "strike": strike,
            "ltp": float(v["last_price"]),
            "expiry": expiry.isoformat(),
            "tradingsymbol": k.replace("NFO:", ""),
        })
    return pd.DataFrame(rows).sort_values(["distance_pct", "side"]).reset_index(drop=True)


@dataclass
class MarketSnapshot:
    timestamp: str
    spot: float
    prev_close: float
    open: float
    gap_pct: float
    vix: float
    nearest_expiry: str
    combined_at_2_5pct: float        # CE+PE premium at 2.5% OTM
    chain: list                       # raw chain rows
    gap_bucket: str
    vix_bucket: str
    premium_bucket: str


def detect_conditions(distances: list[float] = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]) -> MarketSnapshot:
    spot = get_spot()
    prev = get_prev_close()
    op = get_open()
    vix = get_vix()
    gap_pct = (op - prev) / prev * 100 if prev else 0.0

    chain = get_chain(distances=distances)
    expiry = chain.iloc[0]["expiry"] if not chain.empty else ""

    # Combined premium at 2.5%
    sub = chain[chain["distance_pct"] == 2.5]
    if len(sub) == 2:
        combined_2_5 = float(sub["ltp"].sum())
    else:
        combined_2_5 = float("nan")

    # Bucketing per STRATEGY_LIVE.md v2.0
    if abs(gap_pct) <= 0.5:           gap_bucket = "flat"
    elif gap_pct > 1.0:              gap_bucket = "gap_up_big"
    elif gap_pct > 0.5:              gap_bucket = "gap_up"
    elif gap_pct < -1.0:             gap_bucket = "gap_dn_big"
    else:                            gap_bucket = "gap_dn"

    if vix < 13:    vix_bucket = "very_low"
    elif vix < 16:  vix_bucket = "default"
    elif vix < 18:  vix_bucket = "elevated"
    elif vix < 22:  vix_bucket = "high"
    else:           vix_bucket = "very_high"

    if combined_2_5 != combined_2_5:    premium_bucket = "unknown"
    elif combined_2_5 < 2:              premium_bucket = "thin"
    elif combined_2_5 < 6:              premium_bucket = "default"
    elif combined_2_5 < 15:             premium_bucket = "elevated"
    else:                                premium_bucket = "spike"

    return MarketSnapshot(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        spot=spot, prev_close=prev, open=op, gap_pct=gap_pct, vix=vix,
        nearest_expiry=expiry,
        combined_at_2_5pct=combined_2_5,
        chain=chain.to_dict("records"),
        gap_bucket=gap_bucket, vix_bucket=vix_bucket, premium_bucket=premium_bucket,
    )


def smoke_test():
    """Quick connectivity test."""
    try:
        s = get_spot()
        v = get_vix()
        print(f"✓ Connected.  NIFTY spot = ₹{s:.2f}  ·  INDIA VIX = {v:.2f}")
        snap = detect_conditions(distances=[2.5, 3.0])
        print(f"  Prev close: ₹{snap.prev_close:.2f}  Open: ₹{snap.open:.2f}  Gap: {snap.gap_pct:+.2f}%")
        print(f"  Nearest expiry: {snap.nearest_expiry}")
        print(f"  Combined @ 2.5% OTM: ₹{snap.combined_at_2_5pct:.2f}/share")
        print(f"  Buckets: gap={snap.gap_bucket} · vix={snap.vix_bucket} · prem={snap.premium_bucket}")
        return snap
    except Exception as e:
        print(f"✗ FAILED: {e}")
        raise


if __name__ == "__main__":
    smoke_test()
