"""`bout_sides`: every bout twice, once from each fighter's side, with self_/opp_/diff_ columns.

This removes red/blue corner bias and turns "the fighter with more X" into a plain filter.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from fightdb.features.stat_meta import feature_keys
from fightdb.models.matchup import MATCHUP_COLS, project
from fightdb.models.ratings import SCHEDULE_ADJUSTED

_BOUTS_SQL = """
SELECT b.bout_id, b.event_id, e.name AS event_name, e.date AS event_date, e.location,
       b.weight_class, b.division, b.sex, b.is_title, b.scheduled_rounds, b.card_order,
       b.status, b.fighter_a_id, b.fighter_b_id, b.result_a, b.method_group, b.method,
       b.end_round, b.fight_seconds
FROM bouts b JOIN events e USING (event_id)
WHERE b.status IN ('scheduled', 'completed') AND e.date IS NOT NULL
"""


# Days between a fighter first appearing on an event's card (ESPN) and the event. Only known for
# fighters added after card tracking began; those already on the card at the first import get NULL
# (unknown), never 0. Completed bouts are linked to the ESPN bout from the same night by fighter,
# or, for debutants ESPN listed without a UFC history ("tba:" IDs), by their opponent.
_NOTICE_SQL = """
WITH espn AS (
    SELECT b.bout_id, b.event_id, e.date, b.fighter_a_id AS fa, b.fighter_b_id AS fb,
           b.first_seen_at, b.status
    FROM bouts b JOIN events e USING (event_id)
    WHERE b.bout_id LIKE 'espn:%' AND e.date IS NOT NULL
),
start AS (SELECT CAST(min(first_seen_at) AS DATE) AS d FROM espn),
espn_sides AS (
    SELECT bout_id, event_id, date, fa AS f, fb AS o, first_seen_at, status FROM espn
    UNION ALL
    SELECT bout_id, event_id, date, fb, fa, first_seen_at, status FROM espn
),
first_on_card AS (
    SELECT event_id, f, min(first_seen_at) AS first_seen FROM espn_sides GROUP BY ALL
),
notice AS (
    SELECT s.bout_id, s.f, s.o, s.date, s.status,
           CASE WHEN CAST(c.first_seen AS DATE) > (SELECT d FROM start)
                THEN date_diff('day', CAST(c.first_seen AS DATE), s.date) END AS days_notice
    FROM espn_sides s JOIN first_on_card c USING (event_id, f)
    WHERE s.status <> 'cancelled'
),
hist AS (
    SELECT b.bout_id, e.date, b.fighter_a_id AS f, b.fighter_b_id AS o
    FROM bouts b JOIN events e USING (event_id)
    WHERE b.status = 'completed' AND e.date >= (SELECT d FROM start)
    UNION ALL
    SELECT b.bout_id, e.date, b.fighter_b_id, b.fighter_a_id
    FROM bouts b JOIN events e USING (event_id)
    WHERE b.status = 'completed' AND e.date >= (SELECT d FROM start)
),
linked AS (
    SELECT bout_id, f AS fighter_id, days_notice FROM notice WHERE status = 'scheduled'
    UNION ALL
    SELECT h.bout_id, h.f, n.days_notice
    FROM hist h JOIN notice n ON n.f = h.f AND abs(date_diff('day', n.date, h.date)) <= 1
    UNION ALL
    SELECT h.bout_id, h.f, n.days_notice
    FROM hist h JOIN notice n
      ON n.o = h.o AND n.f LIKE 'tba:%' AND abs(date_diff('day', n.date, h.date)) <= 1
)
SELECT bout_id, fighter_id, max(days_notice) AS days_notice FROM linked GROUP BY ALL
"""
SHORT_NOTICE_DAYS = 14


def days_notice(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    df = con.execute(_NOTICE_SQL).df()
    df["days_notice"] = df["days_notice"].astype(float)
    return df


def build_bout_sides(
    con: duckdb.DuckDBPyConnection, snap: pd.DataFrame, proj: pd.DataFrame | None = None
) -> pd.DataFrame:
    bouts = con.execute(_BOUTS_SQL).df()
    names = con.execute("SELECT fighter_id, name, stance FROM fighters").df()

    flip = {"W": "L", "L": "W", "D": "D", "NC": "NC"}
    a = bouts.assign(
        fighter_id=bouts["fighter_a_id"],
        opp_id=bouts["fighter_b_id"],
        result=bouts["result_a"],
        is_a=True,
    )
    b = bouts.assign(
        fighter_id=bouts["fighter_b_id"],
        opp_id=bouts["fighter_a_id"],
        result=bouts["result_a"].map(flip),
        is_a=False,
    )
    sides = pd.concat([a, b], ignore_index=True).drop(
        columns=["fighter_a_id", "fighter_b_id", "result_a"]
    )
    sides["event_date"] = pd.to_datetime(sides["event_date"])
    sides["year"] = sides["event_date"].dt.year

    sides = sides.merge(
        names.rename(columns={"name": "fighter_name", "stance": "self_stance"}),
        on="fighter_id",
        how="left",
    )
    sides = sides.merge(
        names.rename(columns={"fighter_id": "opp_id", "name": "opp_name", "stance": "opp_stance"}),
        on="opp_id",
        how="left",
    )

    snap = snap.drop(columns=["days_notice"], errors="ignore").merge(
        days_notice(con), on=["bout_id", "fighter_id"], how="left"
    )
    sides = attach_features(sides, snap, proj)

    odds = con.execute(
        "SELECT bout_id, fighter_id, implied_prob_close AS self_implied_prob FROM odds"
    ).df()
    sides = sides.merge(odds, on=["bout_id", "fighter_id"], how="left")

    sides = _outcomes(sides)
    for p in ("self", "opp"):
        known = sides[f"{p}_days_notice"].notna()
        sides[f"{p}_short_notice"] = np.where(
            known, (sides[f"{p}_days_notice"] < SHORT_NOTICE_DAYS).astype(float), np.nan
        )
    for p in ("self", "opp"):
        for stance in ("Southpaw", "Orthodox"):
            value = sides[f"{p}_stance"]
            sides[f"{p}_{stance.lower()}"] = np.where(
                value.isin(["Southpaw", "Orthodox", "Switch", "Open Stance", "Sideways"]),
                (value == stance).astype(float),
                np.nan,
            )
    return sides


def hypothetical_sides(current: pd.DataFrame, a_id: str, b_id: str) -> pd.DataFrame:
    """Both sides of an imagined bout between two fighters, from their `fighter_current` rows,
    with the same self_/opp_/diff_ and projected columns the models use for real bouts."""
    snap = current[current["fighter_id"].isin([a_id, b_id])].assign(bout_id="matchup")
    pairs = pd.DataFrame({"bout_id": "matchup", "fighter_id": [a_id, b_id], "opp_id": [b_id, a_id]})
    proj = project(snap, pairs)
    return attach_features(pairs.assign(is_a=[True, False]), snap, proj)


def attach_features(
    sides: pd.DataFrame, snap: pd.DataFrame, proj: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Add self_/opp_/diff_ snapshot columns (and projections) to rows keyed by
    bout_id, fighter_id and opp_id."""
    keys = [k for k in feature_keys() if k in snap.columns]
    raw_idx = [f"{c}_raw" for c in SCHEDULE_ADJUSTED if f"{c}_raw" in snap.columns]
    extra = [c for c in ("moving_division", *raw_idx) if c in snap.columns]
    cols = keys + extra
    s_self = snap[["bout_id", "fighter_id", *cols]].rename(columns={k: f"self_{k}" for k in cols})
    s_opp = snap[["bout_id", "fighter_id", *cols]].rename(
        columns={"fighter_id": "opp_id", **{k: f"opp_{k}" for k in cols}}
    )
    sides = sides.merge(s_self, on=["bout_id", "fighter_id"], how="left")
    sides = sides.merge(s_opp, on=["bout_id", "opp_id"], how="left")
    diffs = {f"diff_{k}": sides[f"self_{k}"] - sides[f"opp_{k}"] for k in keys + raw_idx}
    sides = pd.concat([sides, pd.DataFrame(diffs)], axis=1)

    if proj is not None and not proj.empty:
        p_self = proj[["bout_id", "fighter_id", *MATCHUP_COLS]].rename(
            columns={c: f"self_{c}" for c in MATCHUP_COLS}
        )
        p_opp = proj[["bout_id", "fighter_id", *MATCHUP_COLS]].rename(
            columns={"fighter_id": "opp_id", **{c: f"opp_{c}" for c in MATCHUP_COLS}}
        )
        sides = sides.merge(p_self, on=["bout_id", "fighter_id"], how="left").merge(
            p_opp, on=["bout_id", "opp_id"], how="left"
        )
    return sides


def _outcomes(s: pd.DataFrame) -> pd.DataFrame:
    done = s["status"] == "completed"
    res, mg = s["result"], s["method_group"]

    def flag(cond: pd.Series) -> pd.Series:
        return pd.Series(np.where(done, cond.astype(float), np.nan), index=s.index)

    s["won"] = flag(res == "W")
    s["lost"] = flag(res == "L")
    s["draw"] = flag(res == "D")
    s["no_contest"] = flag(res == "NC")
    s["won_ko"] = flag((res == "W") & (mg == "ko_tko"))
    s["won_sub"] = flag((res == "W") & (mg == "sub"))
    s["won_dec"] = flag((res == "W") & (mg == "dec"))
    s["lost_ko"] = flag((res == "L") & (mg == "ko_tko"))
    s["lost_sub"] = flag((res == "L") & (mg == "sub"))
    s["went_distance"] = flag(mg == "dec")
    s["finished_r1"] = flag(mg.isin(["ko_tko", "sub"]) & (s["end_round"] == 1))
    return s
