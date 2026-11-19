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


# All-promotion rating. K starts high and settles as a fighter's record grows, so a prospect's
# rating moves quickly through the regional scene and a veteran's does not swing on one result.
# Walk-forward win-model log loss 2012+: UFC-only Elo 0.6548, this at a flat K of 32 0.6504,
# with the early boost 0.6468 (see NEXT_TASKS.md, 2026-11-11).
PRO_K = 20.0
PRO_K_EARLY = 40.0
PRO_K_FADE_FIGHTS = 10.0
PRO_COLUMNS = ["fighter_id", "date", "pro_elo", "pro_fights", "pro_wins"]


def compute_pro_ratings(fights: pd.DataFrame) -> pd.DataFrame:
    """Elo and record over every pro fight, any promotion.

    `fights` needs: date, fighter_a_id, fighter_b_id, result_a ('W' | 'L' | 'D'), is_finish.
    Returns one row per fighter per fight day with their rating and record *after* that day;
    join strictly-earlier rows to get the pre-fight state. Same-day fights all use ratings from
    before that day.
    """
    f = fights.sort_values(["date", "fighter_a_id", "fighter_b_id"])
    elo: dict[str, float] = {}
    n: dict[str, int] = {}
    wins: dict[str, int] = {}
    rows: list[tuple[str, object, float, int, int]] = []
    pending: dict[str, list[float]] = {}
    current: object = None

    def flush() -> None:
        for fid, (delta, fought, won) in pending.items():
            elo[fid] = elo.get(fid, ELO_START) + delta
            n[fid] = n.get(fid, 0) + int(fought)
            wins[fid] = wins.get(fid, 0) + int(won)
            rows.append((fid, current, elo[fid], n[fid], wins[fid]))
        pending.clear()

    for day, a, b, result, finish in zip(
        f["date"], f["fighter_a_id"], f["fighter_b_id"], f["result_a"], f["is_finish"], strict=True
    ):
        score = ELO_SCORES.get(result)
        if score is None:
            continue
        if day != current:
            flush()
            current = day
        surprise = score - elo_expected(elo.get(a, ELO_START), elo.get(b, ELO_START))
        mult = FINISH_MULT if finish else 1.0
        for fid, sign, won in ((a, 1.0, score == 1.0), (b, -1.0, score == 0.0)):
            k = PRO_K + PRO_K_EARLY * float(np.exp(-n.get(fid, 0) / PRO_K_FADE_FIGHTS))
            p = pending.setdefault(fid, [0.0, 0.0, 0.0])
            p[0] += sign * k * mult * surprise
            p[1] += 1
            p[2] += won
    flush()
    return pd.DataFrame(rows, columns=PRO_COLUMNS)


# Performance rating: Elo where the score is how much of the fight a fighter won (0-1), not just
# who got the result. A favourite who scrapes a split decision loses rating; a loser who won two
# rounds before being stopped loses less. K of 96, 150 and 250 scored the same in the
# walk-forward test, so this is the smallest of them (see NEXT_TASKS.md, 2026-11-13). It is far
# above ELO_K because a share of a fight moves less than a win or loss does.
PERF_K = 96.0
# Who the judges gave a round to, from that round's stat differences (logistic, no intercept).
# Fitted once on the 787 scored rounds before 2012, so it never sees the backtest years; it
# picks the judges' round winner in 83% of 11,360 rounds from 2012 on. Used for rounds with no
# scorecard (most finishes, and decisions the source has no cards for).
ROUND_WEIGHTS = {
    "kd": 0.89,
    "sig_l": 0.15,
    "tot_l": 0.03,
    "td_l": 0.58,
    "ctrl_min": 0.39,
    "sub_att": 0.15,
}


def round_win_prob(diffs: pd.DataFrame) -> pd.Series:
    """Chance the judges give a round to the fighter whose stat leads are in `diffs`
    (columns of ROUND_WEIGHTS: this fighter minus the opponent)."""
    z = pd.Series(0.0, index=diffs.index)
    for col, weight in ROUND_WEIGHTS.items():
        z += weight * diffs[col].astype(float)
    return 1.0 / (1.0 + np.exp(-z))


def bout_performance(bouts: pd.DataFrame, rounds: pd.DataFrame) -> pd.Series:
    """Fighter A's share of each bout, 0-1, indexed like `bouts`.

    `bouts` needs: bout_id, result_a, method_group, end_round, scheduled_rounds.
    `rounds` needs: bout_id, round, share (A's share of that round: the judges' cards where we
    have them, else `round_win_prob`).

    Decisions: the mean round share. Finishes: rounds before the last one count as fought, and
    the finisher takes the last round and every round that was never needed. Anything else
    (disqualifications, no stats at all): the plain result.
    """
    res = bouts["result_a"].map(ELO_SCORES).astype(float)
    r = rounds.merge(bouts[["bout_id", "end_round"]], on="bout_id")
    whole = r.groupby("bout_id")["share"].agg(["sum", "size"])
    early = r[r["round"] < r["end_round"]].groupby("bout_id")["share"].agg(["sum", "size"])
    ids = bouts["bout_id"]
    total, n = ids.map(whole["sum"]), ids.map(whole["size"])
    early_total = ids.map(early["sum"]).fillna(0.0)
    early_n = ids.map(early["size"]).fillna(0.0)
    length = np.maximum(bouts["scheduled_rounds"].fillna(3.0), bouts["end_round"].fillna(1.0))
    finish = (early_total + res * (length - early_n)) / length
    perf = res.copy()
    decision = (bouts["method_group"] == "dec") & n.notna()
    perf[decision] = (total / n)[decision]
    finished = bouts["method_group"].isin(ELO_FINISHES) & bouts["end_round"].notna()
    perf[finished] = finish[finished]
    return perf


def compute_perf_elo(bouts: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    """Elo on `perf_a` (fighter A's share of the bout, from `bout_performance`).

    `bouts` needs: bout_id, date, fighter_a_id, fighter_b_id, perf_a (NaN rows are skipped).
    Returns (pre-fight ratings per (bout_id, fighter_id), final ratings by fighter). Same-day
    bouts all use ratings from before that day.
    """
    ratings: dict[str, float] = {}
    rows: list[tuple[str, str, float]] = []
    for _, day in bouts.sort_values(["date", "bout_id"]).groupby("date", sort=True):
        pending: dict[str, float] = {}
        for b in day.itertuples(index=False):
            a, c = b.fighter_a_id, b.fighter_b_id
            ra, rc = ratings.get(a, ELO_START), ratings.get(c, ELO_START)
            rows.append((b.bout_id, a, ra))
            rows.append((b.bout_id, c, rc))
            if pd.isna(b.perf_a):
                continue
            delta = PERF_K * (float(b.perf_a) - elo_expected(ra, rc))
            pending[a] = pending.get(a, 0.0) + delta
            pending[c] = pending.get(c, 0.0) - delta
        for fid, d in pending.items():
            ratings[fid] = ratings.get(fid, ELO_START) + d
    return pd.DataFrame(rows, columns=["bout_id", "fighter_id", "perf_elo"]), ratings


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
# The schedule factors are averages of opponents' shrunk numbers, themselves shrunk towards 1.0,
# so they understate how soft or hard a schedule was. Squaring them gave the lowest walk-forward
# log loss (1.0 vs 1.5 / 2 / 3: 0.6556, 0.6550, 0.6548, 0.6552 on 2012+; 0.6452 vs 0.6437 at 2
# on 2020+) and kept every calibration gate passing.
SOS_POWER = 2.0


def adjust_for_schedule(s: pd.DataFrame) -> pd.DataFrame:
    """Opponent-adjust the striking and wrestling indices (ANALYSIS_IDEAS §1.3).

    Offence is divided by how leaky the opponents' defence was (landing a lot on fighters who
    get hit a lot counts for less); defence is multiplied by how dangerous the opponents'
    offence was. `sos_*` columns come from `snapshots._strength_of_schedule`; 1.0 = average.
    """
    s = s.copy()
    for c in SCHEDULE_ADJUSTED:
        s[f"{c}_raw"] = s[c]
    s["idx_str_off"] = s["idx_str_off"] / s["sos_str_leak"] ** SOS_POWER
    s["idx_str_def"] = s["idx_str_def"] * s["sos_str_threat"] ** SOS_POWER
    s["idx_wrest_off"] = s["idx_wrest_off"] / s["sos_td_leak"] ** SOS_POWER
    s["idx_wrest_def"] = s["idx_wrest_def"] * s["sos_td_threat"] ** SOS_POWER
    return s
