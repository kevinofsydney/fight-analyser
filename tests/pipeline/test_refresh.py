import json
import sqlite3
from datetime import date

import duckdb
import httpx
import pytest

from fightdb.config import Settings
from fightdb.pipeline.refresh import run_refresh
from fightdb.sources import espn
from tests.conftest import FIXTURES

TODAY = date(2026, 10, 29)


def _no_network() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected network call: {request.url}")

    return httpx.Client(transport=httpx.MockTransport(handler))


def _upcoming(mutate=None):
    payload = json.loads((FIXTURES / "espn" / "scoreboard_range.json").read_text())
    if mutate:
        mutate(payload)
    return lambda client, today: espn.parse_scoreboard(payload)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        db_path=tmp_path / "fightdb.duckdb",
        download_dir=tmp_path / "dl",
        overrides_dir=tmp_path / "overrides",
        _env_file=None,
    )


def _run(settings, upcoming):
    return run_refresh(
        settings,
        kind="backfill",
        file=FIXTURES / "canon_mini.db",
        client=_no_network(),
        fetch_upcoming=upcoming,
        today=TODAY,
        train_models=False,
    )


def test_first_run_then_card_changes(settings):
    report = _run(settings, _upcoming())
    assert report["counts"]["bouts"] > 0 and report["counts"]["upcoming_bouts"] > 0
    assert report["card_changes"] == []  # first run records no changes

    def cancel_first_bout(p):
        p["events"][0]["competitions"].pop(0)

    report2 = _run(settings, _upcoming(cancel_first_bout))
    assert any(c.startswith("Cancelled") for c in report2["card_changes"])
    con = duckdb.connect(str(settings.db_path), read_only=True)
    try:
        assert (
            con.execute(
                "SELECT count(*) FROM card_changes WHERE change_type = 'bout_cancelled'"
            ).fetchone()[0]
            == 1
        )
        assert (
            con.execute(
                "SELECT count(*) FROM scrape_runs WHERE status IN ('ok', 'warning')"
            ).fetchone()[0]
            == 2
        )
        assert (
            con.execute("SELECT count(*) FROM bout_sides WHERE status = 'scheduled'").fetchone()[0]
            > 0
        )
    finally:
        con.close()


def test_failed_refresh_leaves_database_untouched(settings):
    _run(settings, _upcoming())
    con = duckdb.connect(str(settings.db_path), read_only=True)
    before = con.execute(
        "SELECT count(*), sum(hash(bout_id || coalesce(result_a, ''))) FROM bouts"
    ).fetchone()
    con.close()

    with pytest.raises(sqlite3.OperationalError):
        run_refresh(
            settings,
            file=FIXTURES / "does-not-exist.db",
            client=_no_network(),
            fetch_upcoming=_upcoming(),
            today=TODAY,
            train_models=False,
        )
    con = duckdb.connect(str(settings.db_path), read_only=True)
    try:
        status = con.execute(
            "SELECT status FROM scrape_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()[0]
        after = con.execute(
            "SELECT count(*), sum(hash(bout_id || coalesce(result_a, ''))) FROM bouts"
        ).fetchone()
    finally:
        con.close()
    assert status == "failed"
    assert after == before  # only the failure log row was added
    assert not settings.db_path.with_suffix(".tmp.duckdb").exists()


def test_espn_failure_keeps_history(settings):
    def broken(client, today):
        raise espn.EspnFormatError("shape changed")

    report = _run(settings, broken)
    assert report["counts"]["bouts"] > 0
    assert any("upcoming cards failed" in w for w in report["warnings"])
