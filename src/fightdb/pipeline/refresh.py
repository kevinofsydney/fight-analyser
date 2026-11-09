"""The daily job: history import (with failover), upcoming cards, derived tables.

Works on a temporary copy of the database and swaps it in only if everything succeeded, so a
failed run never leaves a half-written database behind.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import httpx

from fightdb import errorlog, notify
from fightdb.config import Settings
from fightdb.db.conn import init_db
from fightdb.pipeline import backfill, build, odds, quality, upcoming
from fightdb.sources import espn, http, odds_api, wikipedia
from fightdb.sources.models import UpcomingEvent

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def run_refresh(
    settings: Settings,
    kind: str = "refresh",
    history: bool = True,
    cards: bool = True,
    source: str | None = None,
    file: Path | None = None,
    client: httpx.Client | None = None,
    fetch_upcoming: Callable[[httpx.Client, date], list[UpcomingEvent]] | None = None,
    today: date | None = None,
    train_models: bool = True,
    check_wikipedia: Callable[[httpx.Client, date], list[wikipedia.WikiEvent]] | None = None,
) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_suffix(".tmp.duckdb")
    for p in (tmp_path, Path(str(tmp_path) + ".wal")):
        p.unlink(missing_ok=True)
    if db_path.exists():
        shutil.copy2(db_path, tmp_path)

    run_id = uuid.uuid4().hex[:12]
    started = _now()
    report: dict[str, Any] = {"run_id": run_id, "kind": kind, "warnings": [], "counts": {}}
    own_client = client is None
    client = client or http.make_client()
    with errorlog.context(run_id=run_id, kind=kind):
        try:
            con = duckdb.connect(str(tmp_path))
            try:
                init_db(con)
                before = quality.table_counts(con)
                if history:
                    stored = _completed_bouts(con)
                    bundle, counts, warns = backfill.import_history(
                        con, settings, client, source, file
                    )
                    report.update(source=bundle.source, source_version=bundle.source_version)
                    report["counts"].update(counts)
                    report["warnings"] += warns
                    # Upserts never delete, so a truncated upstream file cannot lose data, but it is a
                    # sign the source is broken and worth surfacing.
                    if stored and len(bundle.bouts) < 0.98 * stored:
                        report["warnings"].append(
                            f"{bundle.source} returned {len(bundle.bouts)} completed bouts; we already had {stored}"
                        )
                        log.warning(report["warnings"][-1])
                if cards:
                    try:
                        events = (fetch_upcoming or _fetch_espn)(client, today)
                        res = upcoming.ingest_upcoming(
                            con,
                            events,
                            today,
                            settings.overrides_dir / "fighter_identity.yaml",
                            run_id,
                        )
                        report["counts"].update(
                            upcoming_events=res.events, upcoming_bouts=res.bouts
                        )
                        report["unmatched"] = res.unmatched
                        report["card_changes"] = res.changes
                    except Exception as exc:  # noqa: BLE001 - the history import is still worth keeping
                        log.exception("upcoming cards failed")
                        report["warnings"].append(f"ESPN upcoming cards failed: {exc}")
                        _wikipedia_fallback(
                            con, client, today, settings, run_id, report, check_wikipedia
                        )
                odds.init_table(con)
                if cards and settings.odds_api_key:
                    try:
                        quotes = odds_api.parse(http.upcoming_odds(client, settings.odds_api_key))
                        report["counts"]["upcoming_odds"] = odds.store(con, quotes, _now())
                    except Exception as exc:  # noqa: BLE001 - odds failures must not stop refresh
                        log.warning("Upcoming odds could not be refreshed (%s)", type(exc).__name__)
                        report["warnings"].append(
                            "Upcoming odds refresh failed; previous quotes retained"
                        )
                if not _completed_bouts(con):
                    raise RuntimeError("database has no completed bouts; run a backfill first")
                report["counts"].update(build.build_all(con, train_models=train_models))
                failures = quality.check_quality(con, before, settings)
                if failures:
                    raise RuntimeError("data quality: " + "; ".join(failures))
                max_date = con.execute(
                    "SELECT max(date) FROM events WHERE status = 'completed'"
                ).fetchone()[0]  # type: ignore[index]
                _write_run(
                    con, report, started, "warning" if report["warnings"] else "ok", max_date
                )
                con.execute("CHECKPOINT")
            finally:
                con.close()
            os.replace(tmp_path, db_path)
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            report["error"] = str(exc)
            _record_failure(db_path, report, started)
            log.exception("%s %s failed", kind, run_id)
            errorlog.mark_logged(exc)
            raise
        finally:
            if own_client:
                client.close()

    if report.get("card_changes"):
        notify.notify_card_changes(settings.discord_webhook_url, report["card_changes"])
    return report


def _wikipedia_fallback(
    con: duckdb.DuckDBPyConnection,
    client: httpx.Client,
    today: date,
    settings: Settings,
    run_id: str,
    report: dict[str, Any],
    fetch: Callable[[httpx.Client, date], list[wikipedia.WikiEvent]] | None,
) -> None:
    """ESPN is down: add any new bouts Wikipedia lists, keep everything already stored, and
    list stored bouts Wikipedia doesn't have (possible cancellations) for a human to check."""
    try:
        wiki = (fetch or wikipedia.fetch_upcoming)(client, today)
        stored = _stored_pairs(con)
        res = upcoming.ingest_upcoming(
            con,
            wikipedia.to_upcoming(wiki),
            today,
            settings.overrides_dir / "fighter_identity.yaml",
            run_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Wikipedia fallback failed")
        report["warnings"].append(f"Wikipedia fallback failed: {exc}; kept the stored card")
        return
    report["warnings"].append(
        f"used Wikipedia fallback for upcoming cards ({len(res.changes)} changes)"
    )
    report["counts"].update(upcoming_events=res.events, upcoming_bouts=res.bouts)
    report["unmatched"] = res.unmatched
    report["card_changes"] = res.changes
    missing = [n for n in wikipedia.compare_cards(stored, wiki) if "Wikipedia doesn't" in n]
    limit = 15
    report["warnings"] += [f"check: {n}" for n in missing[:limit]]
    if len(missing) > limit:
        report["warnings"].append(
            f"check: {len(missing) - limit} more stored bouts not on Wikipedia"
        )


def _stored_pairs(con: duckdb.DuckDBPyConnection) -> dict[date, set[frozenset[str]]]:
    rows = con.execute(
        """SELECT e.date, fa.name, fb.name
           FROM bouts b JOIN events e USING (event_id)
           JOIN fighters fa ON fa.fighter_id = b.fighter_a_id
           JOIN fighters fb ON fb.fighter_id = b.fighter_b_id
           WHERE e.status = 'upcoming' AND b.status = 'scheduled'"""
    ).fetchall()
    stored: dict[date, set[frozenset[str]]] = {}
    for d, a, b in rows:
        stored.setdefault(d, set()).add(frozenset((a, b)))
    return stored


def _completed_bouts(con: duckdb.DuckDBPyConnection) -> int:
    row = con.execute("SELECT count(*) FROM bouts WHERE status = 'completed'").fetchone()
    return int(row[0]) if row else 0


def _fetch_espn(client: httpx.Client, today: date) -> list[UpcomingEvent]:
    return espn.parse_scoreboard(espn.fetch_scoreboard(client, today), today=today)


def _write_run(
    con: duckdb.DuckDBPyConnection,
    report: dict[str, Any],
    started: datetime,
    status: str,
    max_date: Any,
) -> None:
    con.execute(
        """INSERT INTO scrape_runs (run_id, kind, started_at, finished_at, status, source, source_version,
                                    max_event_date, counts, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            report["run_id"],
            report["kind"],
            started,
            _now(),
            status,
            report.get("source"),
            report.get("source_version"),
            max_date,
            json.dumps(
                {
                    **report["counts"],
                    "unmatched": report.get("unmatched", []),
                    "card_changes": report.get("card_changes", []),
                }
            ),
            "; ".join(report["warnings"]) or report.get("error"),
        ],
    )


def _record_failure(db_path: Path, report: dict[str, Any], started: datetime) -> None:
    try:
        con = duckdb.connect(str(db_path))
        try:
            init_db(con)
            report.setdefault("counts", {})
            report["warnings"] = report.get("warnings", []) + [f"error: {report.get('error')}"]
            _write_run(con, report, started, "failed", None)
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        log.exception("could not record failed run")
