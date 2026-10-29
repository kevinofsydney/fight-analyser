from datetime import date

import pandas as pd
import pytest

from fightdb.features import snapshots
from fightdb.pipeline import upsert
from fightdb.sources.models import Bout, Event, Fighter, HistoryBundle
from tests.conftest import make_bout, make_round


def _history():
    fighters = [
        Fighter(
            fighter_id=x,
            name=x.upper(),
            dob=date(1990, 1, 1),
            reach_cm=180,
            height_cm=175,
            source="t",
        )
        for x in "abcd"
    ]
    events = [
        Event(event_id="e1", name="E1", date=date(2020, 1, 1), status="completed", source="t"),
        Event(event_id="e2", name="E2", date=date(2020, 6, 1), status="completed", source="t"),
        Event(event_id="e3", name="E3", date=date(2021, 1, 1), status="completed", source="t"),
        Event(event_id="u1", name="U1", date=date(2030, 1, 1), status="upcoming", source="t"),
    ]
    bouts = [
        make_bout("b1", "e1", "a", "b", "W", "ko_tko", seconds=150),
        make_bout("b2", "e1", "c", "d", "L", "dec"),  # same day as b1
        make_bout("b3", "e2", "a", "c", "W", "dec"),
        make_bout("b4", "e3", "a", "d", "L", "sub", seconds=400),
        Bout(
            bout_id="u",
            event_id="u1",
            weight_class="Lightweight Bout",
            division="lightweight",
            sex="M",
            fighter_a_id="a",
            fighter_b_id="b",
            status="scheduled",
            source="t",
        ),
    ]
    rounds = [
        make_round("b1", "a", 1, sig_l=20, sig_a=30, td_l=1, td_a=2, kd=1),
        make_round("b1", "b", 1, sig_l=5, sig_a=20, td_l=0, td_a=1),
        make_round("b2", "c", 1),
        make_round("b2", "d", 1),
        make_round("b3", "a", 1, sig_l=10, sig_a=40, td_l=2, td_a=4),
        make_round("b3", "c", 1, sig_l=8, sig_a=16, td_l=0, td_a=3),
        make_round("b4", "a", 1),
        make_round("b4", "d", 1),
    ]
    return HistoryBundle(source="t", events=events, fighters=fighters, bouts=bouts, rounds=rounds)


@pytest.fixture
def snap(con):
    upsert.upsert_history(con, _history())
    s, _ = snapshots.build_snapshots(con)
    return s.set_index(["bout_id", "fighter_id"])


def test_debut_has_no_rates(snap):
    row = snap.loc[("b1", "a")]
    assert row["ufc_fights"] == 0
    assert pd.isna(row["slpm"]) and pd.isna(row["td_acc"]) and pd.isna(row["days_since_last"])
    # Shrunk values fall back to the division baseline (or are missing when no baseline exists yet).
    assert row["streak"] == 0


def test_hand_computed_values(snap):
    row = snap.loc[
        ("b3", "a")
    ]  # after b1 only: 150 s, 20/30 sig, 1/2 TD, 1 KD, opponent 5/20 sig, 0/1 TD
    assert row["ufc_fights"] == 1 and row["ufc_wins"] == 1 and row["streak"] == 1
    assert row["slpm"] == pytest.approx(20 / 2.5)
    assert row["sapm"] == pytest.approx(5 / 2.5)
    assert row["sig_acc"] == pytest.approx(20 / 30)
    assert row["sig_def"] == pytest.approx(1 - 5 / 20)
    assert row["td_acc"] == pytest.approx(0.5)
    assert row["td_def"] == pytest.approx(1.0)
    assert row["kd_p15"] == pytest.approx(1 / 2.5 * 15)
    assert row["finish_rate"] == 1.0
    assert row["days_since_last"] == (date(2020, 6, 1) - date(2020, 1, 1)).days
    assert row["age_years"] == pytest.approx(
        (pd.Timestamp("2020-06-01") - pd.Timestamp("1990-01-01")).days / 365.25
    )

    row4 = snap.loc[("b4", "a")]  # after b1 + b3
    assert row4["ufc_fights"] == 2 and row4["streak"] == 2
    assert row4["sig_acc"] == pytest.approx(30 / 70)


def test_no_lookahead(con, snap):
    """Every snapshot counts exactly the fighter's completed bouts on strictly earlier dates."""
    df = con.execute(
        """SELECT s.bout_id, s.fighter_id, count(h.bout_id) AS prior
           FROM (SELECT b.bout_id, e.date, unnest([b.fighter_a_id, b.fighter_b_id]) AS fighter_id
                 FROM bouts b JOIN events e USING (event_id) WHERE b.status IN ('completed', 'scheduled')) s
           LEFT JOIN (SELECT b.bout_id, e.date, unnest([b.fighter_a_id, b.fighter_b_id]) AS fighter_id
                      FROM bouts b JOIN events e USING (event_id) WHERE b.status = 'completed') h
             ON h.fighter_id = s.fighter_id AND h.date < s.date
           GROUP BY ALL"""
    ).df()
    for r in df.itertuples():
        assert snap.loc[(r.bout_id, r.fighter_id), "ufc_fights"] == r.prior, (
            r.bout_id,
            r.fighter_id,
        )


def test_same_day_bouts_do_not_see_each_other(snap):
    assert snap.loc[("b2", "c"), "ufc_fights"] == 0


def test_scheduled_uses_all_history(snap):
    row = snap.loc[("u", "a")]
    assert row["ufc_fights"] == 3 and row["streak"] == -1
    assert row["elo"] != 1500
