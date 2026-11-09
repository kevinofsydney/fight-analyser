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
    payload = json.loads((FIXTURES / "espn" / "scoreboard_range.json").read_text(encoding="utf-8"))
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


def _scheduled(settings):
    con = duckdb.connect(str(settings.db_path), read_only=True)
    try:
        return con.execute(
            """SELECT b.bout_id, b.event_id, fa.name, fb.name FROM bouts b
               JOIN fighters fa ON fa.fighter_id = b.fighter_a_id
               JOIN fighters fb ON fb.fighter_id = b.fighter_b_id
               WHERE b.status = 'scheduled' ORDER BY 1"""
        ).fetchall()
    finally:
        con.close()


def test_wikipedia_fallback_then_espn_returns(settings):
    from fightdb.sources import wikipedia

    espn_events = _upcoming()(None, TODAY)
    _run(settings, _upcoming())
    before = _scheduled(settings)
    first = espn_events[0]
    known = first.bouts[0]

    def broken(client, today):
        raise espn.EspnFormatError("shape changed")

    def wiki(client, today):
        return [
            wikipedia.WikiEvent(
                "Some page",
                first.name,
                first.date,
                None,
                (
                    # Already stored (names differ only in case): must not be duplicated.
                    wikipedia.WikiBout("x", known.fighter_a.name.upper(), known.fighter_b.name, 0),
                    wikipedia.WikiBout("Lightweight", "New Guy", "Other Guy", 1),
                ),
            ),
            wikipedia.WikiEvent("UFC 999", "UFC 999", date(2027, 6, 1), None, ()),
        ]

    report = run_refresh(
        settings,
        file=FIXTURES / "canon_mini.db",
        client=_no_network(),
        fetch_upcoming=broken,
        check_wikipedia=wiki,
        today=TODAY,
        train_models=False,
    )
    assert any("used Wikipedia fallback" in w for w in report["warnings"])
    assert sorted(report["card_changes"]) == sorted(
        [
            f"Added (via wikipedia): New Guy vs. Other Guy ({first.name})",
            "New event (via wikipedia): UFC 999 (2027-06-01)",
        ]
    )
    after = _scheduled(settings)
    assert set(before) < set(after)  # nothing stored was cancelled or rewritten
    added = [r for r in after if r not in before]
    assert len(added) == 1 and added[0][1] == f"espn:{first.source_event_id}"

    # ESPN is back: the Wikipedia-only bout isn't on ESPN, so the primary source cancels it,
    # but the bout both sources had is untouched and nothing is double-reported.
    back = _run(settings, _upcoming())
    assert sorted(back["card_changes"]) == [
        f"Cancelled: New Guy vs. Other Guy ({first.name})",
        "Event no longer listed: UFC 999",
    ]
    assert set(before) <= set(_scheduled(settings))


def test_espn_back_adopts_fallback_event(settings):
    """An event first seen only on Wikipedia is merged into ESPN's event for that date."""
    from fightdb.sources import wikipedia

    espn_events = _upcoming()(None, TODAY)
    last = espn_events[-1]

    def without_last(p):
        p["events"].pop()

    _run(settings, _upcoming(without_last))

    def broken(client, today):
        raise espn.EspnFormatError("down")

    bout = last.bouts[0]
    run_refresh(
        settings,
        file=FIXTURES / "canon_mini.db",
        client=_no_network(),
        fetch_upcoming=broken,
        check_wikipedia=lambda c, t: [
            wikipedia.WikiEvent(
                "P",
                last.name,
                last.date,
                None,
                (wikipedia.WikiBout("x", bout.fighter_a.name, bout.fighter_b.name, 0),),
            )
        ],
        today=TODAY,
        train_models=False,
    )
    back = _run(settings, _upcoming())
    # The event is not reported as cancelled + re-added, and the shared bout isn't either.
    assert not any("no longer listed" in c for c in back["card_changes"])
    assert not any(
        f"{bout.fighter_a.name} vs. {bout.fighter_b.name}" in c for c in back["card_changes"]
    )
    con = duckdb.connect(str(settings.db_path), read_only=True)
    try:
        assert con.execute(
            "SELECT count(*) FROM events WHERE starts_with(event_id, 'wikipedia:')"
        ).fetchone() == (0,)
    finally:
        con.close()


def test_optional_upcoming_odds_refresh_and_failure_retains_last_prices(settings, monkeypatch):
    from datetime import datetime

    import fightdb.pipeline.refresh as refresh

    settings.odds_api_key = "dummy-test-key"
    monkeypatch.setattr(refresh, "_now", lambda: datetime(2026, 10, 29, 12))
    payload = [
        {
            "id": "x",
            "commence_time": "2026-11-15T01:00:00Z",
            "home_team": "Ciryl Gane",
            "away_team": "Josh Hokit",
            "bookmakers": [
                {
                    "key": "book",
                    "title": "Example",
                    "last_update": "2026-10-29T11:00:00Z",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Ciryl Gane", "price": 1.7},
                                {"name": "Josh Hokit", "price": 2.2},
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload if len(calls) == 1 else {"invalid": "shape"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = run_refresh(
            settings,
            file=FIXTURES / "canon_mini.db",
            client=client,
            fetch_upcoming=_upcoming(),
            today=TODAY,
            train_models=False,
        )
        assert first["counts"]["upcoming_odds"] == 2
        second = run_refresh(
            settings,
            file=FIXTURES / "canon_mini.db",
            client=client,
            fetch_upcoming=_upcoming(),
            today=TODAY,
            train_models=False,
        )
        assert len(calls) == 1
        assert "upcoming_odds" not in second["counts"]
        monkeypatch.setattr(refresh, "_now", lambda: datetime(2026, 10, 30, 12))
        third = run_refresh(
            settings,
            file=FIXTURES / "canon_mini.db",
            client=client,
            fetch_upcoming=_upcoming(),
            today=TODAY,
            train_models=False,
        )
        fourth = run_refresh(
            settings,
            file=FIXTURES / "canon_mini.db",
            client=client,
            fetch_upcoming=_upcoming(),
            today=TODAY,
            train_models=False,
        )
        assert "upcoming_odds" not in fourth["counts"]
    assert len(calls) == 2
    assert "Upcoming odds refresh failed; previous quotes retained" in third["warnings"]
    con = duckdb.connect(str(settings.db_path), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM upcoming_odds").fetchone()[0] == 2
    finally:
        con.close()
