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

from fightdb import notify
from fightdb.config import Settings
from fightdb.db.conn import init_db
from fightdb.pipeline import backfill, build, upcoming
from fightdb.sources import espn, http
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
    try:
        con = duckdb.connect(str(tmp_path))
        try:
            init_db(con)
            if history:
                stored = _completed_bouts(con)
                bundle, counts, warns = backfill.import_history(con, settings, client, source, file)
                report.update(source=bundle.source, source_version=bundle.source_version)
                report["counts"].update(counts)
                report["warnings"] += warns
                # Upserts never delete, so a truncated upstream file cannot lose data, but it is a
                # sign the source is broken and worth surfacing.
                if stored and len(bundle.bouts) < 0.98 * stored:
                    report["warnings"].append(
                        f"{bundle.source} returned {len(bundle.bouts)} completed bouts; we already had {stored}"
                    )
            if cards:
                try:
                    events = (fetch_upcoming or _fetch_espn)(client, today)
                    res = upcoming.ingest_upcoming(
                        con, events, today, settings.overrides_dir / "fighter_identity.yaml", run_id
                    )
                    report["counts"].update(upcoming_events=res.events, upcoming_bouts=res.bouts)
                    report["unmatched"] = res.unmatched
                    report["card_changes"] = res.changes
                except Exception as exc:  # noqa: BLE001 - the history import is still worth keeping
                    log.exception("upcoming cards failed")
                    report["warnings"].append(f"upcoming cards failed: {exc}")
            if not _completed_bouts(con):
                raise RuntimeError("database has no completed bouts; run a backfill first")
            report["counts"].update(build.build_all(con, train_models=train_models))
            max_date = con.execute(
                "SELECT max(date) FROM events WHERE status = 'completed'"
            ).fetchone()[0]  # type: ignore[index]
            _write_run(con, report, started, "warning" if report["warnings"] else "ok", max_date)
            con.execute("CHECKPOINT")
        finally:
            con.close()
        os.replace(tmp_path, db_path)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        report["error"] = str(exc)
        _record_failure(db_path, report, started)
        raise
    finally:
        if own_client:
            client.close()

    if report.get("card_changes"):
        notify.notify_card_changes(settings.discord_webhook_url, report["card_changes"])
    return report


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
