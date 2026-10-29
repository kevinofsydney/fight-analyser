"""Elo ratings and the 100-centred fighter indices."""

from __future__ import annotations

import numpy as np
import pandas as pd

ELO_START = 1500.0
ELO_K = 32.0
FINISH_MULT = 1.25


def elo_expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


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
            score = {"W": 1.0, "L": 0.0, "D": 0.5}.get(b.result_a)
            if score is None:
                continue
            k = ELO_K * (FINISH_MULT if b.method_group in ("ko_tko", "sub") else 1.0)
            delta = k * (score - elo_expected(ra, rc))
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
    s["idx_durability"] = _ratio(s["b_kd_per_sig"], s["kd_abs_per_sig_s"])
    s["idx_pace"] = _ratio(s["pace_s"], s["b_pace"])
    return s
