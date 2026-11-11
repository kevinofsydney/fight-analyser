from datetime import date

import duckdb
import numpy as np
import pandas as pd
import pytest

from fightdb.db.conn import init_db
from fightdb.features import snapshots
from fightdb.models import ratings
from fightdb.pipeline import upsert
from fightdb.sources.models import ProFight
from tests.conftest import synthetic_history
from tests.features.test_snapshots import _history


def _pro(fight_id, day, a, b, result="W", finish=False):
    return ProFight(
        fight_id=fight_id,
        date=day,
        promotion="Regional",
        fighter_a_id=a,
        fighter_b_id=b,
        result_a=result,
        is_finish=finish,
    )


def _frame(fights):
    return pd.DataFrame([f.model_dump() for f in fights])


def test_pro_rating_counts_record_and_rewards_the_winner():
    state = ratings.compute_pro_ratings(
        _frame(
            [
                _pro("1", date(2019, 1, 1), "a", "x"),
                _pro("2", date(2019, 2, 1), "a", "y", "D"),
                _pro("3", date(2019, 3, 1), "z", "a", "W", finish=True),
            ]
        )
    ).set_index(["fighter_id", "date"])
    first = state.loc[("a", date(2019, 1, 1))]
    k_first = ratings.PRO_K + ratings.PRO_K_EARLY
    assert first["pro_elo"] == pytest.approx(ratings.ELO_START + k_first * 0.5)
    assert state.loc[("x", date(2019, 1, 1)), "pro_elo"] == pytest.approx(
        ratings.ELO_START - k_first * 0.5
    )
    last = state.loc[("a", date(2019, 3, 1))]
    assert (last["pro_fights"], last["pro_wins"]) == (3, 1)
    assert last["pro_elo"] < state.loc[("a", date(2019, 2, 1)), "pro_elo"]


def test_pro_rating_moves_less_for_an_established_fighter():
    fights = [_pro(str(i), date(2015, 1, 1 + i), "vet", f"x{i}", "D") for i in range(25)]
    fights.append(_pro("late", date(2016, 1, 1), "vet", "new"))
    state = ratings.compute_pro_ratings(_frame(fights)).set_index(["fighter_id", "date"])
    vet_gain = (
        state.loc[("vet", date(2016, 1, 1)), "pro_elo"]
        - state.loc[("vet", date(2015, 1, 25)), "pro_elo"]
    )
    new_loss = ratings.ELO_START - state.loc[("new", date(2016, 1, 1)), "pro_elo"]
    assert 0 < vet_gain < new_loss / 2


def test_same_day_pro_fights_use_ratings_from_before_that_day():
    day = date(2018, 5, 5)
    state = ratings.compute_pro_ratings(
        _frame([_pro("1", day, "a", "b"), _pro("2", day, "a", "c")])
    ).set_index("fighter_id")
    k = ratings.PRO_K + ratings.PRO_K_EARLY
    # Both wins are scored against a 1500-rated "a"; the second does not see the first.
    assert state.loc["a", "pro_elo"] == pytest.approx(ratings.ELO_START + 2 * k * 0.5)
    assert state.loc["a", "pro_fights"] == 2


def test_snapshot_pro_record_is_pre_fight_and_unknown_without_history(con):
    hist = _history().model_copy(
        update={
            "pro_fights": [
                _pro("r1", date(2018, 1, 1), "a", "fc-fighter:1", finish=True),
                _pro("r2", date(2019, 1, 1), "a", "fc-fighter:2", "L"),
                _pro("b1", date(2020, 1, 1), "a", "b", finish=True),  # the UFC bout b1 itself
            ]
        }
    )
    upsert.upsert_history(con, hist)
    s, _ = snapshots.build_snapshots(con)
    s = s.set_index(["bout_id", "fighter_id"])

    debut = s.loc[("b1", "a")]
    assert debut["pro_fights"] == 2  # its own bout on the same day is not counted
    assert debut["pro_win_pct"] == pytest.approx((1 + 5) / (2 + 10))
    assert debut["pro_elo"] != ratings.ELO_START
    assert s.loc[("b3", "a"), "pro_fights"] == 3
    # No recorded pro fight before the bout: unknown, never "zero fights at 1500".
    assert np.isnan(s.loc[("b1", "b"), "pro_elo"])
    assert np.isnan(s.loc[("b2", "c"), "pro_fights"])
    assert np.isnan(s.loc[("b2", "c"), "pro_win_pct"])
    # Scheduled bouts use everything up to now.
    assert s.loc[("u", "b"), "pro_fights"] == 1


def test_future_pro_fights_never_change_past_pro_record(con):
    """Look-ahead guard: rewriting every pro result after a cutoff leaves earlier snapshots alone."""
    hist = synthetic_history(n_bouts=240, seed=3)
    days = {e.event_id: e.date for e in hist.events}
    pro = [
        _pro(b.bout_id, days[b.event_id], b.fighter_a_id, b.fighter_b_id, b.result_a)
        for b in hist.bouts
    ]
    cutoff = sorted(set(days.values()))[20]
    flipped = [
        p.model_copy(update={"result_a": "L" if p.result_a == "W" else "W", "is_finish": True})
        if p.date > cutoff
        else p
        for p in pro
    ]

    def build(fights):
        c = duckdb.connect(":memory:")
        init_db(c)
        upsert.upsert_history(c, hist.model_copy(update={"pro_fights": fights}))
        out, _ = snapshots.build_snapshots(c)
        return out.set_index(["bout_id", "fighter_id"]).sort_index()

    before, after = build(pro), build(flipped)
    cols = ["pro_elo", "pro_fights", "pro_win_pct"]
    # A bout on the first day after the cutoff still only sees results up to the cutoff.
    first_late = min(d for d in days.values() if d > cutoff)
    early = before.index[before["date"] <= pd.Timestamp(first_late)]
    pd.testing.assert_frame_equal(before.loc[early, cols], after.loc[early, cols])
    assert before.loc[early, "pro_elo"].notna().any()
    late = before.index[before["date"] > pd.Timestamp(first_late)]
    assert not before.loc[late, "pro_elo"].equals(after.loc[late, "pro_elo"])
