from datetime import date

from fightdb.pipeline import upsert
from fightdb.sources.models import Event, Fighter, HistoryBundle
from tests.conftest import make_bout, make_round


def bundle(name="Event 1"):
    return HistoryBundle(
        source="t",
        events=[
            Event(event_id="e1", name=name, date=date(2024, 1, 1), status="completed", source="t")
        ],
        fighters=[
            Fighter(fighter_id="a", name="A", reach_cm=180, source="t"),
            Fighter(fighter_id="b", name="B", source="t"),
        ],
        bouts=[make_bout("b1", "e1", "a", "b")],
        rounds=[make_round("b1", "a"), make_round("b1", "b")],
    )


def test_idempotent(con):
    upsert.upsert_history(con, bundle())
    first_seen = con.execute("SELECT first_seen_at FROM bouts").fetchone()[0]
    upsert.upsert_history(con, bundle("Renamed"))
    assert con.execute("SELECT count(*) FROM bouts").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM round_stats").fetchone()[0] == 2
    assert con.execute("SELECT name FROM events").fetchone()[0] == "Renamed"
    assert con.execute("SELECT first_seen_at FROM bouts").fetchone()[0] == first_seen


def test_fighter_merge_keeps_known_values(con):
    upsert.upsert_history(con, bundle())
    later = HistoryBundle(
        source="t2",
        fighters=[Fighter(fighter_id="a", name="A", reach_cm=None, stance="Southpaw", source="t2")],
    )
    upsert.upsert_history(con, later)
    reach, stance = con.execute(
        "SELECT reach_cm, stance FROM fighters WHERE fighter_id = 'a'"
    ).fetchone()
    assert reach == 180 and stance == "Southpaw"


def test_judge_rounds_view(con):
    from fightdb.pipeline.build import JUDGE_ROUNDS_VIEW
    from fightdb.sources import fight_canon
    from tests.conftest import FIXTURES

    bundle = fight_canon.load(FIXTURES / "canon_mini.db", "test")
    upsert.upsert_history(con, bundle)
    upsert.upsert_history(con, bundle)
    assert con.execute("SELECT count(*) FROM judge_scores").fetchone() == (
        len(bundle.judge_scores),
    )
    con.execute(JUDGE_ROUNDS_VIEW)
    rows = con.execute(
        "SELECT margin, won_round, sig_l_diff FROM judge_rounds WHERE bout_id = '1714950933f88ab9'"
    ).fetchall()
    assert len(rows) == 18
    # Every round is scored from both sides: margins mirror and exactly one side wins a 10-9.
    assert sum(r[0] for r in rows) == 0
    assert all(r[2] is not None for r in rows)
