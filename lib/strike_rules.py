"""Strike selector engine — evaluates combinable rule expressions.

Rule expression grammar (recursive JSON):
    { "all_of": [rule, rule, ...] }
    { "any_of": [rule, rule, ...] }
    { "not":    rule }
    { "filter": "DISTANCE_POINTS", "params": {"min": 300} }

Engine returns ranked list of candidate strikes with per-filter pass/fail
logs. Log is persisted to `strike_selector_evaluations` for future analytics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.strike_selector.filters import (
    EvalResult, MarketCtx, REGISTRY, StrikeRow,
)


@dataclass(slots=True)
class StrikeCandidate:
    strike: StrikeRow
    passed: bool
    evaluations: list[EvalResult]
    score: float = 0.0             # ranking score within passing candidates
    rank_reason: str = ""


def evaluate_rule(rule: dict, strike: StrikeRow, market: MarketCtx,
                   log: list[EvalResult]) -> bool:
    """Recursively evaluate one rule against one strike."""
    if "all_of" in rule:
        return all(evaluate_rule(r, strike, market, log) for r in rule["all_of"])
    if "any_of" in rule:
        return any(evaluate_rule(r, strike, market, log) for r in rule["any_of"])
    if "not" in rule:
        return not evaluate_rule(rule["not"], strike, market, log)
    if "filter" in rule:
        cls = REGISTRY.get(rule["filter"])
        if cls is None:
            log.append(EvalResult(False, rule["filter"], "unknown filter id"))
            return False
        result = cls().check(strike, market, rule.get("params", {}))
        log.append(result)
        return result.passed
    return False


def evaluate_chain(
    chain: list[StrikeRow], market: MarketCtx, rule: dict,
    target_side: str = "BOTH",   # 'CE' | 'PE' | 'BOTH'
) -> list[StrikeCandidate]:
    """Evaluate rule against every strike in chain; return candidates (pass+fail)."""
    out: list[StrikeCandidate] = []
    for s in chain:
        if target_side != "BOTH" and s.option_type != target_side:
            continue
        log: list[EvalResult] = []
        ok = evaluate_rule(rule, s, market, log)
        # Ranking score: prefer higher cushion + higher credit/margin
        cushion = abs(s.strike - market.spot) / max(market.expected_move, 1)
        out.append(StrikeCandidate(
            strike=s, passed=ok, evaluations=log,
            score=cushion * s.ltp,
            rank_reason=f"cushion {cushion:.2f}× × LTP ₹{s.ltp:.2f}",
        ))
    # Passing ones first, by score desc
    out.sort(key=lambda c: (not c.passed, -c.score))
    return out


def evaluate_pair(
    chain: list[StrikeRow], market: MarketCtx, rule: dict,
    lot_size: int | None = None, margin_per_lot: float | None = None,
) -> list[dict[str, Any]]:
    """Evaluate pair-rules. For every CE × PE combination, attach combined
    premium + per-Cr margin so those filters can check them.
    """
    ce = [s for s in chain if s.option_type == "CE"]
    pe = [s for s in chain if s.option_type == "PE"]
    pairs: list[dict[str, Any]] = []

    for c in ce:
        for p in pe:
            # Only symmetric or user-defined pairs; for now, same distance ± tolerance
            dc = c.strike - market.spot
            dp = market.spot - p.strike
            if abs(dc - dp) > market.spot * 0.01:   # within 1% distance
                continue
            # Attach pair info as shallow copies via duck-typed attrs
            pair_total_bid = c.bid + p.bid
            for side in (c, p):
                object.__setattr__(side, "pair_total_bid", pair_total_bid)
                if lot_size:
                    object.__setattr__(side, "lot_size", lot_size)
                if margin_per_lot:
                    object.__setattr__(side, "margin_per_lot", margin_per_lot)

            ce_log: list[EvalResult] = []
            pe_log: list[EvalResult] = []
            ok_ce = evaluate_rule(rule, c, market, ce_log)
            ok_pe = evaluate_rule(rule, p, market, pe_log)
            both_ok = ok_ce and ok_pe

            pairs.append({
                "ce_strike": c.strike, "ce_ltp": c.ltp, "ce_oi": c.oi,
                "pe_strike": p.strike, "pe_ltp": p.ltp, "pe_oi": p.oi,
                "combined_premium": pair_total_bid,
                "passed": both_ok,
                "ce_evaluations": [e.__dict__ for e in ce_log],
                "pe_evaluations": [e.__dict__ for e in pe_log],
                "cushion_ce": abs(dc) / max(market.expected_move, 1),
                "cushion_pe": abs(dp) / max(market.expected_move, 1),
            })

    pairs.sort(key=lambda x: (not x["passed"], -x["combined_premium"]))
    return pairs
