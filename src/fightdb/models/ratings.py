"""Elo ratings and the 100-centred fighter indices."""

from __future__ import annotations

import numpy as np
import pandas as pd

ELO_START = 1500.0
ELO_K = 32.0
FINISH_MULT = 1.25


ELO_SCORES = {"W": 1.0, "L": 0.0, "D": 0.5}
ELO_FINISHES = ("ko_tko", "sub")


def elo_expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def elo_k(method_group: object) -> float:
    return ELO_K * (FINISH_MULT if method_group in ELO_FINISHES else 1.0)


def elo_delta(ra: float, rb: float, result: object, method_group: object) -> float | None:
    """Rating change for the fighter rated `ra`; None for results that do not move Elo."""
    score = ELO_SCORES.get(result) if isinstance(result, str) else None
    if score is None:
        return None
    return elo_k(method_group) * (score - elo_expected(ra, rb))


def elo_breakdown(history: pd.DataFrame) -> pd.DataFrame:
    """Explain one fighter's Elo, fight by fight.

    `history` holds the fighter's completed bouts before the bout being explained, with
    event_date, opp_name, self_elo, opp_elo, result and method_group (pre-fight ratings).
    """
    rows = []
    for h in history.sort_values(["event_date", "bout_id"]).itertuples(index=False):
        delta = elo_delta(h.self_elo, h.opp_elo, h.result, h.method_group)
        rows.append(
            {
                "event_date": h.event_date,
                "opp_name": h.opp_name,
                "self_elo": h.self_elo,
                "opp_elo": h.opp_elo,
                "expected": elo_expected(h.self_elo, h.opp_elo),
                "result": h.result,
                "method_group": h.method_group,
                "k": elo_k(h.method_group) if delta is not None else None,
                "change": delta if delta is not None else 0.0,
                "elo_after": h.self_elo + (delta or 0.0),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "event_date",
            "opp_name",
            "self_elo",
            "opp_elo",
            "expected",
            "result",
            "method_group",
            "k",
            "change",
            "elo_after",
        ],
    )


def compute_elo(bouts: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    """Walk completed bouts in date order.

    `bouts` needs: bout_id, date, fighter_a_id, fighter_b_id, result_a, method_group.
    Returns (pre-fight ratings per (bout_id, fighter_id), final ratings by fighter).
    Same-day bouts all use ratings from before that day (early-UFC tournaments).
    """
    ratings: dict[str, float] = {}
    rows: list[tuple[str, str, float]] = []
    ordered = bouts.sort_values(["date", "bout_id"])
    for _, day in ordered.groupby("date", sort=True):
        pending: dict[str, float] = {}
        for b in day.itertuples(index=False):
            a, c = b.fighter_a_id, b.fighter_b_id
            ra, rc = ratings.get(a, ELO_START), ratings.get(c, ELO_START)
            rows.append((b.bout_id, a, ra))
            rows.append((b.bout_id, c, rc))
            delta = elo_delta(ra, rc, b.result_a, b.method_group)
            if delta is None:
                continue
            pending[a] = pending.get(a, 0.0) + delta
            pending[c] = pending.get(c, 0.0) - delta
        for fid, d in pending.items():
            ratings[fid] = ratings.get(fid, ELO_START) + d
    return pd.DataFrame(rows, columns=["bout_id", "fighter_id", "elo"]), ratings


def _ratio(num: pd.Series, den: pd.Series) -> pd.Series:
    out = 100.0 * num / den
    return out.replace([np.inf, -np.inf], np.nan)


def add_indices(s: pd.DataFrame) -> pd.DataFrame:
    """Indices from shrunk rates and division baselines (`b_*` columns). 100 = division average."""
    s = s.copy()
    s["idx_str_off"] = _ratio(s["slpm_s"], s["b_slpm"])
    s["idx_str_def"] = _ratio(s["b_slpm"], s["sapm_s"])
    s["idx_wrest_off"] = _ratio(s["td_att_p15_s"] * s["td_acc_s"], s["b_td_p15"])
    s["idx_wrest_def"] = _ratio(s["b_td_acc"], 1.0 - s["td_def_s"])
    s["idx_sub_threat"] = _ratio(s["sub_att_p15_s"], s["b_sub_p15"])
    s["idx_sub_def"] = _ratio(s["b_sub_loss_per_att"], s["sub_loss_per_att_s"])
    s["idx_durability"] = _ratio(s["b_kd_per_sig"], s["kd_abs_per_sig_s"])
    s["idx_pace"] = _ratio(s["pace_s"], s["b_pace"])
    return s


SCHEDULE_ADJUSTED = ("idx_str_off", "idx_str_def", "idx_wrest_off", "idx_wrest_def")


def adjust_for_schedule(s: pd.DataFrame) -> pd.DataFrame:
    """Opponent-adjust the striking and wrestling indices (ANALYSIS_IDEAS §1.3).

    Offence is divided by how leaky the opponents' defence was (landing a lot on fighters who
    get hit a lot counts for less); defence is multiplied by how dangerous the opponents'
    offence was. `sos_*` columns come from `snapshots._strength_of_schedule`; 1.0 = average.
    """
    s = s.copy()
    for c in SCHEDULE_ADJUSTED:
        s[f"{c}_raw"] = s[c]
    s["idx_str_off"] = s["idx_str_off"] / s["sos_str_leak"]
    s["idx_str_def"] = s["idx_str_def"] * s["sos_str_threat"]
    s["idx_wrest_off"] = s["idx_wrest_off"] / s["sos_td_leak"]
    s["idx_wrest_def"] = s["idx_wrest_def"] * s["sos_td_threat"]
    return s
