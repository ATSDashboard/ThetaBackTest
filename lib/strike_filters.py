"""Composable strike filters — THE core engine of the trade module.

Each filter is a pure function: (chain, market, strike) → bool + reasoning.
Combinators AllOf / AnyOf / Not compose filters into rule expressions.

Every evaluation is logged with per-filter pass/fail for future ML data.

Adding a new filter = new subclass + register in REGISTRY. Zero UI change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from typing import Any


# ── Data plumbing ────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class StrikeRow:
    strike: float
    option_type: str        # 'CE' | 'PE'
    ltp: float
    bid: float
    ask: float
    oi: int
    oi_change_pct: float
    volume: int
    iv: float
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None


@dataclass(frozen=True, slots=True)
class MarketCtx:
    spot: float
    futures: float
    vix: float
    vix_change_pct: float
    oi_pcr: float
    vol_pcr: float
    max_pain: float
    dte: int
    expected_move: float              # 1σ to expiry
    atr_5d: float | None = None
    ivr_percentile: float | None = None  # 0-100
    technical_resistance: list[float] = field(default_factory=list)
    technical_support: list[float] = field(default_factory=list)


@dataclass(slots=True)
class EvalResult:
    passed: bool
    filter_id: str
    reason: str


# ── Filter base class ────────────────────────────────────────────────────────
class StrikeFilter(ABC):
    id: str
    label: str
    params_schema: dict = {}

    @abstractmethod
    def check(self, strike: StrikeRow, market: MarketCtx, params: dict) -> EvalResult: ...

    def __repr__(self) -> str: return f"<{self.id}>"


# ── 16 built-in filters ──────────────────────────────────────────────────────
class DistancePoints(StrikeFilter):
    id = "DISTANCE_POINTS"
    label = "Distance from spot (points)"
    params_schema = {"min": "int|null", "max": "int|null"}

    def check(self, s, m, p):
        d = abs(s.strike - m.spot)
        lo, hi = p.get("min"), p.get("max")
        if lo is not None and d < lo: return EvalResult(False, self.id, f"distance {d:.0f} < {lo}")
        if hi is not None and d > hi: return EvalResult(False, self.id, f"distance {d:.0f} > {hi}")
        return EvalResult(True, self.id, f"distance {d:.0f} pts ✓")


class DistancePercent(StrikeFilter):
    id = "DISTANCE_PERCENT"
    label = "Distance from spot (%)"
    params_schema = {"min": "float|null", "max": "float|null"}

    def check(self, s, m, p):
        pct = abs(s.strike - m.spot) / m.spot * 100
        lo, hi = p.get("min"), p.get("max")
        if lo is not None and pct < lo: return EvalResult(False, self.id, f"{pct:.2f}% < {lo}%")
        if hi is not None and pct > hi: return EvalResult(False, self.id, f"{pct:.2f}% > {hi}%")
        return EvalResult(True, self.id, f"distance {pct:.2f}% ✓")


class Delta(StrikeFilter):
    id = "DELTA"
    label = "Absolute delta band"
    params_schema = {"min": "float|null", "max": "float|null"}

    def check(self, s, m, p):
        if s.delta is None:
            return EvalResult(False, self.id, "delta missing")
        d = abs(s.delta)
        lo, hi = p.get("min"), p.get("max")
        if lo is not None and d < lo: return EvalResult(False, self.id, f"|Δ| {d:.2f} < {lo}")
        if hi is not None and d > hi: return EvalResult(False, self.id, f"|Δ| {d:.2f} > {hi}")
        return EvalResult(True, self.id, f"|Δ| {d:.2f} ✓")


class PremiumPerLeg(StrikeFilter):
    id = "PREMIUM_PER_LEG"
    label = "Premium per leg (₹)"
    params_schema = {"min": "float|null", "max": "float|null"}

    def check(self, s, m, p):
        lo, hi = p.get("min"), p.get("max")
        if lo is not None and s.ltp < lo: return EvalResult(False, self.id, f"₹{s.ltp:.2f} < ₹{lo}")
        if hi is not None and s.ltp > hi: return EvalResult(False, self.id, f"₹{s.ltp:.2f} > ₹{hi}")
        return EvalResult(True, self.id, f"LTP ₹{s.ltp:.2f} ✓")


class CombinedPremium(StrikeFilter):
    """Evaluated against a *pair* — only meaningful in pair mode."""
    id = "COMBINED_PREMIUM"
    label = "Combined premium of CE + PE bids (₹)"
    params_schema = {"min": "float"}

    def check(self, s, m, p):
        # `s` here is augmented with .pair_total_bid by the engine
        tot = getattr(s, "pair_total_bid", None)
        if tot is None:
            return EvalResult(True, self.id, "not evaluated (single-leg context)")
        lo = p.get("min", 0)
        return EvalResult(tot >= lo, self.id, f"combined ₹{tot:.2f} {'≥' if tot>=lo else '<'} ₹{lo}")


class PremiumPerCrMargin(StrikeFilter):
    """Credit per ₹1Cr of margin — user's favourite metric."""
    id = "PREMIUM_PER_CR_MARGIN"
    label = "Premium collected per ₹1Cr margin"
    params_schema = {"min": "int"}

    def check(self, s, m, p):
        # requires .margin_per_lot attached by the engine
        margin = getattr(s, "margin_per_lot", None)
        lot = getattr(s, "lot_size", None)
        if not margin or not lot:
            return EvalResult(True, self.id, "not evaluated (margin unknown)")
        credit_per_cr = (s.ltp * lot) / margin * 10_000_000
        lo = p.get("min", 0)
        return EvalResult(credit_per_cr >= lo, self.id,
                          f"₹{credit_per_cr:,.0f}/Cr {'≥' if credit_per_cr>=lo else '<'} ₹{lo:,}")


class MinOI(StrikeFilter):
    id = "OI_MIN"
    label = "Minimum open interest"
    params_schema = {"min": "int"}

    def check(self, s, m, p):
        lo = p.get("min", 0)
        return EvalResult(s.oi >= lo, self.id, f"OI {s.oi:,} {'≥' if s.oi >= lo else '<'} {lo:,}")


class OIWallBehind(StrikeFilter):
    """Strike is BEYOND the top-N OI wall (must breach wall first to reach strike)."""
    id = "OI_WALL_BEHIND"
    label = "Strike is beyond top-N OI wall"
    params_schema = {"top_n": "int"}

    def check(self, s, m, p):
        top_n = p.get("top_n", 3)
        # chain walls attached by engine
        walls = getattr(s, "top_walls_same_side", None)
        if not walls:
            return EvalResult(True, self.id, "wall data not available")
        if s.option_type == "CE":
            ok = s.strike > max(w.strike for w in walls[:top_n])
        else:
            ok = s.strike < min(w.strike for w in walls[:top_n])
        return EvalResult(ok, self.id, f"{'beyond' if ok else 'inside'} top-{top_n} walls")


class BidAskSpread(StrikeFilter):
    id = "BID_ASK_SPREAD_PCT"
    label = "Max bid-ask spread (% of LTP)"
    params_schema = {"max": "float"}

    def check(self, s, m, p):
        if s.ltp <= 0: return EvalResult(False, self.id, "LTP zero")
        sp = (s.ask - s.bid) / s.ltp * 100
        hi = p.get("max", 100)
        return EvalResult(sp <= hi, self.id, f"spread {sp:.2f}% {'≤' if sp<=hi else '>'} {hi}%")


class MinVolume(StrikeFilter):
    id = "MIN_VOLUME"
    label = "Minimum today's volume"
    params_schema = {"min": "int"}

    def check(self, s, m, p):
        lo = p.get("min", 0)
        return EvalResult(s.volume >= lo, self.id, f"vol {s.volume:,} {'≥' if s.volume>=lo else '<'} {lo:,}")


class IVRankFilter(StrikeFilter):
    id = "IV_RANK"
    label = "IV rank (percentile 0-100)"
    params_schema = {"min": "float|null", "max": "float|null"}

    def check(self, s, m, p):
        if m.ivr_percentile is None:
            return EvalResult(True, self.id, "IVR unavailable")
        lo, hi = p.get("min"), p.get("max")
        v = m.ivr_percentile
        if lo is not None and v < lo: return EvalResult(False, self.id, f"IVR {v:.0f} < {lo}")
        if hi is not None and v > hi: return EvalResult(False, self.id, f"IVR {v:.0f} > {hi}")
        return EvalResult(True, self.id, f"IVR {v:.0f} ✓")


class DTE(StrikeFilter):
    id = "DAYS_TO_EXPIRY"
    label = "Days to expiry"
    params_schema = {"min": "int|null", "max": "int|null"}

    def check(self, s, m, p):
        lo, hi = p.get("min"), p.get("max")
        if lo is not None and m.dte < lo: return EvalResult(False, self.id, f"DTE {m.dte} < {lo}")
        if hi is not None and m.dte > hi: return EvalResult(False, self.id, f"DTE {m.dte} > {hi}")
        return EvalResult(True, self.id, f"DTE {m.dte} ✓")


class CushionRatio(StrikeFilter):
    id = "CUSHION_RATIO"
    label = "Distance / expected move (×)"
    params_schema = {"min": "float"}

    def check(self, s, m, p):
        if m.expected_move <= 0:
            return EvalResult(True, self.id, "expected move zero")
        r = abs(s.strike - m.spot) / m.expected_move
        lo = p.get("min", 0)
        return EvalResult(r >= lo, self.id, f"cushion {r:.2f}× {'≥' if r>=lo else '<'} {lo}×")


class PCRRegime(StrikeFilter):
    id = "PCR_REGIME"
    label = "OI PCR regime gate"
    params_schema = {"allow": "list[str]"}   # any of ['bullish','bearish','neutral']

    def check(self, s, m, p):
        allow = set(p.get("allow", ["bullish", "bearish", "neutral"]))
        regime = "bullish" if m.oi_pcr > 1.3 else "bearish" if m.oi_pcr < 0.8 else "neutral"
        return EvalResult(regime in allow, self.id,
                          f"PCR regime={regime} {'∈' if regime in allow else '∉'} {sorted(allow)}")


class VIXRegime(StrikeFilter):
    id = "VIX_REGIME"
    label = "VIX regime gate"
    params_schema = {"allow": "list[str]"}   # any of ['calm','normal','panic']

    def check(self, s, m, p):
        allow = set(p.get("allow", ["calm", "normal", "panic"]))
        regime = "calm" if m.vix <= 13 else "panic" if m.vix >= 25 else "normal"
        return EvalResult(regime in allow, self.id,
                          f"VIX {m.vix:.1f} regime={regime} {'∈' if regime in allow else '∉'} {sorted(allow)}")


class TimeWindow(StrikeFilter):
    id = "TIME_WINDOW"
    label = "Allowed intraday time window (IST)"
    params_schema = {"from": "HH:MM", "to": "HH:MM"}

    def check(self, s, m, p):
        # evaluated at call time by the engine (now_ist)
        now = getattr(m, "now_ist", None)
        if now is None:
            return EvalResult(True, self.id, "time context not provided")
        t_from = time.fromisoformat(p.get("from", "09:15"))
        t_to = time.fromisoformat(p.get("to", "15:30"))
        ok = t_from <= now <= t_to
        return EvalResult(ok, self.id, f"{now} {'∈' if ok else '∉'} [{t_from}-{t_to}]")


# ── Registry (add new filter classes here) ──────────────────────────────────
REGISTRY: dict[str, type[StrikeFilter]] = {
    c.id: c for c in [
        DistancePoints, DistancePercent, Delta, PremiumPerLeg, CombinedPremium,
        PremiumPerCrMargin, MinOI, OIWallBehind, BidAskSpread, MinVolume,
        IVRankFilter, DTE, CushionRatio, PCRRegime, VIXRegime, TimeWindow,
    ]
}


def list_filters() -> list[dict[str, Any]]:
    return [
        {"id": cls.id, "label": cls.label, "params_schema": cls.params_schema}
        for cls in REGISTRY.values()
    ]
