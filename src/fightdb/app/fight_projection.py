"""Expected fight totals: the per-round matchup projections scaled by how long the fight should last."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

ROUND_SECONDS = 300


def _num(v: Any) -> float:
    try:
        return float(v)
    except TypeError, ValueError:
        return math.nan


def expected_rounds(p_distance: float, scheduled_rounds: int, mean_finish_seconds: float) -> float:
    """Expected rounds of fighting: the full distance with chance `p_distance`, otherwise the
    average length of a finished fight with this many scheduled rounds."""
    full = float(scheduled_rounds)
    if math.isnan(p_distance) or math.isnan(mean_finish_seconds):
        return full
    return p_distance * full + (1 - p_distance) * mean_finish_seconds / ROUND_SECONDS


def totals(a: pd.Series, b: pd.Series, rounds: float) -> pd.DataFrame:
    """Per fighter, the major stats expected over `rounds` rounds of fighting. `a` and `b` are
    bout_sides rows with the matchup projections (self_x_*_r, self_p_*)."""

    def side(s: pd.Series, o: pd.Series) -> dict[str, float]:
        sig = _num(s.get("self_x_sig_r")) * rounds
        acc = _num(s.get("self_p_sig_land"))
        td_att = _num(s.get("self_x_td_att_r")) * rounds
        td = _num(s.get("self_x_td_r")) * rounds
        opp_att = _num(o.get("self_x_td_att_r")) * rounds
        opp_td = _num(o.get("self_x_td_r")) * rounds
        return {
            "Significant strikes landed": sig,
            "Significant strikes thrown": sig / acc if acc > 0 else math.nan,
            "Takedowns landed": td,
            "Takedown attempts": td_att,
            "Takedowns defended": opp_att - opp_td,
            "Knockdowns": _num(s.get("self_x_kd_r")) * rounds,
            "Submission attempts": _num(s.get("self_x_sub_att_r")) * rounds,
            "Control time (min)": _num(s.get("self_x_ctrl_s_r")) * rounds / 60,
        }

    first, second = side(a, b), side(b, a)
    return pd.DataFrame(
        {
            "Stat": list(first),
            str(a.fighter_name): list(first.values()),
            str(b.fighter_name): list(second.values()),
        }
    )
