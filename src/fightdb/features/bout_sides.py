"""`bout_sides`: every bout twice, once from each fighter's side, with self_/opp_/diff_ columns.

This removes red/blue corner bias and turns "the fighter with more X" into a plain filter.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from fightdb.features.stat_meta import feature_keys
from fightdb.models.matchup import MATCHUP_COLS

_BOUTS_SQL = """
SELECT b.bout_id, b.event_id, e.name AS event_name, e.date AS event_date, e.location,
       b.weight_class, b.division, b.sex, b.is_title, b.scheduled_rounds, b.card_order,
       b.status, b.fighter_a_id, b.fighter_b_id, b.result_a, b.method_group, b.method,
       b.end_round, b.fight_seconds
FROM bouts b JOIN events e USING (event_id)
WHERE b.status IN ('scheduled', 'completed') AND e.date IS NOT NULL
"""


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

    keys = [k for k in feature_keys() if k in snap.columns]
    extra = [c for c in ("moving_division",) if c in snap.columns]
    cols = keys + extra
    s_self = snap[["bout_id", "fighter_id", *cols]].rename(columns={k: f"self_{k}" for k in cols})
    s_opp = snap[["bout_id", "fighter_id", *cols]].rename(
        columns={"fighter_id": "opp_id", **{k: f"opp_{k}" for k in cols}}
    )
    sides = sides.merge(s_self, on=["bout_id", "fighter_id"], how="left")
    sides = sides.merge(s_opp, on=["bout_id", "opp_id"], how="left")
    diffs = {f"diff_{k}": sides[f"self_{k}"] - sides[f"opp_{k}"] for k in keys}
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

    odds = con.execute(
        "SELECT bout_id, fighter_id, implied_prob_close AS self_implied_prob FROM odds"
    ).df()
    sides = sides.merge(odds, on=["bout_id", "fighter_id"], how="left")

    sides = _outcomes(sides)
    sides["self_southpaw"] = (sides["self_stance"] == "Southpaw").astype(int)
    sides["opp_southpaw"] = (sides["opp_stance"] == "Southpaw").astype(int)
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
