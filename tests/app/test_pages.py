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
