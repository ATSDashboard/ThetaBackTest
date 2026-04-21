"""Simple, dependency-free technical levels — for the on-demand strike suggestion.

All formulas are classical. No external TA library; no licensed data vendor.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TechnicalLevels:
    prev_close: float
    prev_high: float
    prev_low: float
    pivot: float
    r1: float; r2: float; r3: float
    s1: float; s2: float; s3: float
    fib_38: float
    fib_50: float
    fib_62: float
    weekly_pivot: float


def classical_pivot(prev_high: float, prev_low: float, prev_close: float) -> dict[str, float]:
    """Classical pivot formulas (intraday)."""
    p = (prev_high + prev_low + prev_close) / 3
    r1 = 2 * p - prev_low
    s1 = 2 * p - prev_high
    r2 = p + (prev_high - prev_low)
    s2 = p - (prev_high - prev_low)
    r3 = prev_high + 2 * (p - prev_low)
    s3 = prev_low - 2 * (prev_high - p)
    return {"pivot": p, "r1": r1, "r2": r2, "r3": r3, "s1": s1, "s2": s2, "s3": s3}


def fib_retracements(high: float, low: float) -> dict[str, float]:
    """Fibonacci retracement levels over an N-day range."""
    rng = high - low
    return {
        "fib_0":   low,
        "fib_23":  low + 0.236 * rng,
        "fib_38":  low + 0.382 * rng,
        "fib_50":  low + 0.5   * rng,
        "fib_62":  low + 0.618 * rng,
        "fib_78":  low + 0.786 * rng,
        "fib_100": high,
    }


def compute_levels(
    prev_high: float, prev_low: float, prev_close: float,
    week_high: float, week_low: float,
) -> TechnicalLevels:
    piv = classical_pivot(prev_high, prev_low, prev_close)
    fib = fib_retracements(week_high, week_low)
    week_pivot = (week_high + week_low + prev_close) / 3
    return TechnicalLevels(
        prev_close=prev_close, prev_high=prev_high, prev_low=prev_low,
        pivot=piv["pivot"], r1=piv["r1"], r2=piv["r2"], r3=piv["r3"],
        s1=piv["s1"], s2=piv["s2"], s3=piv["s3"],
        fib_38=fib["fib_38"], fib_50=fib["fib_50"], fib_62=fib["fib_62"],
        weekly_pivot=week_pivot,
    )


def supports_resistances(levels: TechnicalLevels, spot: float) -> tuple[list[float], list[float]]:
    """Split all computed levels into below-spot (supports) and above-spot (resistances)."""
    candidates = [
        levels.r1, levels.r2, levels.r3, levels.s1, levels.s2, levels.s3,
        levels.pivot, levels.weekly_pivot, levels.prev_high, levels.prev_low,
        levels.fib_38, levels.fib_50, levels.fib_62,
    ]
    supports = sorted([x for x in candidates if x < spot], reverse=True)[:5]
    resistances = sorted([x for x in candidates if x > spot])[:5]
    return supports, resistances
