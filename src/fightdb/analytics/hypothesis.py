"""Evaluate a hypothesis against `bout_sides`: filters + condition -> success rate with honest stats.

All user values go in as SQL parameters; column names are checked against an allow-list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from fightdb.analytics import stats
from fightdb.analytics.schema import Clause, Hypothesis, allowed_features
from fightdb.features.stat_meta import OUTCOMES

SMALL_N = 30
_OPS = {">": ">", ">=": ">=", "<": "<", "<=": "<=", "==": "=", "!=": "<>"}


@dataclass
class HypothesisResult:
    hypothesis_id: str
    n: int
    k: int
    rate: float
    ci_low: float
    ci_high: float
    baseline: float
    baseline_label: str
    diff_pp: float
    diff_relative: float | None
    p_value: float
    excluded_missing: int
    verdict: str
    warnings: list[str] = field(default_factory=list)
    bout_ids: list[str] = field(default_factory=list)
    baseline_n: int | None = None

    @property
    def significant(self) -> bool:
        return self.p_value < 0.05


def _population_sql(h: Hypothesis) -> tuple[str, list[Any]]:
    p = h.population
    where = ["status = 'completed'"]
    params: list[Any] = []
    if p.divisions:
        where.append(f"division IN ({', '.join('?' for _ in p.divisions)})")
        params += p.divisions
    if p.sex:
        where.append("sex = ?")
        params.append(p.sex)
    if p.date_from:
        where.append("event_date >= ?")
        params.append(p.date_from)
    if p.date_to:
        where.append("event_date <= ?")
        params.append(p.date_to)
    if p.title_only:
        where.append("is_title")
    if p.scheduled_rounds:
        where.append("scheduled_rounds = ?")
        params.append(p.scheduled_rounds)
    if p.min_ufc_fights_each:
        where.append("self_ufc_fights >= ? AND opp_ufc_fights >= ?")
        params += [p.min_ufc_fights_each, p.min_ufc_fights_each]
    if "draw" in p.exclude_outcomes:
        where.append("draw = 0")
    if "no_contest" in p.exclude_outcomes:
        where.append("no_contest = 0")
    if "dq" in p.exclude_outcomes:
        where.append("coalesce(method_group, '') <> 'dq'")
    return " AND ".join(where), params


def _clause_sql(c: Clause) -> tuple[str, list[Any]]:
    if c.feature not in allowed_features():  # defence in depth; schema already validates
        raise ValueError(f"unknown feature {c.feature}")
    col = f'"{c.feature}"'
    if c.op == "between":
        lo, hi = c.value  # type: ignore[misc]
        return f"{col} BETWEEN ? AND ?", [lo, hi]
    return f"{col} {_OPS[c.op]} ?", [c.value]


def _condition_sql(clauses: list[Clause]) -> tuple[str, list[Any], list[str]]:
    parts, params, cols = [], [], []
    for c in clauses:
        sql, p = _clause_sql(c)
        parts.append(sql)
        params += p
        cols.append(c.feature)
    return (" AND ".join(parts) or "TRUE"), params, cols


def evaluate(
    con: duckdb.DuckDBPyConnection, h: Hypothesis, keep_bouts: bool = True
) -> HypothesisResult:
    pop_sql, pop_params = _population_sql(h)
    cond_sql, cond_params, cond_cols = _condition_sql(h.condition)
    outcome = h.outcome
    if outcome not in OUTCOMES:
        raise ValueError(outcome)
    not_null = " AND ".join(f'"{c}" IS NOT NULL' for c in cond_cols) or "TRUE"

    base = f"SELECT * FROM bout_sides WHERE {pop_sql}"
    row = con.execute(
        f"""WITH pop AS ({base})
            SELECT
              count(*) FILTER (WHERE {not_null} AND ({cond_sql})) AS n,
              coalesce(sum({outcome}) FILTER (WHERE {not_null} AND ({cond_sql})), 0) AS k,
              count(*) FILTER (WHERE NOT ({not_null})) AS missing,
              count(*) FILTER (WHERE {not_null} AND NOT ({cond_sql})) AS n_c,
              coalesce(sum({outcome}) FILTER (WHERE {not_null} AND NOT ({cond_sql})), 0) AS k_c,
              count(*) AS n_all,
              coalesce(sum({outcome}), 0) AS k_all
            FROM pop""",
        pop_params + cond_params * 4,
    ).fetchone()
    assert row is not None
    n, k, missing, n_c, k_c, n_all, k_all = (int(x) for x in row)

    rate = k / n if n else float("nan")
    lo, hi = stats.wilson_ci(k, n)
    if h.compare_to == "baseline_50":
        baseline, label, b_n = 0.5, "50% (coin flip)", None
        p_value = stats.binomial_test(k, n, 0.5)
    elif h.compare_to == "complement":
        baseline = k_c / n_c if n_c else float("nan")
        label, b_n = "bouts where the condition is false", n_c
        p_value = stats.two_proportion_test(k, n, k_c, n_c)
    else:
        baseline = k_all / n_all if n_all else float("nan")
        label, b_n = "all bouts in this population", n_all
        p_value = stats.binomial_test(k, n, baseline) if n and 0 < baseline < 1 else 1.0

    d = (
        stats.describe_difference(rate, baseline)
        if n and baseline == baseline
        else stats.Difference(float("nan"), None)
    )
    warnings = []
    if n < SMALL_N:
        warnings.append(
            f"Small sample: only {n} matching bout-sides (fewer than {SMALL_N}). Treat as anecdote."
        )
    if missing:
        warnings.append(
            f"{missing} bout-sides excluded because a condition value was missing (e.g. no reach or DOB on record)."
        )
    if h.outcome == "won" and h.compare_to == "baseline_50" and not h.condition:
        warnings.append(
            "With no condition, every bout counts twice and the win rate is 50% by construction."
        )

    bout_ids: list[str] = []
    if keep_bouts and n:
        bout_ids = [
            r[0]
            for r in con.execute(
                f"SELECT bout_id FROM ({base}) WHERE {not_null} AND ({cond_sql}) ORDER BY event_date DESC",
                pop_params + cond_params,
            ).fetchall()
        ]
    return HypothesisResult(
        hypothesis_id=h.id,
        n=n,
        k=k,
        rate=rate,
        ci_low=lo,
        ci_high=hi,
        baseline=baseline,
        baseline_label=label,
        diff_pp=d.pp,
        diff_relative=d.relative,
        p_value=p_value,
        excluded_missing=missing,
        verdict=_verdict(h, n, k, rate, lo, hi, baseline, d, p_value),
        warnings=warnings,
        bout_ids=bout_ids,
        baseline_n=b_n,
    )


def _verdict(
    h: Hypothesis,
    n: int,
    k: int,
    rate: float,
    lo: float,
    hi: float,
    baseline: float,
    d: stats.Difference,
    p: float,
) -> str:
    if n == 0:
        return "No bouts match this hypothesis."
    what = OUTCOMES[h.outcome].lower()
    direction = "above" if d.pp > 0 else "below"
    rel = f" ({d.relative:+.0%} relative)" if d.relative is not None else ""
    sig = (
        f"Statistically significant (p = {p:.3f})."
        if p < 0.05
        else f"Not statistically significant (p = {p:.2f})."
    )
    return (
        f"{k} of {n} matching fighters {what}: {rate:.0%} (95% CI {lo:.0%}–{hi:.0%}). "
        f"That is {abs(d.pp):.1f} percentage points {direction} the {baseline:.0%} baseline{rel}. {sig}"
    )


def sweep(con: duckdb.DuckDBPyConnection, h: Hypothesis) -> pd.DataFrame:
    """Re-run the hypothesis across thresholds of `h.sweep.feature`, replacing any clause on it."""
    if h.sweep is None:
        raise ValueError("hypothesis has no sweep section")
    rows = []
    for t in np.arange(h.sweep.start, h.sweep.stop + 1e-9, h.sweep.step):
        clauses = [c for c in h.condition if c.feature != h.sweep.feature]
        clauses.append(Clause(feature=h.sweep.feature, op=h.sweep.op, value=float(t)))
        r = evaluate(con, h.model_copy(update={"condition": clauses}), keep_bouts=False)
        rows.append(
            {
                "threshold": float(t),
                "n": r.n,
                "rate": r.rate,
                "ci_low": r.ci_low,
                "ci_high": r.ci_high,
                "baseline": r.baseline,
                "p_value": r.p_value,
            }
        )
    return pd.DataFrame(rows)


def matches_upcoming(con: duckdb.DuckDBPyConnection, h: Hypothesis) -> pd.DataFrame:
    """Upcoming bout-sides where the hypothesis condition currently holds."""
    cond_sql, cond_params, _ = _condition_sql(h.condition)
    where, params = ["status = 'scheduled'"], []
    if h.population.divisions:
        where.append(f"division IN ({', '.join('?' for _ in h.population.divisions)})")
        params += h.population.divisions
    if h.population.sex:
        where.append("sex = ?")
        params.append(h.population.sex)
    return con.execute(
        f"""SELECT bout_id, event_name, event_date, fighter_id, fighter_name, opp_name
            FROM bout_sides WHERE {" AND ".join(where)} AND ({cond_sql}) ORDER BY event_date""",
        params + cond_params,
    ).df()
