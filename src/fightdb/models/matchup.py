"""Matchup projections ("xG" for MMA): combine one fighter's offence with the other's defence.

Rates combine multiplicatively against the division baseline (A x B / league), and
probabilities combine with the odds-ratio ("log5") method. See docs/ANALYSIS_IDEAS.md §1.1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

ROUND_MIN = 5.0


def odds(p: pd.Series | float) -> pd.Series | float:
    return p / (1 - p)


def log5(p_off: pd.Series | float, p_def_allowed: pd.Series | float, p_league: pd.Series | float):
    """Probability of success when an attacker with rate `p_off` meets a defender who allows
    `p_def_allowed`, in a league where the average is `p_league`."""
    o = odds(p_off) * odds(p_def_allowed) / odds(p_league)
    return o / (1 + o)


def project(snap: pd.DataFrame, pairs: pd.DataFrame) -> pd.DataFrame:
    """Per (bout_id, fighter_id): projected per-round numbers for `fighter_id` vs `opp_id`.

    `pairs` has bout_id, fighter_id, opp_id. `snap` is fighter_snapshots.
    """
    a = pairs.merge(snap, on=["bout_id", "fighter_id"], how="left")
    b = pairs[["bout_id", "opp_id"]].merge(
        snap.rename(columns={"fighter_id": "opp_id"}), on=["bout_id", "opp_id"], how="left"
    )
    a = a.set_index(["bout_id", "fighter_id"])
    b.index = a.index

    out = pd.DataFrame(index=a.index)
    with np.errstate(divide="ignore", invalid="ignore"):
        # Takedowns
        td_att_p15 = a["td_att_p15_s"] * b["opp_td_att_p15_s"] / (a["b_td_att_pm"] * 15)
        p_td = log5(a["td_acc_s"], b["td_allowed_s"], a["b_td_acc"])
        out["p_td_success"] = p_td
        out["x_td_att_r"] = td_att_p15 / 3
        out["x_td_r"] = td_att_p15 * p_td / 3
        # Significant strikes
        sig_att_pm = a["sig_att_pm_s"] * b["opp_sig_att_pm_s"] / a["b_sig_att_pm"]
        p_sig = log5(a["sig_acc_s"], b["sig_allowed_s"], a["b_sig_acc"])
        out["p_sig_land"] = p_sig
        out["x_sig_r"] = sig_att_pm * p_sig * ROUND_MIN
        # Control time and knockdowns
        out["x_ctrl_s_r"] = np.minimum(out["x_td_r"] * a["ctrl_per_td_s"], ROUND_MIN * 60)
        out["x_kd_r"] = (
            out["x_sig_r"] * a["kd_per_sig_s"] * (b["kd_abs_per_sig_s"] / a["b_kd_per_sig"])
        )
        out["x_sub_att_r"] = a["sub_att_p15_s"] / 3 * (b["td_allowed_s"] / a["b_td_acc"])
    return out.replace([np.inf, -np.inf], np.nan).reset_index()


MATCHUP_COLS = [
    "p_td_success",
    "x_td_att_r",
    "x_td_r",
    "p_sig_land",
    "x_sig_r",
    "x_ctrl_s_r",
    "x_kd_r",
    "x_sub_att_r",
]
