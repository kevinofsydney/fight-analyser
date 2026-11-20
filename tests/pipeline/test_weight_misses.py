from datetime import date
from pathlib import Path

import duckdb

from fightdb.pipeline import upsert, weight_misses
from fightdb.sources.models import Event, Fighter, HistoryBundle
from tests.conftest import make_bout

HEADER = "card_date,fighter_name,card_name,record_type,pounds_over,notes,source_url\n"


def _fighter(fid: str, name: str) -> Fighter:
    return Fighter(fighter_id=fid, name=name, sex="M", source="test")


def _db(con: duckdb.DuckDBPyConnection) -> None:
    events = [
        Event(event_id="e1", name="UFC 1", date=date(2024, 3, 2), status="completed", source="t"),
        # A stale second copy of the same card, as the old upcoming feed left behind.
        Event(event_id="e1b", name="UFC 1", date=date(2024, 3, 2), status="past", source="t"),
    ]
    fighters = [
        _fighter("f1", "Phil Rowe"),
        _fighter("f2", "Opponent One"),
        _fighter("f3", "Katlyn Cerminara"),
        _fighter("f4", "Opponent Two"),
        _fighter("f5", "Only Elsewhere"),
    ]
    bouts = [
        make_bout("b1", "e1", "f1", "f2"),
        make_bout("b2", "e1", "f3", "f4"),
        make_bout("b1b", "e1b", "f1", "f2").model_copy(update={"status": "past"}),
    ]
    upsert.upsert_history(
        con, HistoryBundle(source="t", events=events, fighters=fighters, bouts=bouts, rounds=[])
    )


def _write(tmp_path: Path, rows: str, names: str = "") -> Path:
    d = tmp_path / "overrides"
    d.mkdir()
    (d / weight_misses.FILE_NAME).write_text(HEADER + rows, encoding="utf-8")
    if names:
        (d / "fighter_identity.yaml").write_text(names, encoding="utf-8")
    return d


def test_links_close_spelling_to_completed_bout(con, tmp_path):
    _db(con)
    d = _write(tmp_path, "2024-03-02,Philip Rowe,UFC 1,official_miss,2.5,,https://x.test/1\n")
    assert weight_misses.load(con, d) == {"weight_misses": 1, "weight_misses_unlinked": 0}
    row = con.execute(
        "SELECT fighter_id, bout_id, opp_id, link, pounds_over FROM weight_misses"
    ).fetchone()
    assert row == ("f1", "b1", "f2", "bout", 2.5)


def test_name_override_and_cancelled_and_unknown(con, tmp_path):
    _db(con)
    rows = (
        "2024-03-02,Katlyn Chookagian,UFC 1,official_miss,,,https://x.test/2\n"
        "2024-05-01,Only Elsewhere,UFC 2,official_miss,3,Cancelled,https://x.test/3\n"
        "2024-05-01,Nobody Known,UFC 2,weight_unavailable,,Cancelled,https://x.test/4\n"
    )
    names = 'names:\n  "Katlyn Chookagian": "f3"\n'
    d = _write(tmp_path, rows, names)
    assert weight_misses.load(con, d)["weight_misses_unlinked"] == 1
    got = con.execute(
        "SELECT fighter_name, fighter_id, bout_id, link, pounds_over FROM weight_misses "
        "ORDER BY card_date, fighter_name"
    ).fetchall()
    assert got == [
        ("Katlyn Chookagian", "f3", "b2", "bout", None),
        ("Nobody Known", None, None, None, None),
        ("Only Elsewhere", "f5", None, "fighter", 3.0),
    ]


def test_missing_file_leaves_empty_table(con, tmp_path):
    assert weight_misses.load(con, tmp_path) == {"weight_misses": 0}
    assert con.execute("SELECT count(*) FROM weight_misses").fetchone() == (0,)


def test_checked_in_file_parses():
    rows = weight_misses.read_csv(Path("data/overrides") / weight_misses.FILE_NAME)
    assert len(rows) > 300
    assert all(r.card_date >= weight_misses.COVERAGE_START for r in rows)
    assert all(r.pounds_over is None or r.pounds_over > 0 for r in rows)
