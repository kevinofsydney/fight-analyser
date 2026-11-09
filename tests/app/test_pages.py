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
    stances = con.execute(
        "SELECT fighter_name, self_stance FROM bout_sides WHERE bout_id = ?", [bout_id]
    ).fetchall()
    con.close()
    at = AppTest.from_file(str(APP / "pages" / "1_Bout.py"), default_timeout=120)
    at.session_state["bout_id"] = bout_id
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.title[0].value == "Bout"
    assert any(b.label == "Reset" for b in at.button)
    assert any(e.label == "What goes into the model outlook?" for e in at.expander)
    index_items = [m for m in at.markdown if m.value == "Striking offence index"]
    assert index_items and "opponent" in index_items[0].proto.help.lower()
    raw_items = [
        m for m in at.markdown if m.value == "Striking offence index (before schedule adjustment)"
    ]
    assert raw_items and "before the opponent-strength adjustment" in raw_items[0].proto.help
    assert any(m.value == "Durability index" and "knockdowns" in m.proto.help for m in at.markdown)
    profile = next(
        df.value
        for df in at.dataframe
        if "Stat" in df.value.columns and "Current pro record" in df.value["Stat"].values
    )
    assert {"Age at bout", "Height", "Reach", "Stance"}.issubset(set(profile["Stat"]))
    assert len(profile.columns) == 3
    stance_row = profile.loc[profile["Stat"] == "Stance"].iloc[0]
    for fighter, stance in stances:
        assert stance_row[fighter] == (stance.strip() if stance and stance.strip() else "—")
    elo_metrics = [m for m in at.metric if "Elo before fight" in m.label]
    assert elo_metrics and all("K × (result − expected)" in m.help for m in elo_metrics)
    assert any(e.label == "How Elo was calculated" for e in at.expander)
    assert any(
        "Opponent Elo" in df.value.columns and "Change" in df.value.columns for df in at.dataframe
    ) or any("No earlier UFC fights" in c.value for c in at.caption)
    assert any(h.value == "Fight statistics by round" for h in at.subheader)


def test_fighter_wear_profile_renders(db):
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    fighter_id = con.execute("SELECT fighter_id FROM bout_sides LIMIT 1").fetchone()[0]
    con.close()
    at = AppTest.from_file(str(APP / "pages" / "6_Fighter.py"), default_timeout=120)
    at.session_state["fighter_selector"] = fighter_id
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(h.value == "Fight wear (UFC history)" for h in at.subheader)
    assert any(m.label == "Strikes absorbed" for m in at.metric)
    fights = next(df.value for df in at.dataframe if "Elo before" in df.value.columns)
    assert str(fights["Elo before"].dtype) == "Int64"
    charts = at.get("vega_lite_chart")
    assert len(charts) == 1
    spec = charts[0].proto.spec
    for title in ("Fight", "Method", "Round", "Elo before fight", "Elo change", "Elo after fight"):
        assert f'"{title}"' in spec


def test_hypothesis_date_allows_recent_start(db):
    from datetime import date

    at = AppTest.from_file(str(APP / "pages" / "4_Hypothesis_Lab.py"), default_timeout=120)
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
    at = AppTest.from_file(str(APP / "pages" / "5_Saved_Hypotheses.py"), default_timeout=120)
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
    for day, price_a, price_b in (("2026-11-01", 1.9, 1.95), ("2026-11-02", 1.75, 2.15)):
        con.execute(
            """INSERT INTO upcoming_odds_history VALUES
               (?, ?, 'book', 'Example Book', ?, NULL, ?::TIMESTAMP),
               (?, ?, 'book', 'Example Book', ?, NULL, ?::TIMESTAMP)""",
            [bout_id, fighter_a, price_a, day, bout_id, fighter_b, price_b, day],
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
        assert len(tables[0]) == 1
        assert {"1.75", "2.15"}.issubset(set(tables[0].iloc[0].astype(str)))
        assert any(m.value == "**Betting line movement**" for m in at.markdown)
        assert len(at.get("vega_lite_chart")) == 1
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

    page = APP / "pages" / "5_Saved_Hypotheses.py"
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


def test_profile_comparison_highlights_only_known_advantages():
    import ast
    import math

    import pandas as pd

    page = APP / "pages" / "1_Bout.py"
    module = ast.parse(page.read_text(encoding="utf-8"))
    names = {"num", "highlight_profile_advantage"}
    functions = [
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {
        "pd": pd,
        "math": math,
        "a": pd.Series({"self_age_years": 36, "self_height_cm": 193, "self_reach_cm": None}),
        "b": pd.Series({"self_age_years": 29, "self_height_cm": 185, "self_reach_cm": 185}),
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(page), "exec"), namespace)
    highlight = namespace["highlight_profile_advantage"]
    assert highlight(pd.Series({"Stat": "Age at bout", "A": "36", "B": "29"}))[2]
    assert highlight(pd.Series({"Stat": "Height", "A": "193", "B": "185"}))[1]
    assert highlight(pd.Series({"Stat": "Reach", "A": "—", "B": "185"})) == ["", "", ""]
    namespace["a"]["self_height_cm"] = 185
    assert highlight(pd.Series({"Stat": "Height", "A": "185", "B": "185"})) == ["", "", ""]


def test_build_saves_model_weights_and_current_ratings(db):
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        years = {
            r[0] for r in con.execute("SELECT DISTINCT model_year FROM model_weights").fetchall()
        }
        evaluated = {
            r[0]
            for r in con.execute(
                "SELECT DISTINCT test_year FROM model_evals WHERE model_name = 'win_logistic'"
            ).fetchall()
        }
        assert None in years and evaluated <= years
        fighters = con.execute(
            "SELECT count(DISTINCT fighter_id) FROM bout_sides WHERE status = 'completed'"
        ).fetchone()[0]
        assert con.execute("SELECT count(*) FROM fighter_current").fetchone()[0] == fighters
    finally:
        con.close()


def test_bout_page_breaks_down_the_win_probability(db):
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    bout_id, p_win = con.execute(
        """SELECT bout_id, self_p_win FROM bout_sides
           WHERE status = 'completed' AND is_a AND self_p_win IS NOT NULL LIMIT 1"""
    ).fetchone()
    con.close()
    at = AppTest.from_file(str(APP / "pages" / "1_Bout.py"), default_timeout=120)
    at.session_state["bout_id"] = bout_id
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(e.label == "How the model calculated these chances" for e in at.expander)
    total = next(m.value for m in at.markdown if m.value.startswith("**Total push"))
    assert f"= {p_win:.0%}**" in total
    factors = next(df.value for df in at.dataframe if "Push" in df.value.columns)
    assert len(factors) == 16 and {"Weight", "Typical gap", "Gap"} <= set(factors.columns)


def test_bout_search_filters_and_opens_the_selected_bout(db):
    import json

    import duckdb
    from streamlit.elements.arrow import DataframeSelectionSerde

    con = duckdb.connect(str(db), read_only=True)
    name, opponent = con.execute(
        "SELECT fighter_name, opp_name FROM bout_sides WHERE status = 'completed' LIMIT 1"
    ).fetchone()
    expected = con.execute(
        """SELECT count(DISTINCT bout_id) FROM bout_sides
           WHERE status = 'completed' AND fighter_name ILIKE ?""",
        [f"%{name}%"],
    ).fetchone()[0]
    con.close()
    # Start from the entrypoint so switching to the Bout page resolves, as in the real app.
    at = AppTest.from_file(str(APP / "Home.py"), default_timeout=120)
    at.switch_page("pages/2_Bout_Search.py")
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    at.text_input[0].set_value(name).run()
    results = at.dataframe[0].value
    assert len(results) > 0
    assert results["Fighter"].str.contains(name, regex=False).all()
    assert len(results) == min(expected, 500)  # one row per bout, from the searched side
    at.text_input[1].set_value(opponent).run()
    narrowed = at.dataframe[0].value
    assert 0 < len(narrowed) <= len(results)
    assert narrowed["Opponent"].str.contains(opponent, regex=False).all()
    at.text_input[1].set_value("").run()
    at.text_input[0].set_value("no fighter is called this").run()
    assert any("No bouts match" in i.value for i in at.info)
    at.text_input[0].set_value(name).run()

    at.session_state["search_results"] = DataframeSelectionSerde().deserialize(
        json.dumps({"selection": {"rows": [0]}})
    )
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["bout_id"]


def test_matchup_projects_any_two_fighters_and_explains_it(db):
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    a_id, b_id = [
        r[0]
        for r in con.execute(
            "SELECT fighter_id FROM fighter_current ORDER BY ufc_fights DESC LIMIT 2"
        ).fetchall()
    ]
    con.close()
    at = AppTest.from_file(str(APP / "pages" / "3_Matchup.py"), default_timeout=120)
    at.session_state["mu_a"] = a_id
    at.session_state["mu_b"] = b_id
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    wins = [float(m.value.removesuffix("% to win")) for m in at.metric]
    assert len(wins) == 2 and abs(sum(wins) - 100) <= 1
    assert any(m.value.startswith("**Total push") for m in at.markdown)
    assert any(h.value.startswith("Why the model favours") for h in at.subheader)
