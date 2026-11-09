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
    assert sum(c.value == "Current pro record" for c in at.caption) == 2
    assert sum(c.value == "Age at bout" for c in at.caption) == 2
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


def test_saved_hypothesis_shows_matching_fights(db):
    at = AppTest.from_file(str(APP / "pages" / "3_Saved_Hypotheses.py"), default_timeout=120)
    at.session_state["hypothesis_1"] = True
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("Explore historical outcomes" in m.value for m in at.markdown)
    assert any("95% Wilson interval" in c.value for c in at.caption)
    assert any("One eligible fighter" in c.value for c in at.caption)
    assert any("Previous matching fights" in m.value for m in at.markdown)
    assert any("Matching fighter" in df.value.columns for df in at.dataframe)


def test_closing_odds_formatting():
    from fightdb.app.components import format_closing_range

    assert format_closing_range(-200, 150) == "1.50 to 2.50 decimal (-200 to +150 American)"
    assert format_closing_range(120, 120) == "2.20 decimal (+120 American)"
    assert format_closing_range(None, None) == "—"


def test_bout_shows_historical_closing_prices(db, tmp_path, monkeypatch):
    import shutil

    import duckdb

    path = tmp_path / "closing.duckdb"
    shutil.copyfile(db, path)
    con = duckdb.connect(str(path))
    bout_id, fighter_a, fighter_b = con.execute(
        "SELECT bout_id, fighter_a_id, fighter_b_id FROM bouts WHERE status = 'completed' LIMIT 1"
    ).fetchone()
    con.execute(
        "INSERT INTO odds (bout_id, fighter_id, close_low, close_high) VALUES (?, ?, ?, ?), (?, ?, ?, ?)",
        [bout_id, fighter_a, -200, -150, bout_id, fighter_b, 120, 150],
    )
    con.close()
    monkeypatch.setenv("DB_PATH", str(path))
    get_settings.cache_clear()
    try:
        at = AppTest.from_file(str(APP / "pages" / "1_Bout.py"), default_timeout=120)
        at.session_state["bout_id"] = bout_id
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert any("-200 to -150" in metric.value for metric in at.metric)
        assert any("+120 to +150" in metric.value for metric in at.metric)
    finally:
        get_settings.cache_clear()


def test_bout_shows_upcoming_bookmaker_prices(db, tmp_path, monkeypatch):
    import shutil

    import duckdb

    from fightdb.pipeline.odds import init_table

    path = tmp_path / "upcoming-odds.duckdb"
    shutil.copyfile(db, path)
    con = duckdb.connect(str(path))
    event_id, bout_id, fighter_a, fighter_b = con.execute(
        "SELECT event_id, bout_id, fighter_a_id, fighter_b_id FROM bouts LIMIT 1"
    ).fetchone()
    con.execute("UPDATE events SET status='upcoming' WHERE event_id = ?", [event_id])
    con.execute("UPDATE bout_sides SET status='scheduled' WHERE bout_id = ?", [bout_id])
    init_table(con)
    con.execute(
        "INSERT INTO upcoming_odds VALUES (?, ?, 'book', 'Example Book', 1.75, NULL, now()::TIMESTAMP), (?, ?, 'book', 'Example Book', 2.15, NULL, now()::TIMESTAMP)",
        [bout_id, fighter_a, bout_id, fighter_b],
    )
    con.close()
    monkeypatch.setenv("DB_PATH", str(path))
    get_settings.cache_clear()
    try:
        at = AppTest.from_file(str(APP / "pages" / "1_Bout.py"), default_timeout=120)
        at.session_state["bout_id"] = bout_id
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        tables = [df.value for df in at.dataframe if "Bookmaker" in df.value.columns]
        assert len(tables) == 1
        assert set(tables[0]["Decimal odds"]) == {"1.75", "2.15"}
    finally:
        get_settings.cache_clear()


def test_home_card_selection_opens_selected_bout(db, tmp_path, monkeypatch):
    import json
    import shutil

    import duckdb
    from streamlit.elements.arrow import DataframeSelectionSerde

    path = tmp_path / "home-card.duckdb"
    shutil.copyfile(db, path)
    con = duckdb.connect(str(path))
    event_id = con.execute("SELECT event_id FROM events ORDER BY date DESC LIMIT 1").fetchone()[0]
    con.execute("UPDATE events SET status = 'upcoming' WHERE event_id = ?", [event_id])
    bouts = con.execute(
        "SELECT bout_id FROM bout_sides WHERE event_id = ? AND is_a ORDER BY card_order", [event_id]
    ).fetchall()
    con.close()
    monkeypatch.setenv("DB_PATH", str(path))
    get_settings.cache_clear()
    try:
        at = AppTest.from_file(str(APP / "Home.py"), default_timeout=120)
        at.session_state[f"card_{event_id}"] = DataframeSelectionSerde().deserialize(
            json.dumps({"selection": {"rows": [1]}})
        )
        at.session_state["bout_event_selector"] = "stale-event"
        at.session_state["bout_selector"] = "stale-bout"
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["bout_id"] == bouts[1][0]
        assert at.session_state["bout_event_selector"] == event_id
        assert at.session_state["bout_selector"] == bouts[1][0]
    finally:
        get_settings.cache_clear()


def test_fighter_summary_formats_missing_data_and_portrait_safely():
    import pandas as pd

    from fightdb.app.components import fighter_portrait, odds_range, pro_record

    assert pro_record(10, 0, 2) == "10–0–2"
    assert pro_record(10, None, 0) == "—"
    assert odds_range(pd.Series([1.75, 2.15])) == "1.75–2.15"
    assert odds_range(pd.Series([], dtype=float)) == "—"
    portrait = fighter_portrait("Jane <Smith>", "12345")
    assert "players/full/12345.png" in portrait
    assert "&lt;Smith&gt;" in portrait
    assert "players/full" not in fighter_portrait("Fighter", "123' onload='alert(1)")


def test_chart_groups_count_each_bout_once_and_preserve_wins():
    import ast

    import pandas as pd

    from fightdb.analytics import stats

    page = APP / "pages" / "3_Saved_Hypotheses.py"
    module = ast.parse(page.read_text(encoding="utf-8"))
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "group_observations"
    )
    namespace = {"pd": pd, "stats": stats}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(page), "exec"), namespace)
    observations = pd.DataFrame(
        {
            "bout_id": ["a", "a", "b", "b", "c"],
            "fighter_id": ["x", "y", "x", "z", "x"],
            "feature_value": [4.0, -4.0, 1.0, -1.0, 4.0],
            "outcome_value": [1, 0, 0, 1, 1],
        }
    )
    fights, groups = namespace["group_observations"](observations)
    assert len(fights) == 3
    assert set(fights["fighter_id"]) == {"x"}
    assert groups["count"].sum() == 3
    assert groups["successes"].sum() == 2
    assert ((groups["ci_low"] <= groups["rate"]) & (groups["rate"] <= groups["ci_high"])).all()
