from datetime import date

import duckdb
import numpy as np
import pandas as pd
import pytest

from fightdb.db.conn import init_db
from fightdb.features import snapshots
from fightdb.models import ratings
from fightdb.pipeline import upsert
from fightdb.sources.models import JudgeScore
from tests.conftest import make_round, synthetic_history
from tests.features.test_snapshots import _history


def _bouts(rows):
    cols = ["bout_id", "result_a", "method_group", "end_round", "scheduled_rounds"]
    return pd.DataFrame(rows, columns=cols)


def _rounds(rows):
    return pd.DataFrame(rows, columns=["bout_id", "round", "share"])


def test_round_model_is_even_without_a_lead_and_symmetric():
    lead = pd.DataFrame(
        [
            dict.fromkeys(ratings.ROUND_WEIGHTS, 0.0),
            {**dict.fromkeys(ratings.ROUND_WEIGHTS, 0.0), "kd": 1},
        ]
    )
    p = ratings.round_win_prob(lead)
    assert p[0] == pytest.approx(0.5)
    assert p[1] > 0.5
    assert ratings.round_win_prob(-lead)[1] == pytest.approx(1 - p[1])


def test_bout_performance_by_method():
    bouts = _bouts(
        [
            ("dec", "W", "dec", 3, 3),  # won two rounds of three
            ("ko1", "W", "ko_tko", 1, 3),  # first-round finish: the whole fight
            ("late", "W", "sub", 3, 3),  # lost two rounds, then finished it
            ("lost", "L", "ko_tko", 2, 3),  # won the first, stopped in the second
            ("dq", "W", "dq", 2, 3),  # disqualification: the plain result
            ("bare", "L", "dec", 3, 3),  # no round data at all
            ("title", "W", "ko_tko", 2, 5),
        ]
    )
    rounds = _rounds(
        [
            ("dec", 1, 1.0),
            ("dec", 2, 0.0),
            ("dec", 3, 1.0),
            ("ko1", 1, 0.2),
            ("late", 1, 0.0),
            ("late", 2, 0.0),
            ("late", 3, 0.9),
            ("lost", 1, 1.0),
            ("lost", 2, 0.4),
            ("dq", 1, 0.0),
            ("dq", 2, 0.0),
            ("title", 1, 0.5),
            ("title", 2, 0.5),
        ]
    )
    perf = ratings.bout_performance(bouts, rounds).set_axis(bouts["bout_id"])
    assert perf["dec"] == pytest.approx(2 / 3)
    assert perf["ko1"] == pytest.approx(1.0)
    assert perf["late"] == pytest.approx(1 / 3)
    assert perf["lost"] == pytest.approx(1 / 3)
    assert perf["dq"] == pytest.approx(1.0)
    assert perf["bare"] == pytest.approx(0.0)
    assert perf["title"] == pytest.approx((0.5 + 4) / 5)


def _perf_bouts(rows):
    df = pd.DataFrame(rows, columns=["bout_id", "date", "fighter_a_id", "fighter_b_id", "perf_a"])
    df["date"] = pd.to_datetime(df["date"])
    return df


def test_perf_elo_rewards_the_margin_and_can_punish_a_narrow_win():
    _, now = ratings.compute_perf_elo(
        _perf_bouts(
            [
                ("1", "2020-01-01", "dominant", "x", 1.0),
                ("2", "2020-01-01", "narrow", "y", 0.6),
                ("3", "2020-01-01", "skipped", "z", np.nan),
            ]
        )
    )
    assert now["dominant"] == pytest.approx(ratings.ELO_START + ratings.PERF_K * 0.5)
    assert now["narrow"] == pytest.approx(ratings.ELO_START + ratings.PERF_K * 0.1)
    assert now["x"] == pytest.approx(ratings.ELO_START - ratings.PERF_K * 0.5)
    assert "skipped" not in now

    # A big favourite who only edges it did worse than expected, so the rating falls.
    hist, later = ratings.compute_perf_elo(
        _perf_bouts(
            [
                ("1", "2020-01-01", "fav", "x", 1.0),
                ("2", "2020-02-01", "fav", "y", 1.0),
                ("3", "2020-03-01", "fav", "new", 0.52),
            ]
        )
    )
    before = hist.set_index(["bout_id", "fighter_id"]).loc[("3", "fav"), "perf_elo"]
    assert later["fav"] < before
    assert later["new"] > ratings.ELO_START


def test_same_day_bouts_use_ratings_from_before_that_day():
    hist, now = ratings.compute_perf_elo(
        _perf_bouts([("1", "2020-01-01", "a", "b", 1.0), ("2", "2020-01-01", "a", "c", 1.0)])
    )
    assert (hist["perf_elo"] == ratings.ELO_START).all()
    assert now["a"] == pytest.approx(ratings.ELO_START + 2 * ratings.PERF_K * 0.5)


def test_judges_cards_beat_the_stat_model_and_ignore_deductions(con):
    hist = _history()
    first = hist.bouts[0]
    a, b = first.fighter_a_id, first.fighter_b_id
    cards = []
    for rnd in (1, 2, 3):
        # A takes every round 10-9, with a point deducted in round 2 (9-9 on the card).
        a_score, deduction = (9, 1) if rnd == 2 else (10, 0)
        cards.append(
            JudgeScore(
                bout_id=first.bout_id,
                fighter_id=a,
                round=rnd,
                judge="J",
                score=a_score,
                deduction=deduction,
            )
        )
        cards.append(JudgeScore(bout_id=first.bout_id, fighter_id=b, round=rnd, judge="J", score=9))
    # The stats say the opposite: B out-lands A heavily in every round.
    rounds = [r for r in hist.rounds if r.bout_id != first.bout_id]
    for rnd in (1, 2, 3):
        rounds.append(make_round(first.bout_id, a, rnd, sig_l=1, sig_a=10))
        rounds.append(make_round(first.bout_id, b, rnd, sig_l=40, sig_a=60, kd=1))
    decided = first.model_copy(update={"method_group": "dec", "end_round": 3})
    upsert.upsert_history(
        con,
        hist.model_copy(
            update={
                "judge_scores": cards,
                "rounds": rounds,
                "bouts": [decided, *hist.bouts[1:]],
            }
        ),
    )
    perf = snapshots._bout_performance(con).set_index("bout_id")
    assert perf.loc[first.bout_id, "perf_a"] == pytest.approx(1.0)


def test_snapshot_perf_elo_is_pre_fight(con):
    upsert.upsert_history(con, _history())
    s, _ = snapshots.build_snapshots(con)
    first_day = s["date"].min()
    assert (s.loc[s["date"] == first_day, "perf_elo"] == ratings.ELO_START).all()
    assert s["perf_elo"].notna().all()
    assert (s.loc[s["date"] > first_day, "perf_elo"] != ratings.ELO_START).any()


def test_future_fights_never_change_past_perf_elo():
    """Look-ahead guard: rewriting every result and stat after a cutoff leaves earlier
    snapshots alone."""
    hist = synthetic_history(n_bouts=240, seed=5)
    days = {e.event_id: e.date for e in hist.events}
    cutoff = sorted(set(days.values()))[20]
    late = {b.bout_id for b in hist.bouts if days[b.event_id] > cutoff}
    flipped_bouts = [
        b.model_copy(
            update={
                "result_a": "L" if b.result_a == "W" else "W",
                "winner_id": b.fighter_b_id if b.result_a == "W" else b.fighter_a_id,
                "method_group": "ko_tko",
                "end_round": 1,
            }
        )
        if b.bout_id in late
        else b
        for b in hist.bouts
    ]
    flipped_rounds = [
        r.model_copy(update={"sig_l": 0, "kd": 3}) if r.bout_id in late else r for r in hist.rounds
    ]

    def build(bundle):
        c = duckdb.connect(":memory:")
        init_db(c)
        upsert.upsert_history(c, bundle)
        out, _ = snapshots.build_snapshots(c)
        return out.set_index(["bout_id", "fighter_id"]).sort_index()

    before = build(hist)
    after = build(hist.model_copy(update={"bouts": flipped_bouts, "rounds": flipped_rounds}))
    first_late = min(d for d in days.values() if d > cutoff)
    early = before.index[before["date"] <= pd.Timestamp(first_late)]
    pd.testing.assert_series_equal(before.loc[early, "perf_elo"], after.loc[early, "perf_elo"])
    assert (before.loc[early, "perf_elo"] != ratings.ELO_START).any()
    later = before.index[before["date"] > pd.Timestamp(first_late)]
    assert not before.loc[later, "perf_elo"].equals(after.loc[later, "perf_elo"])
    assert date(2011, 1, 1) <= cutoff
