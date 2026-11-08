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


def test_submission_defence(con):
    from fightdb.models import shrinkage

    h = _history()
    # d attempts 4 submissions in b4 and finishes a with one of them.
    rounds = [r for r in h.rounds if not (r.bout_id == "b4" and r.fighter_id == "d")]
    rounds.append(make_round("b4", "d", 1, sub_att=4))
    upsert.upsert_history(con, h.model_copy(update={"rounds": rounds}))
    s, k = snapshots.build_snapshots(con)
    row = s.set_index(["bout_id", "fighter_id"]).loc[("u", "a")]
    assert row["stat_sub_losses"] == 1 and row["opp_sub_att"] == 4
    expected = shrinkage.shrink(
        pd.Series([1.0]),
        pd.Series([4.0]),
        pd.Series([row["b_sub_loss_per_att"]]),
        k["sub_loss_per_att"],
    )[0]
    assert row["sub_loss_per_att_s"] == pytest.approx(expected)
    assert row["idx_sub_def"] == pytest.approx(
        100 * row["b_sub_loss_per_att"] / row["sub_loss_per_att_s"]
    )
    debut = s.set_index(["bout_id", "fighter_id"]).loc[("b1", "a")]
    assert debut["stat_sub_losses"] == 0 and debut["opp_sub_att"] == 0


def test_sub_loss_without_recorded_attempt_is_capped(snap):
    # In the base history d finishes a by submission in b4 with no attempt recorded.
    row = snap.loc[("u", "a")]
    assert row["sub_losses"] == 1
    assert row["stat_sub_losses"] == 0


def test_future_fights_never_change_past_snapshots(con):
    """Look-ahead guard for every snapshot column, including strength of schedule and indices:
    rewriting all fights after a cutoff must leave snapshots up to the cutoff unchanged."""
    import duckdb

    from tests.conftest import synthetic_history

    hist = synthetic_history(n_bouts=240, seed=3)
    cutoff = sorted({e.date for e in hist.events})[20]
    upsert.upsert_history(con, hist)
    before, _ = snapshots.build_snapshots(con)

    late = {
        b.bout_id
        for b in hist.bouts
        if next(e.date for e in hist.events if e.event_id == b.event_id) > cutoff
    }
    altered = [
        r.model_copy(update={"sig_l": 40, "sig_a": 41, "td_l": 3, "td_a": 3, "ctrl_s": 299})
        if r.bout_id in late
        else r
        for r in hist.rounds
    ]
    con2 = duckdb.connect(":memory:")
    from fightdb.db.conn import init_db

    init_db(con2)
    upsert.upsert_history(con2, hist.model_copy(update={"rounds": altered}))
    after, _ = snapshots.build_snapshots(con2)

    early = (
        before[before["date"] <= pd.Timestamp(cutoff)]
        .set_index(["bout_id", "fighter_id"])
        .sort_index()
    )
    early_after = after.set_index(["bout_id", "fighter_id"]).loc[early.index].sort_index()
    cols = [c for c in early.columns if c.startswith(("sos_", "idx_", "slpm", "td_acc"))]
    assert {"sos_str_leak", "idx_str_off", "idx_str_off_raw"} <= set(cols)
    pd.testing.assert_frame_equal(early[cols], early_after[cols])
    # ...and the rewrite does reach later snapshots, so the test can fail.
    later = before[before["date"] > pd.Timestamp(cutoff)].set_index(["bout_id", "fighter_id"]).index
    assert (
        not before.set_index(["bout_id", "fighter_id"])
        .loc[later, "slpm"]
        .equals(after.set_index(["bout_id", "fighter_id"]).loc[later, "slpm"])
    )


def test_schedule_adjustment_direction():
    from fightdb.models import ratings

    s = pd.DataFrame(
        {
            "idx_str_off": [100.0, 100.0],
            "idx_str_def": [100.0, 100.0],
            "idx_wrest_off": [100.0, 100.0],
            "idx_wrest_def": [100.0, 100.0],
            # first fighter faced leaky, dangerous opponents; second faced average ones
            "sos_str_leak": [1.25, 1.0],
            "sos_str_threat": [1.2, 1.0],
            "sos_td_leak": [1.25, 1.0],
            "sos_td_threat": [1.2, 1.0],
        }
    )
    out = ratings.adjust_for_schedule(s)
    assert out.loc[0, "idx_str_off"] == pytest.approx(80)  # easy opponents: offence counts less
    assert out.loc[0, "idx_str_def"] == pytest.approx(
        120
    )  # dangerous opponents: defence counts more
    assert out.loc[1, "idx_wrest_off"] == 100 and out.loc[0, "idx_str_off_raw"] == 100
