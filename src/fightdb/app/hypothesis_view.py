"""Saved hypotheses on the Bout page: what each pattern says, and whether it matters here."""

from __future__ import annotations

import math

import pandas as pd

from fightdb.analytics.hypothesis import HypothesisResult
from fightdb.analytics.schema import Hypothesis
from fightdb.features.stat_meta import BY_KEY, OUTCOMES
from fightdb.models import outcome

_OUTCOME_VERB = {
    "won": "won",
    "won_ko": "won by KO/TKO",
    "won_sub": "won by submission",
    "won_dec": "won by decision",
    "lost_ko": "lost by KO/TKO",
    "lost_sub": "lost by submission",
    "went_distance": "went the distance",
    "finished_r1": "were finished in round 1",
}


def _model_keys(target: str) -> set[str]:
    """Underlying stat keys a model sees, e.g. "age_years" or "idx_sub_threat"."""
    feats = (
        outcome.DIFF_FEATURES
        if target == "won"
        else outcome.METHOD_DIFF_FEATURES + outcome.LEVEL_FEATURES
    )
    return {
        f.removeprefix("diff_").removeprefix("self_").removeprefix("opp_").removesuffix("_raw")
        for f in feats
    }


def model_inputs_used(h: Hypothesis) -> list[str]:
    """Labels of the hypothesis's condition features that the relevant model already uses
    (the win model for "won", the method model for everything else)."""
    used = _model_keys(h.outcome)
    out = []
    for clause in h.condition:
        key = clause.feature.removeprefix("diff_").removeprefix("self_").removeprefix("opp_")
        if key in used:
            label = BY_KEY[key].label if key in BY_KEY else key
            if label not in out:
                out.append(label)
    return out


def so_what(h: Hypothesis, r: HypothesisResult, fighter: str, model_p: float) -> str:
    """One plain sentence on what the pattern means for this fight."""
    if not r.significant:
        return f"No reliable pattern (p = {r.p_value:.2f}). Ignore it."
    direction = "better" if r.diff_pp > 0 else "worse"
    if h.outcome in ("lost_ko", "lost_sub"):
        direction = "worse" if r.diff_pp > 0 else "better"
    used = model_inputs_used(h)
    chance = "" if math.isnan(model_p) else f" {model_p:.0%}"
    if used:
        return (
            f"Historically {direction} for {fighter}, but the model already uses "
            f"{', '.join(u.lower() for u in used)}, so this is already in its{chance} "
            "number. Not extra evidence."
        )
    return (
        f"Historically {direction} for {fighter}, and the model doesn't use this, so its"
        f"{chance} number doesn't reflect it. Extra context, but it may overlap with what the "
        "model does use, so it's a nudge rather than a correction."
    )


def row(h: Hypothesis, r: HypothesisResult, fighter: str, model_p: float) -> dict[str, object]:
    used = model_inputs_used(h)
    return {
        "Pattern": h.title,
        "Applies to": fighter,
        "History": f"{r.rate:.0%} {_OUTCOME_VERB.get(h.outcome, OUTCOMES.get(h.outcome, ''))}"
        f" (of {r.n:,})",
        "Usual rate": f"{r.baseline:.0%}",
        "Gap": f"{r.diff_pp:+.0f} pts",
        "Reliable?": "Yes" if r.significant else "No",
        "In the model?": "Yes" if used else "No",
        "So what": so_what(h, r, fighter, model_p),
    }


def table(rows: list[dict[str, object]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    # Reliable patterns the model doesn't already see are the only ones that add anything.
    order = (df["Reliable?"] == "Yes").astype(int) * 2 + (df["In the model?"] == "No").astype(int)
    return df.assign(_o=order).sort_values("_o", ascending=False).drop(columns="_o")
