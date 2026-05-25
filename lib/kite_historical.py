"""
Rate-limited wrapper for Kite Connect historical_data() API.

Kite historical limits: 3 requests/second.  Each call returns 1 instrument's
minute bars between (from_dt, to_dt).  Up to ~60 days of minute history per
call (subscription dependent).

Used by ingest/kite_daily.py.
"""
from __future__ import annotations
import time
from datetime import date, datetime, timedelta
from pathlib import Path
import json
import pytz

try:
    from kiteconnect import KiteConnect, exceptions as kite_exc
except ImportError:
    KiteConnect = None
    kite_exc = None

CRED_PATH = Path.home() / ".config" / "kite_credentials.json"
SESS_PATH = Path.home() / ".config" / "kite_session.json"

IST = pytz.timezone("Asia/Kolkata")
MIN_INTERVAL = 0.34       # ~3 calls/sec, leave a small margin


class RateLimitedKite:
    def __init__(self):
        if KiteConnect is None:
            raise RuntimeError("kiteconnect not installed")
        creds = json.loads(CRED_PATH.read_text())
        sess = json.loads(SESS_PATH.read_text())
        self.kite = KiteConnect(api_key=creds["api_key"])
        self.kite.set_access_token(sess["access_token"])
        self._last_call = 0.0

    def _wait(self):
        elapsed = time.time() - self._last_call
        if elapsed < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - elapsed)
        self._last_call = time.time()

    def historical(self, instrument_token: int, from_dt: datetime,
                    to_dt: datetime, interval: str = "minute",
                    oi: bool = True, retries: int = 3) -> list[dict]:
        """Fetch minute candles, retrying transient errors with exponential backoff."""
        last_exc = None
        for attempt in range(retries):
            try:
                self._wait()
                return self.kite.historical_data(
                    instrument_token,
                    from_dt, to_dt,
                    interval=interval,
                    oi=oi,
                )
            except Exception as e:
                last_exc = e
                # Exponential backoff: 1s, 2s, 4s
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
        raise last_exc

    def quote(self, symbols: list[str]) -> dict:
        self._wait()
        return self.kite.quote(symbols)

    def instruments(self, exchange: str = None) -> list[dict]:
        self._wait()
        return self.kite.instruments(exchange) if exchange else self.kite.instruments()


def to_ist_aware(dt: datetime) -> datetime:
    """Localize naive datetime to IST."""
    if dt.tzinfo is None:
        return IST.localize(dt)
    return dt.astimezone(IST)


def session_window(d: date) -> tuple[datetime, datetime]:
    """Return (9:15, 15:35) IST tz-aware datetimes for the given trading date."""
    start = IST.localize(datetime(d.year, d.month, d.day, 9, 15))
    end = IST.localize(datetime(d.year, d.month, d.day, 15, 35))
    return start, end
