"""Smoke tests: every page renders against a small database without raising."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from fightdb.config import Settings, get_settings
from fightdb.pipeline import upsert
from fightdb.pipeline.build import build_all
from tests.conftest import synthetic_history

APP = Path(__file__).resolve().parents[2] / "src" / "fightdb" / "app"
PAGES = [APP / "Home.py", *sorted((APP / "pages").glob("*.py"))]


@pytest.fixture(scope="module")
def db(tmp_path_factory, monkeypatch_module):
    import duckdb

    from fightdb.db.conn import init_db

    path = tmp_path_factory.mktemp("app") / "fightdb.duckdb"
    con = duckdb.connect(str(path))
    init_db(con)
    upsert.upsert_history(con, synthetic_history(n_bouts=650))
    con.execute(
        """INSERT INTO scrape_runs (run_id, kind, started_at, finished_at, status, source, counts)
           VALUES ('r1', 'refresh', now()::TIMESTAMP, now()::TIMESTAMP, 'ok', 'test', '{"unmatched": []}')"""
    )
    build_all(con, train_models=True)
    con.close()
    monkeypatch_module.setenv("DB_PATH", str(path))
    monkeypatch_module.setenv("DATA_RELEASE_URL", "")
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_renders(db, page):
    at = AppTest.from_file(str(page), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def test_settings_point_at_test_db(db):
    assert Settings().db_path == db


def test_date_formatting():
    from datetime import date, datetime

    import pandas as pd

    from fightdb.app.components import fmt_date, format_dates

    assert fmt_date(date(2027, 3, 31)) == "31st March 2027"
    assert fmt_date(date(2027, 3, 11)) == "11th March 2027"
    assert fmt_date(datetime(2027, 3, 4, 16, 26), with_time=True) == "4th March 2027, 4:26 PM"
    assert fmt_date(pd.NaT, with_time=True) == "—"
    frame = pd.DataFrame({"event_date": [pd.Timestamp("2027-03-31")]})
    assert format_dates(frame, dates=("event_date",)).iloc[0, 0] == "31st March 2027"
    assert frame.iloc[0, 0] == pd.Timestamp("2027-03-31")


def test_past_bout_shows_elo_and_round_stats(db):
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    bout_id = con.execute(
        "SELECT bout_id FROM bouts WHERE status = 'completed' LIMIT 1"
    ).fetchone()[0]
    con.close()
    at = AppTest.from_file(str(APP / "pages" / "1_Bout.py"), default_timeout=120)
    at.session_state["bout_id"] = bout_id
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.title[0].value == "Bout"
    assert any(b.label == "Reset" for b in at.button)
    assert any(e.label == "What goes into the model outlook?" for e in at.expander)
    assert any("Elo before fight" in m.label for m in at.metric)
    assert any(h.value == "Fight statistics by round" for h in at.subheader)


def test_fighter_wear_profile_renders(db):
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    fighter_id = con.execute("SELECT fighter_id FROM bout_sides LIMIT 1").fetchone()[0]
    con.close()
    at = AppTest.from_file(str(APP / "pages" / "4_Fighter.py"), default_timeout=120)
    at.session_state["fighter_selector"] = fighter_id
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(h.value == "Fight wear (UFC history)" for h in at.subheader)
    assert any(m.label == "Strikes absorbed" for m in at.metric)


def test_hypothesis_date_allows_recent_start(db):
    from datetime import date

    at = AppTest.from_file(str(APP / "pages" / "2_Hypothesis_Lab.py"), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.date_input[0].max == date.today()


def test_bout_reset_opens_next_event_main_fight(db, tmp_path, monkeypatch):
    import shutil

    import duckdb

    path = tmp_path / "reset.duckdb"
    shutil.copyfile(db, path)
    con = duckdb.connect(str(path))
    event_id = con.execute("SELECT event_id FROM events ORDER BY date DESC LIMIT 1").fetchone()[0]
    con.execute("UPDATE events SET status = 'upcoming' WHERE event_id = ?", [event_id])
    con.execute("UPDATE bout_sides SET status = 'scheduled' WHERE event_id = ?", [event_id])
    expected = con.execute(
        "SELECT bout_id FROM bout_sides WHERE event_id = ? AND is_a ORDER BY card_order LIMIT 1",
        [event_id],
    ).fetchone()[0]
    old = con.execute(
        "SELECT bout_id FROM bouts WHERE event_id != ? LIMIT 1", [event_id]
    ).fetchone()[0]
    con.close()
    monkeypatch.setenv("DB_PATH", str(path))
    get_settings.cache_clear()
    try:
        at = AppTest.from_file(str(APP / "pages" / "1_Bout.py"), default_timeout=120)
        at.session_state["bout_id"] = old
        at.run()
        at.button[0].click().run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.selectbox[0].value == event_id
        assert at.selectbox[1].value == expected
    finally:
        get_settings.cache_clear()
