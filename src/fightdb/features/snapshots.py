"""Point-in-time fighter snapshots: each fighter's numbers as they stood before each bout.

Only bouts on strictly earlier dates count (`merge_asof(..., allow_exact_matches=False)`), so
a snapshot never sees its own bout or anything after it. Scheduled bouts use all completed
history. Division baselines use the trailing three years before the bout.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from fightdb.models import ratings, shrinkage

STAT_COLS = ["kd", "sig_l", "sig_a", "td_l", "td_a", "sub_att", "ctrl_s"]
BASELINE_WINDOW = pd.DateOffset(years=3)
BASELINE_MIN_FIGHTS = 60

COUNT_COLS = [
    "fights",
    "wins",
    "losses",
    "draws",
    "ncs",
    "ko_wins",
    "sub_wins",
    "dec_wins",
    "ko_losses",
    "sub_losses",
    "seconds",
    "stat_seconds",
    "kd",
    "sig_l",
    "sig_a",
    "td_l",
    "td_a",
    "sub_att",
    "ctrl_s",
    "opp_kd",
    "opp_sig_l",
    "opp_sig_a",
    "opp_td_l",
    "opp_td_a",
    "opp_sub_att",
    "opp_ctrl_s",
    "pace_r1",
    "pace_r3",
]

_SIDES_SQL = """
WITH totals AS (
    SELECT bout_id, fighter_id,
           sum(kd) AS kd, sum(sig_l) AS sig_l, sum(sig_a) AS sig_a, sum(td_l) AS td_l,
           sum(td_a) AS td_a, sum(sub_att) AS sub_att, sum(ctrl_s) AS ctrl_s,
           sum(CASE WHEN round = 1 THEN sig_l END) AS r1_sig_l,
           sum(CASE WHEN round = 3 THEN sig_l END) AS r3_sig_l
    FROM round_stats GROUP BY ALL
),
base AS (
    SELECT b.*, e.date, e.name AS event_name FROM bouts b JOIN events e USING (event_id)
    WHERE b.status IN ('scheduled', 'completed') AND e.date IS NOT NULL
),
sides AS (
    SELECT bout_id, event_id, event_name, date, division, sex, status, method_group, end_round,
           fight_seconds, fighter_a_id AS fighter_id, fighter_b_id AS opp_id, result_a AS result
    FROM base
    UNION ALL
    SELECT bout_id, event_id, event_name, date, division, sex, status, method_group, end_round,
           fight_seconds, fighter_b_id, fighter_a_id,
           CASE result_a WHEN 'W' THEN 'L' WHEN 'L' THEN 'W' ELSE result_a END
    FROM base
)
SELECT s.*,
       t.kd, t.sig_l, t.sig_a, t.td_l, t.td_a, t.sub_att, t.ctrl_s, t.r1_sig_l, t.r3_sig_l,
       o.kd AS opp_kd, o.sig_l AS opp_sig_l, o.sig_a AS opp_sig_a, o.td_l AS opp_td_l,
       o.td_a AS opp_td_a, o.sub_att AS opp_sub_att, o.ctrl_s AS opp_ctrl_s,
       (t.bout_id IS NOT NULL AND o.bout_id IS NOT NULL) AS has_stats
FROM sides s
LEFT JOIN totals t ON t.bout_id = s.bout_id AND t.fighter_id = s.fighter_id
LEFT JOIN totals o ON o.bout_id = s.bout_id AND o.fighter_id = s.opp_id
"""


def load_sides(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    df = con.execute(_SIDES_SQL).df()
    df["date"] = pd.to_datetime(df["date"])
    return df


def _history_rows(sides: pd.DataFrame) -> pd.DataFrame:
    """Per completed (bout, fighter): counts that accumulate into career totals."""
    h = sides[(sides["status"] == "completed") & sides["result"].notna()].copy()
    res, mg = h["result"], h["method_group"]
    h["fights"] = 1
    h["wins"] = (res == "W").astype(int)
    h["losses"] = (res == "L").astype(int)
    h["draws"] = (res == "D").astype(int)
    h["ncs"] = (res == "NC").astype(int)
    h["ko_wins"] = ((res == "W") & (mg == "ko_tko")).astype(int)
    h["sub_wins"] = ((res == "W") & (mg == "sub")).astype(int)
    h["dec_wins"] = ((res == "W") & (mg == "dec")).astype(int)
    h["ko_losses"] = ((res == "L") & (mg == "ko_tko")).astype(int)
    h["sub_losses"] = ((res == "L") & (mg == "sub")).astype(int)
    h["seconds"] = h["fight_seconds"].fillna(0)
    stats = h["has_stats"].astype(bool)
    h["stat_seconds"] = np.where(stats, h["seconds"], 0)
    for c in STAT_COLS + [f"opp_{c}" for c in STAT_COLS]:
        h[c] = np.where(stats, h[c].fillna(0), 0)
    # Pace: only fights that completed round 3 (went past it, or went the distance).
    went_r3 = stats & ((h["end_round"] > 3) | ((h["end_round"] == 3) & (mg == "dec")))
    h["pace_r1"] = np.where(went_r3, h["r1_sig_l"].fillna(0), 0)
    h["pace_r3"] = np.where(went_r3, h["r3_sig_l"].fillna(0), 0)
    return h


def _career_by_day(h: pd.DataFrame) -> pd.DataFrame:
    """Cumulative totals per fighter after each fight day, plus streak and last division."""
    day = h.groupby(["fighter_id", "date"], as_index=False)[COUNT_COLS].sum()
    day = day.sort_values(["fighter_id", "date"])
    cum = day.groupby("fighter_id")[COUNT_COLS].cumsum()
    out = pd.concat([day[["fighter_id", "date"]], cum], axis=1)

    last = (
        h.sort_values(["fighter_id", "date", "bout_id"])
        .groupby(["fighter_id", "date"], as_index=False)
        .agg(day_results=("result", list), last_division=("division", "last"))
    )
    streaks = []
    for _, g in last.groupby("fighter_id", sort=False):
        s = 0
        for results in g["day_results"]:
            for r in results:
                if r == "W":
                    s = s + 1 if s > 0 else 1
                elif r == "L":
                    s = s - 1 if s < 0 else -1
                elif r == "D":
                    s = 0
            streaks.append(s)
    last["streak"] = streaks
    out = out.merge(
        last[["fighter_id", "date", "streak", "last_division"]], on=["fighter_id", "date"]
    )
    out["last_date"] = out["date"]
    return out


def _asof(left: pd.DataFrame, right: pd.DataFrame, by: str, left_on: str = "date") -> pd.DataFrame:
    left = left.sort_values(left_on)
    right = right.sort_values("date")
    merged = pd.merge_asof(
        left,
        right,
        left_on=left_on,
        right_on="date",
        by=by,
        allow_exact_matches=False,
        suffixes=("", "_r"),
    )
    return merged.drop(columns=[c for c in merged.columns if c == "date_r"])


BASE_COLS = [
    "fights",
    "stat_seconds",
    "kd",
    "sig_l",
    "sig_a",
    "td_l",
    "td_a",
    "sub_att",
    "ctrl_s",
    "ko_losses",
    "pace_r1",
    "pace_r3",
]


def _division_baselines(h: pd.DataFrame, queries: pd.DataFrame) -> pd.DataFrame:
    """Trailing-window pooled rates per division as of each query date (`b_*` columns)."""
    rows = h.copy()
    rows["division"] = rows["division"].fillna("unknown")
    allrows = rows.assign(division="__all__")
    daily = (
        pd.concat([rows, allrows]).groupby(["division", "date"], as_index=False)[BASE_COLS].sum()
    )
    daily = daily.sort_values(["division", "date"])
    cum = pd.concat(
        [daily[["division", "date"]], daily.groupby("division")[BASE_COLS].cumsum()], axis=1
    )

    q = queries[["bout_id", "fighter_id", "date", "division"]].copy()
    q["division"] = q["division"].fillna("unknown")
    q["date_start"] = q["date"] - BASELINE_WINDOW

    def window(group_col: pd.Series) -> pd.DataFrame:
        qq = q.assign(grp=group_col)
        c = cum.rename(columns={"division": "grp"})
        end = _asof(qq, c, by="grp")
        start = _asof(qq, c, by="grp", left_on="date_start")
        end = end.set_index(["bout_id", "fighter_id"])
        start = start.set_index(["bout_id", "fighter_id"]).reindex(end.index)
        return end[BASE_COLS].fillna(0) - start[BASE_COLS].fillna(0)

    div_w = window(q["division"])
    all_w = window(pd.Series("__all__", index=q.index))
    use_all = div_w["fights"] < BASELINE_MIN_FIGHTS
    w = div_w.where(~use_all, all_w)
    # Early history: if even the all-division window is thin, widen to everything before.
    thin = w["fights"] < BASELINE_MIN_FIGHTS
    if thin.any():
        allcum = cum[cum["division"] == "__all__"].drop(columns="division")
        qq = q.assign(grp="__all__")
        full = _asof(qq, allcum.assign(grp="__all__"), by="grp").set_index(
            ["bout_id", "fighter_id"]
        )
        w = w.where(~thin, full[BASE_COLS].reindex(w.index).fillna(0))

    mins = w["stat_seconds"] / 60
    b = pd.DataFrame(index=w.index)
    with np.errstate(divide="ignore", invalid="ignore"):
        b["b_slpm"] = w["sig_l"] / mins
        b["b_sig_att_pm"] = w["sig_a"] / mins
        b["b_sig_acc"] = w["sig_l"] / w["sig_a"]
        b["b_td_att_pm"] = w["td_a"] / mins
        b["b_td_p15"] = w["td_l"] / mins * 15
        b["b_td_acc"] = w["td_l"] / w["td_a"]
        b["b_sub_p15"] = w["sub_att"] / mins * 15
        b["b_sub_pm"] = w["sub_att"] / mins
        b["b_kd_pm"] = w["kd"] / mins
        b["b_kd_per_sig"] = w["kd"] / w["sig_l"]
        b["b_ctrl_per_td"] = w["ctrl_s"] / w["td_l"]
        b["b_ko_loss_rate"] = w["ko_losses"] / w["fights"]
        b["b_pace"] = w["pace_r3"] / w["pace_r1"]
    return b.replace([np.inf, -np.inf], np.nan).reset_index()


_PRIOR_SPECS = {
    # name: (kind, numerator, exposure, min exposure in career totals)
    "sig_acc": ("prop", "sig_l", "sig_a", 20),
    "sig_allowed": ("prop", "opp_sig_l", "opp_sig_a", 20),
    "td_acc": ("prop", "td_l", "td_a", 3),
    "td_allowed": ("prop", "opp_td_l", "opp_td_a", 3),
    "slpm": ("rate", "sig_l", "minutes", 5),
    "sapm": ("rate", "opp_sig_l", "minutes", 5),
    "sig_att_pm": ("rate", "sig_a", "minutes", 5),
    "td_att_pm": ("rate", "td_a", "minutes", 5),
    "opp_td_att_pm": ("rate", "opp_td_a", "minutes", 5),
    "sub_pm": ("rate", "sub_att", "minutes", 5),
    "kd_pm": ("rate", "kd", "minutes", 5),
    "kd_per_sig": ("prop", "kd", "sig_l", 20),
    "kd_abs_per_sig": ("prop", "opp_kd", "opp_sig_l", 20),
}


def compute_priors(career_final: pd.DataFrame, fights: pd.DataFrame) -> dict[str, float]:
    """Shrinkage prior strengths from final career totals, corrected for overdispersion
    estimated from fight-level counts."""
    c = career_final.assign(minutes=career_final["stat_seconds"] / 60)
    f = fights[fights["stat_seconds"] > 0].assign(minutes=lambda d: d["stat_seconds"] / 60)
    out: dict[str, float] = {}
    for name, (kind, x, t, min_t) in _PRIOR_SPECS.items():
        phi = shrinkage.overdispersion(f, x, t, kind)
        if kind == "prop":
            out[name] = shrinkage.estimate_k_proportion(c[x], c[t], min_n=min_t, phi=phi)
        else:
            out[name] = shrinkage.estimate_k_rate(c[x], c[t], min_t=min_t, phi=phi)
    out["ctrl_per_td"] = 5.0
    out["pace"] = 30.0
    return out


def build_snapshots(con: duckdb.DuckDBPyConnection) -> tuple[pd.DataFrame, dict[str, float]]:
    sides = load_sides(con)
    h = _history_rows(sides)
    career = _career_by_day(h)

    q = sides[["bout_id", "fighter_id", "opp_id", "date", "division", "status"]].copy()
    s = _asof(q, career, by="fighter_id")
    s[COUNT_COLS] = s[COUNT_COLS].fillna(0)

    fighters = con.execute("SELECT fighter_id, dob, height_cm, reach_cm, stance FROM fighters").df()
    fighters["dob"] = pd.to_datetime(fighters["dob"])
    s = s.merge(fighters, on="fighter_id", how="left")

    base = _division_baselines(h, q)
    s = s.merge(base, on=["bout_id", "fighter_id"], how="left")

    final = career.sort_values("date").groupby("fighter_id").tail(1)
    priors = compute_priors(final, h)
    s = _derive(s, priors)

    elo_hist, elo_now = ratings.compute_elo(
        _completed_bouts(con).assign(date=lambda d: pd.to_datetime(d["date"]))
    )
    s = s.merge(elo_hist, on=["bout_id", "fighter_id"], how="left")
    sched = s["status"] == "scheduled"
    if sched.any():
        s.loc[sched, "elo"] = (
            s.loc[sched, "fighter_id"].map(elo_now).astype(float).fillna(ratings.ELO_START)
        )
    s["elo"] = s["elo"].fillna(ratings.ELO_START)

    s = ratings.add_indices(s)
    return s.drop(columns=["status", "opp_id"]), priors


def _completed_bouts(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute(
        """SELECT b.bout_id, e.date, b.fighter_a_id, b.fighter_b_id, b.result_a, b.method_group
           FROM bouts b JOIN events e USING (event_id)
           WHERE b.status = 'completed' AND e.date IS NOT NULL"""
    ).df()


def _derive(s: pd.DataFrame, k: dict[str, float]) -> pd.DataFrame:
    s = s.copy()
    mins = s["stat_seconds"] / 60
    has = mins > 0
    with np.errstate(divide="ignore", invalid="ignore"):
        s["age_years"] = (s["date"] - s["dob"]).dt.days / 365.25
        s["ape_index_cm"] = s["reach_cm"] - s["height_cm"]
        s["ufc_fights"] = s["fights"]
        s["ufc_wins"] = s["wins"]
        s["ufc_losses"] = s["losses"]
        decided = s["wins"] + s["losses"]
        s["win_pct"] = np.where(decided > 0, s["wins"] / decided, np.nan)
        s["finish_rate"] = np.where(
            s["wins"] > 0, (s["ko_wins"] + s["sub_wins"]) / s["wins"], np.nan
        )
        s["streak"] = s["streak"].fillna(0)
        s["days_since_last"] = (s["date"] - s["last_date"]).dt.days
        s["cage_minutes"] = s["seconds"] / 60
        s["moving_division"] = (
            s["last_division"].notna() & (s["last_division"] != s["division"])
        ).astype(int)

        def per(x: pd.Series, scale: float = 1.0) -> pd.Series:
            return pd.Series(np.where(has, x / mins * scale, np.nan), index=s.index)

        def frac(x: pd.Series, n: pd.Series) -> pd.Series:
            return pd.Series(np.where(n > 0, x / n, np.nan), index=s.index)

        s["slpm"] = per(s["sig_l"])
        s["sapm"] = per(s["opp_sig_l"])
        s["sig_acc"] = frac(s["sig_l"], s["sig_a"])
        s["sig_def"] = 1 - frac(s["opp_sig_l"], s["opp_sig_a"])
        s["str_diff"] = s["slpm"] - s["sapm"]
        s["kd_p15"] = per(s["kd"], 15)
        s["td_att_p15"] = per(s["td_a"], 15)
        s["td_p15"] = per(s["td_l"], 15)
        s["td_acc"] = frac(s["td_l"], s["td_a"])
        s["td_def"] = 1 - frac(s["opp_td_l"], s["opp_td_a"])
        s["sub_att_p15"] = per(s["sub_att"], 15)
        s["ctrl_p15"] = per(s["ctrl_s"] / 60, 15)

    sh = shrinkage.shrink
    s["sig_acc_s"] = sh(s["sig_l"], s["sig_a"], s["b_sig_acc"], k["sig_acc"])
    s["sig_allowed_s"] = sh(s["opp_sig_l"], s["opp_sig_a"], s["b_sig_acc"], k["sig_allowed"])
    s["sig_def_s"] = 1 - s["sig_allowed_s"]
    s["td_acc_s"] = sh(s["td_l"], s["td_a"], s["b_td_acc"], k["td_acc"])
    s["td_allowed_s"] = sh(s["opp_td_l"], s["opp_td_a"], s["b_td_acc"], k["td_allowed"])
    s["td_def_s"] = 1 - s["td_allowed_s"]
    s["slpm_s"] = sh(s["sig_l"], mins, s["b_slpm"], k["slpm"])
    s["sapm_s"] = sh(s["opp_sig_l"], mins, s["b_slpm"], k["sapm"])
    s["sig_att_pm_s"] = sh(s["sig_a"], mins, s["b_sig_att_pm"], k["sig_att_pm"])
    s["opp_sig_att_pm_s"] = sh(s["opp_sig_a"], mins, s["b_sig_att_pm"], k["sig_att_pm"])
    s["td_att_p15_s"] = 15 * sh(s["td_a"], mins, s["b_td_att_pm"], k["td_att_pm"])
    s["opp_td_att_p15_s"] = 15 * sh(s["opp_td_a"], mins, s["b_td_att_pm"], k["opp_td_att_pm"])
    s["sub_att_p15_s"] = 15 * sh(s["sub_att"], mins, s["b_sub_pm"], k["sub_pm"])
    s["kd_p15_s"] = 15 * sh(s["kd"], mins, s["b_kd_pm"], k["kd_pm"])
    s["kd_per_sig_s"] = sh(s["kd"], s["sig_l"], s["b_kd_per_sig"], k["kd_per_sig"])
    s["kd_abs_per_sig_s"] = sh(s["opp_kd"], s["opp_sig_l"], s["b_kd_per_sig"], k["kd_abs_per_sig"])
    s["ctrl_per_td_s"] = sh(s["ctrl_s"], s["td_l"], s["b_ctrl_per_td"], k["ctrl_per_td"])
    s["pace_s"] = sh(s["pace_r3"], s["pace_r1"], s["b_pace"], k["pace"])
    return s
