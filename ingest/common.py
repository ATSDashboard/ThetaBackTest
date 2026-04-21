"""Shared ingestion primitives — canonical schema + ticker parsers."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

# ── Canonical schema ─────────────────────────────────────────────────────
# Every parser produces a list of dicts with these exact keys.
CANONICAL_COLS = [
    "timestamp",        # pd.Timestamp, IST tz-aware
    "source",           # 'GFDL_HISTORICAL' | 'TW_NARROW' | 'TW_NARROW_IND' | 'TW_WIDE'
    "instrument",       # 'NIFTY' | 'SENSEX'
    "expiry",           # date or None (for SPOT)
    "strike",           # int or None
    "option_type",      # 'CE' | 'PE' | 'FUT' | 'SPOT'
    "open", "high", "low", "close",  # float
    "volume",           # int
    "oi",               # int or None
    "bar_minutes",      # 1 | 5
    "dte",              # int or None
    # Optional indicators (NULL if not present)
    "ema55", "ema200",
    "macd", "macd_signal", "macd_hist",
    "rsi", "rsi_ma",
    "stoch_k", "stoch_d",
    "early_cross", "golden_cross",            # bool / labels
    "bullish_divergence", "bearish_divergence",
]

INDICATOR_COLS = [
    "ema55", "ema200", "macd", "macd_signal", "macd_hist",
    "rsi", "rsi_ma", "stoch_k", "stoch_d",
    "early_cross", "golden_cross", "bullish_divergence", "bearish_divergence",
]


def empty_row() -> dict:
    """Template row with all columns NULL."""
    return {c: None for c in CANONICAL_COLS}


# ── Ticker parsers ───────────────────────────────────────────────────────
_MONTHS = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
           "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}


def parse_gfdl_ticker(t: str) -> dict:
    """
    Parse GFDL ticker like:
      NIFTY05JUN2522600PE.NFO  → (NIFTY, 05 Jun 2025, 22600, PE)
      NIFTY05JUN25FUT.NFO      → (NIFTY, 05 Jun 2025, None, FUT)

    Returns dict with instrument, expiry, strike, option_type.
    Raises ValueError on unparseable tickers (recorded to reject log).
    """
    t = t.strip().upper()
    if t.endswith(".NFO"): t = t[:-4]
    m = re.match(r"^([A-Z]+?)(\d{2})([A-Z]{3})(\d{2})(\d+)?([CP]E|FUT)$", t)
    if not m:
        raise ValueError(f"unparseable GFDL ticker: {t}")
    instr, dd, mmm, yy, strike, optype = m.groups()
    year = 2000 + int(yy)
    month = _MONTHS[mmm]
    exp = date(year, month, int(dd))
    return {
        "instrument": instr,
        "expiry": exp,
        "strike": int(strike) if strike else None,
        "option_type": "FUT" if optype == "FUT" else optype,
    }


def parse_tw_symbol(filename: str) -> dict | None:
    """
    Parse TW filename like:
      NSE_NIFTY260421C25300, 1.csv        → (NIFTY, 2026-04-21, 25300, CE, bar=1)
      BSE_DLY_BSX260416P77500, 5 (1).csv  → (SENSEX, 2026-04-16, 77500, PE, bar=5)
      BSE_DLY_SENSEX, 5 (1).csv           → (SENSEX, None, None, SPOT, bar=5)
      NSE_NIFTY, 1(2).csv                  → (NIFTY, None, None, SPOT, bar=1)
    """
    # Strip trailing " 1.csv", " 5 (1).csv", " 1(2).csv" etc
    base = filename.replace(".csv", "")
    # Find comma → bar size
    bar = 1
    if "," in base:
        left, right = base.split(",", 1)
        m = re.search(r"\b(\d+)\b", right)
        if m: bar = int(m.group(1))
        base = left.strip()

    # Spot symbols
    if re.match(r"^(NSE_|BSE_DLY_)?(NIFTY|SENSEX)$", base):
        instr = "SENSEX" if "SENSEX" in base else "NIFTY"
        return {"instrument": instr, "expiry": None, "strike": None,
                "option_type": "SPOT", "bar_minutes": bar}

    # Options: look for YYMMDD + C/P + strike
    m = re.match(
        r"^(NSE_|BSE_DLY_)?(NIFTY|BSX)(\d{2})(\d{2})(\d{2})([CP])(\d+)$",
        base,
    )
    if not m:
        return None
    _, sym, yy, mm, dd, cp, strike = m.groups()
    instr = "SENSEX" if sym == "BSX" else "NIFTY"
    exp = date(2000 + int(yy), int(mm), int(dd))
    return {
        "instrument": instr,
        "expiry": exp,
        "strike": int(strike),
        "option_type": "CE" if cp == "C" else "PE",
        "bar_minutes": bar,
    }


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
