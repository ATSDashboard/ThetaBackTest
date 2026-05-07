"""
Hardcoded NIFTY + SENSEX weekly expiry calendar (2025-2026).

Sources:
  - Actual `expiry` column distinct values pulled from parquet store (2025-04-17 onwards)
  - Live Kite instruments dump (future expiries beyond parquet coverage)

Why hardcoded? Weekly expiry weekday changed and holidays shift expiries.
Heuristics like "Tuesday weekday" miss:
  - NIFTY transition Thu → Tue on 2025-09-02
  - Holiday-shifted expiries (e.g., 2025-04-30 Wed instead of Thu May 1 = Maharashtra Day)
  - One-off bridge weeks during transitions

Use these helpers in any backtest instead of weekday checks:
  is_e0(d, instrument)              → bool
  is_e1(d, instrument)              → bool
  nearest_weekly_expiry_after(d, i) → date
  dte_to_nearest_weekly(d, i)       → int
  weekly_expiries(instrument)       → list[date]

KEY TRANSITION DATE:
  NIFTY weekly expiry weekday changed from Thursday to Tuesday on 2025-09-02.
  Last Thursday weekly = 2025-08-28.  First Tuesday weekly = 2025-09-02 (5-day bridge).

LAST UPDATED: 2026-04-30 (after pulling fresh Kite instruments dump).
WHEN TO UPDATE: when user supplies more historical SENSEX data, OR each new month
to extend forward from Kite dump. See `_pull_from_data_and_kite()` below for refresh.
"""
from __future__ import annotations
from datetime import date
from functools import lru_cache

# ── NIFTY weekly expiries (2025-04 onwards) ─────────────────────────────
# Source: parquet store + live Kite dump as of 2026-04-30. Excludes monthly/quarterly
# (anything > 14 days from previous expiry — handled separately if needed).
NIFTY_THU_TO_TUE_TRANSITION = date(2025, 9, 2)   # first Tuesday weekly

NIFTY_WEEKLY_EXPIRIES = [
    # 2025 — Thursday era
    date(2025, 4, 17), date(2025, 4, 24),
    date(2025, 4, 30),  # SHIFTED Wed (May 1 = Maharashtra Day holiday)
    date(2025, 5, 8),   date(2025, 5, 15), date(2025, 5, 22), date(2025, 5, 29),
    date(2025, 6, 5),   date(2025, 6, 12), date(2025, 6, 19), date(2025, 6, 26),
    date(2025, 7, 3),   date(2025, 7, 10), date(2025, 7, 17), date(2025, 7, 24), date(2025, 7, 31),
    date(2025, 8, 7),   date(2025, 8, 14), date(2025, 8, 21),
    date(2025, 8, 28),  # LAST THURSDAY WEEKLY

    # 2025 — Tuesday era starts
    date(2025, 9, 2),   # FIRST TUESDAY WEEKLY (5-day bridge from Aug 28)
    date(2025, 9, 9),   date(2025, 9, 16), date(2025, 9, 23),
    date(2025, 9, 25),  # SPECIAL Thu — September monthly (transition artifact)
    date(2025, 9, 30),
    date(2025, 10, 7),  date(2025, 10, 14),
    date(2025, 10, 20),  # SHIFTED Mon (Tue Oct 21 = Diwali)
    date(2025, 10, 28),  date(2025, 11, 4),  date(2025, 11, 11),
    date(2025, 11, 18), date(2025, 11, 25),
    date(2025, 12, 2),  date(2025, 12, 9),  date(2025, 12, 16), date(2025, 12, 23),
    date(2025, 12, 24),  # SPECIAL Wed (extra week during Christmas)
    date(2025, 12, 30),

    # 2026 — Tuesday weekly
    date(2026, 1, 6),   date(2026, 1, 13), date(2026, 1, 20), date(2026, 1, 27),
    date(2026, 2, 3),   date(2026, 2, 10), date(2026, 2, 17), date(2026, 2, 24),
    date(2026, 3, 2),   # SHIFTED Mon (Tue Mar 3 = Holi)
    date(2026, 3, 10),  date(2026, 3, 17), date(2026, 3, 24),
    date(2026, 3, 26),  # SPECIAL Thu (likely March monthly)
    date(2026, 3, 30),  # SHIFTED Mon
    date(2026, 3, 31),  # FY-end bridge Tue
    date(2026, 4, 7),
    date(2026, 4, 13),  # SHIFTED Mon (Tue Apr 14 = Ambedkar Jayanti)
    date(2026, 4, 21),  # 8-day cycle from Apr 13
    date(2026, 4, 28),  date(2026, 5, 5),   date(2026, 5, 12), date(2026, 5, 19), date(2026, 5, 26),

    # 2026 — extending from Kite live dump (future, may add as listed)
    date(2026, 6, 2),   date(2026, 6, 9),   date(2026, 6, 16), date(2026, 6, 23), date(2026, 6, 30),
]

# Special / holiday-shifted NIFTY days (for analysis tagging)
NIFTY_SPECIAL = {
    date(2025, 4, 30):  "Wed — shifted from Thu May 1 (Maharashtra Day)",
    date(2025, 9, 2):   "Tue — first Tuesday weekly (Thu→Tue transition bridge)",
    date(2025, 9, 25):  "Thu — September monthly during transition",
    date(2025, 10, 20): "Mon — shifted from Tue Oct 21 (Diwali)",
    date(2025, 12, 24): "Wed — Christmas-week extra expiry",
    date(2026, 3, 2):   "Mon — shifted from Tue Mar 3 (Holi)",
    date(2026, 3, 26):  "Thu — March monthly (special)",
    date(2026, 3, 30):  "Mon — FY-end bridge",
    date(2026, 4, 13):  "Mon — shifted from Tue Apr 14 (Ambedkar Jayanti)",
}

# ── SENSEX weekly expiries (verified from parquet 2025-04-29 onwards + Kite future) ───
# CRITICAL: SENSEX changed weekday on 2025-09-04 (Tue → Thu). SEBI mandated single-day
# weekly expiry per index, so NIFTY moved Thu→Tue on 2025-09-02 and SENSEX moved Tue→Thu
# at the same time (the SWAP). Last Tue: 2025-08-26. First Thu: 2025-09-04 (9-day bridge).
SENSEX_TUE_TO_THU_TRANSITION = date(2025, 9, 4)

SENSEX_WEEKLY_EXPIRIES = [
    # ── Tuesday era (April-August 2025, before SEBI swap) ──
    date(2025, 4, 29), date(2025, 5, 6),  date(2025, 5, 13), date(2025, 5, 20), date(2025, 5, 27),
    date(2025, 6, 3),  date(2025, 6, 10), date(2025, 6, 17), date(2025, 6, 24),
    date(2025, 7, 1),  date(2025, 7, 8),  date(2025, 7, 15), date(2025, 7, 22), date(2025, 7, 29),
    date(2025, 8, 5),  date(2025, 8, 12), date(2025, 8, 19), date(2025, 8, 26),  # LAST TUESDAY

    # ── Thursday era (September 2025 onwards, after SEBI swap) ──
    date(2025, 9, 4),  # FIRST THURSDAY (9-day bridge from Aug 26)
    date(2025, 9, 11), date(2025, 9, 18), date(2025, 9, 25),
    date(2025, 10, 1),   # SHIFTED Wed (Thu Oct 2 = Gandhi Jayanti)
    date(2025, 10, 9),  date(2025, 10, 16), date(2025, 10, 23), date(2025, 10, 30),
    date(2025, 11, 6),  date(2025, 11, 13), date(2025, 11, 20), date(2025, 11, 27),
    date(2025, 12, 4),  date(2025, 12, 11), date(2025, 12, 18),
    date(2025, 12, 24),  # SHIFTED Wed (Thu Dec 25 = Christmas)
    date(2026, 1, 1),   date(2026, 1, 8),
    date(2026, 1, 14),  # SHIFTED Wed (Thu Jan 15 = Municipal Election)
    date(2026, 1, 15),  # bridge weekly
    date(2026, 1, 22),  date(2026, 1, 29),
    date(2026, 2, 5),   date(2026, 2, 12), date(2026, 2, 19), date(2026, 2, 26),
    date(2026, 3, 5),   date(2026, 3, 12), date(2026, 3, 19),
    date(2026, 3, 25),  # SHIFTED Wed (Thu Mar 26 = Ram Navami)
    date(2026, 4, 2),   date(2026, 4, 9),  date(2026, 4, 16), date(2026, 4, 23), date(2026, 4, 30),
    date(2026, 5, 7),   date(2026, 5, 14), date(2026, 5, 21),
    date(2026, 5, 27),  # SHIFTED Wed (Thu May 28 = Bakri Eid)
    date(2026, 6, 4),   date(2026, 6, 11), date(2026, 6, 18), date(2026, 6, 25),
]

SENSEX_SPECIAL = {
    date(2025, 9, 4):   "Thu — first Thursday weekly (Tue→Thu transition bridge)",
    date(2025, 10, 1):  "Wed — shifted from Thu Oct 2 (Gandhi Jayanti)",
    date(2025, 12, 24): "Wed — shifted from Thu Dec 25 (Christmas)",
    date(2026, 1, 14):  "Wed — shifted from Thu Jan 15 (Municipal Election)",
    date(2026, 3, 25):  "Wed — shifted from Thu Mar 26 (Ram Navami)",
    date(2026, 5, 27):  "Wed — shifted from Thu May 28 (Bakri Eid)",
}

# 2025-Jan-Apr SENSEX weeklies are NOT in our parquet (data starts 2025-04-28).
# When user supplies older data, extend SENSEX_WEEKLY_EXPIRIES backwards.
SENSEX_DEFAULT_PLACEHOLDER_END = date(2025, 4, 28)

# ── NSE/BSE market holidays (no trading) ────────────────────────────────
# Source: NSE official circular + cross-checked with Angel One published list.
# 2024: 14 holidays.  2025: 14.  2026: 16 (per official Angel One/Zerodha publication).
# Equity + F&O segments closed (currency derivatives sometimes differ — not relevant here).
# Holidays falling on weekends are NOT listed (no separate weekday closure).
# ALWAYS UPDATE ANNUALLY (typically Dec for next year) when NSE publishes circular.
MARKET_HOLIDAYS = {
    # 2024 NSE/BSE closures (published Dec 2023, NSE Circular CMTR59722)
    date(2024, 1, 22): "Special holiday (Ram Mandir consecration)",
    date(2024, 1, 26): "Republic Day",
    date(2024, 3, 8):  "Mahashivratri",
    date(2024, 3, 25): "Holi",
    date(2024, 3, 29): "Good Friday",
    date(2024, 4, 11): "Id-Ul-Fitr (Ramadan Eid)",
    date(2024, 4, 17): "Shri Ram Navami",
    date(2024, 5, 1):  "Maharashtra Day",
    date(2024, 5, 20): "General Elections (Mumbai polling)",
    date(2024, 6, 17): "Bakri Eid",
    date(2024, 7, 17): "Muharram",
    date(2024, 8, 15): "Independence Day / Parsi New Year",
    date(2024, 10, 2): "Mahatma Gandhi Jayanti",
    date(2024, 11, 1): "Diwali Laxmi Pujan (Muhurat trading evening)",
    date(2024, 11, 15): "Guru Nanak Jayanti",
    date(2024, 12, 25): "Christmas",

    # 2025 NSE/BSE closures (NSE Circular CMTR65587)
    date(2025, 2, 26): "Mahashivratri",
    date(2025, 3, 14): "Holi",
    date(2025, 3, 31): "Id-Ul-Fitr (Ramadan Eid)",
    date(2025, 4, 10): "Shri Mahavir Jayanti",
    date(2025, 4, 14): "Dr. Ambedkar Jayanti",
    date(2025, 4, 18): "Good Friday",
    date(2025, 5, 1):  "Maharashtra Day",
    date(2025, 8, 15): "Independence Day",
    date(2025, 8, 27): "Ganesh Chaturthi",
    date(2025, 10, 2): "Mahatma Gandhi Jayanti / Dussehra",
    date(2025, 10, 21): "Diwali Laxmi Pujan (Muhurat trading evening)",
    date(2025, 10, 22): "Diwali Balipratipada",
    date(2025, 11, 5): "Prakash Gurpurb Sri Guru Nanak Dev",
    date(2025, 12, 25): "Christmas",

    # 2026 NSE/BSE closures (Angel One published list verified 2026-05-02)
    date(2026, 1, 15): "Municipal Corporation Election in Maharashtra",
    date(2026, 1, 26): "Republic Day",
    date(2026, 3, 3):  "Holi",
    date(2026, 3, 26): "Shri Ram Navami",
    date(2026, 3, 31): "Shri Mahavir Jayanti",
    date(2026, 4, 3):  "Good Friday",
    date(2026, 4, 14): "Dr. Ambedkar Jayanti",
    date(2026, 5, 1):  "Maharashtra Day",
    date(2026, 5, 28): "Bakri Eid",
    date(2026, 6, 26): "Muharram",
    date(2026, 9, 14): "Ganesh Chaturthi",
    date(2026, 10, 2): "Mahatma Gandhi Jayanti",
    date(2026, 10, 20): "Dussehra",
    date(2026, 11, 10): "Diwali-Balipratipada",
    date(2026, 11, 24): "Prakash Gurpurb Sri Guru Nanak Dev",
    date(2026, 12, 25): "Christmas",
}

# Special trading sessions (market OPEN even though normally closed)
SPECIAL_SESSIONS = {
    date(2024, 11, 1):  "Muhurat Trading (Diwali evening, ~6:00-7:15 PM)",
    date(2025, 2, 1):   "Special Saturday session (Union Budget)",
    date(2025, 10, 21): "Muhurat Trading (Diwali evening)",
    date(2026, 11, 8):  "Muhurat Trading (Diwali, Sunday)",
}


def is_market_holiday(d: date) -> str | None:
    """Return holiday name if d is a market holiday, else None."""
    return MARKET_HOLIDAYS.get(d)


def is_trading_day(d: date) -> bool:
    """True if d is a weekday AND not a market holiday."""
    return d.weekday() < 5 and d not in MARKET_HOLIDAYS


# ── Helper functions ─────────────────────────────────────────────────────
@lru_cache(maxsize=2)
def weekly_expiries(instrument: str) -> list[date]:
    if instrument.upper() == "NIFTY":
        return NIFTY_WEEKLY_EXPIRIES
    if instrument.upper() == "SENSEX":
        return SENSEX_WEEKLY_EXPIRIES
    raise ValueError(f"Unknown instrument: {instrument}")


def is_e0(d: date, instrument: str) -> bool:
    """True if date d is itself a weekly expiry day for this instrument."""
    return d in weekly_expiries(instrument)


def nearest_weekly_expiry_after(d: date, instrument: str) -> date | None:
    """Earliest weekly expiry on or after `d`."""
    for exp in weekly_expiries(instrument):
        if exp >= d:
            return exp
    return None


def nearest_weekly_expiry_strictly_after(d: date, instrument: str) -> date | None:
    """Earliest weekly expiry strictly AFTER `d` (skip if d itself is expiry)."""
    for exp in weekly_expiries(instrument):
        if exp > d:
            return exp
    return None


def dte_to_nearest_weekly(d: date, instrument: str) -> int | None:
    """Calendar days between d and the next weekly expiry on or after d."""
    nxt = nearest_weekly_expiry_after(d, instrument)
    return (nxt - d).days if nxt else None


def is_e1(d: date, instrument: str) -> bool:
    """True if d is the trading day before a weekly expiry (calendar-wise, accounting for weekends)."""
    nxt = nearest_weekly_expiry_strictly_after(d, instrument)
    if nxt is None: return False
    delta = (nxt - d).days
    # E-1 = literally previous calendar day OR previous-trading-day for weekend cases
    if delta == 1: return True
    # If weekend in between (e.g., Fri before Tue expiry), Mon is E-1.
    # But that would be delta=1. So weekend bridges already handled.
    # Special: if expiry is Mon (holiday-shifted), E-1 = Fri → delta=3
    if delta == 3:
        # Check weekend: if today=Fri and expiry=Mon
        if d.weekday() == 4 and nxt.weekday() == 0:
            return True
    return False


def is_e_minus_n(d: date, instrument: str, n: int) -> bool:
    """True if d is exactly n trading days before next weekly expiry.
    Approximates 'trading days' by skipping Saturday/Sunday."""
    nxt = nearest_weekly_expiry_strictly_after(d, instrument)
    if nxt is None: return False
    # Count trading days between d (exclusive) and nxt (inclusive)
    cur = d
    trading_count = 0
    while cur < nxt:
        cur = date.fromordinal(cur.toordinal() + 1)
        if cur.weekday() < 5:   # Mon-Fri
            trading_count += 1
    return trading_count == n


def is_special(d: date, instrument: str) -> str | None:
    """Return note string if d is a holiday-shifted or special expiry, else None."""
    if instrument.upper() == "NIFTY":
        return NIFTY_SPECIAL.get(d)
    if instrument.upper() == "SENSEX":
        return SENSEX_SPECIAL.get(d)
    return None


def get_expiry_metadata(d: date) -> dict:
    """Combined metadata about a date — useful for analysis tagging."""
    md = {"date": d.isoformat(), "weekday": d.strftime("%A")}
    for inst in ["NIFTY", "SENSEX"]:
        md[f"{inst}_is_e0"] = is_e0(d, inst)
        md[f"{inst}_is_e1"] = is_e1(d, inst)
        md[f"{inst}_dte_to_weekly"] = dte_to_nearest_weekly(d, inst)
        md[f"{inst}_special_note"] = is_special(d, inst)
        md[f"{inst}_nearest_expiry"] = nearest_weekly_expiry_after(d, inst)
    return md


def is_in_thursday_era(d: date) -> bool:
    """True if d is BEFORE NIFTY transitioned to Tuesday weekly."""
    return d < NIFTY_THU_TO_TUE_TRANSITION


# ── Self-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Sanity checks
    assert is_e0(date(2025, 8, 28), "NIFTY")    # Last Thursday
    assert is_e0(date(2025, 9, 2), "NIFTY")     # First Tuesday
    assert is_e0(date(2026, 4, 28), "NIFTY")    # Recent Tuesday
    assert is_e0(date(2026, 4, 30), "SENSEX")   # Recent SENSEX Thursday
    assert is_e1(date(2026, 4, 27), "NIFTY")    # Mon before Tue 4/28
    assert is_e1(date(2026, 4, 29), "SENSEX")   # Wed before Thu 4/30
    assert not is_in_thursday_era(date(2025, 9, 2))
    assert is_in_thursday_era(date(2025, 8, 28))
    print("✓ All sanity checks passed.")
    print(f"\nNIFTY weeklies known: {len(NIFTY_WEEKLY_EXPIRIES)} from {NIFTY_WEEKLY_EXPIRIES[0]} to {NIFTY_WEEKLY_EXPIRIES[-1]}")
    print(f"SENSEX weeklies known: {len(SENSEX_WEEKLY_EXPIRIES)} from {SENSEX_WEEKLY_EXPIRIES[0]} to {SENSEX_WEEKLY_EXPIRIES[-1]}")
    print(f"\nTransition date NIFTY Thu → Tue: {NIFTY_THU_TO_TUE_TRANSITION}")
    print(f"\nSpecial NIFTY days: {len(NIFTY_SPECIAL)}")
    for d, note in NIFTY_SPECIAL.items():
        print(f"  {d}: {note}")
    print(f"\n2025 SENSEX list = Thursday-default placeholders until user supplies actual data (date < {SENSEX_DEFAULT_PLACEHOLDER_END}).")
