"""Write upcoming cards (from ESPN) into the database, recording what changed since last time."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pandas as pd

from fightdb.pipeline import upsert
from fightdb.pipeline.card_diff import CardBout, CardEvent, DiffResult, diff_cards
from fightdb.sources import identity, units
from fightdb.sources.models import Bout, Event, Fighter, FighterAlias, UpcomingEvent


@dataclass
class UpcomingResult:
    events: int
    bouts: int
    unmatched: list[str]
    changes: list[str]


def ingest_upcoming(
    con: duckdb.DuckDBPyConnection,
    upcoming: list[UpcomingEvent],
    today: date,
    overrides_path: Path | None = None,
    run_id: str | None = None,
) -> UpcomingResult:
    overrides = identity.load_overrides(overrides_path) if overrides_path else {}
    matcher = identity.FighterMatcher(con, overrides)

    events: list[Event] = []
    bouts: list[Bout] = []
    new_fighters: list[Fighter] = []
    aliases: list[FighterAlias] = []
    unmatched: list[str] = []
    card_bouts: list[CardBout] = []

    for ev in upcoming:
        event_id = f"espn:{ev.espn_event_id}"
        events.append(
            Event(
                event_id=event_id,
                name=ev.name,
                date=ev.date,
                location=ev.location,
                status="upcoming",
                source="espn",
            )
        )
        for ub in ev.bouts:
            if ub.status == "cancelled":
                continue
            division, sex = units.division_from_weight_class(ub.weight_class)
            ids = []
            for side, uf in (("a", ub.fighter_a), ("b", ub.fighter_b)):
                if identity.is_placeholder(uf.name):
                    fid = f"tba:{ub.espn_bout_id}:{side}"
                    new_fighters.append(Fighter(fighter_id=fid, name="TBA", source="espn"))
                    ids.append(fid)
                    continue
                m = matcher.match(uf.espn_id, uf.name, uf.record, division)
                if m.fighter_id is None:
                    fid = f"espn:{uf.espn_id}"
                    unmatched.append(f"{uf.name} ({ev.name})")
                    new_fighters.append(
                        Fighter(fighter_id=fid, name=uf.name, sex=sex, source="espn")  # type: ignore[arg-type]
                    )
                else:
                    fid = m.fighter_id
                    if m.method not in ("alias",):
                        aliases.append(
                            FighterAlias(
                                source="espn",
                                source_id=uf.espn_id,
                                fighter_id=fid,
                                name=uf.name,
                                method=m.method,
                            )
                        )
                ids.append(fid)
            bout_id = f"espn:{ub.espn_bout_id}"
            bouts.append(
                Bout(
                    bout_id=bout_id,
                    event_id=event_id,
                    weight_class=ub.weight_class,
                    division=division,
                    sex=sex,  # type: ignore[arg-type]
                    is_title=ub.is_title,
                    scheduled_rounds=ub.scheduled_rounds,
                    card_order=ub.card_order,
                    fighter_a_id=ids[0],
                    fighter_b_id=ids[1],
                    status="scheduled",
                    source="espn",
                )
            )
            card_bouts.append(
                CardBout(
                    bout_id,
                    event_id,
                    ids[0],
                    ids[1],
                    ub.fighter_a.name,
                    ub.fighter_b.name,
                    ub.weight_class,
                    ub.card_order,
                )
            )

    stored_events, stored_bouts = _stored_card(con)
    first_run = not stored_events
    diff = diff_cards(
        stored_events,
        stored_bouts,
        [CardEvent(e.event_id, e.name, e.date) for e in events],
        card_bouts,
        today,
    )

    # Fighters we could not match are only inserted if no real record exists for them.
    upsert.upsert_records(
        con, "fighters", [f for f in new_fighters if not _exists(con, f.fighter_id)]
    )
    upsert.upsert_records(con, "fighter_aliases", aliases)
    upsert.upsert_records(con, "events", events)
    upsert.upsert_records(con, "bouts", bouts)
    _apply(con, diff, today, run_id, record=not first_run)
    return UpcomingResult(
        len(events),
        len(bouts),
        unmatched,
        [c.summary for c in diff.changes] if not first_run else [],
    )


def _exists(con: duckdb.DuckDBPyConnection, fighter_id: str) -> bool:
    return bool(con.execute("SELECT 1 FROM fighters WHERE fighter_id = ?", [fighter_id]).fetchone())


def _stored_card(con: duckdb.DuckDBPyConnection) -> tuple[list[CardEvent], list[CardBout]]:
    evs = con.execute(
        "SELECT event_id, name, date FROM events WHERE status = 'upcoming'"
    ).fetchall()
    rows = con.execute(
        """SELECT b.bout_id, b.event_id, b.fighter_a_id, b.fighter_b_id, fa.name, fb.name, b.weight_class, b.card_order
           FROM bouts b JOIN events e USING (event_id)
           LEFT JOIN fighters fa ON fa.fighter_id = b.fighter_a_id
           LEFT JOIN fighters fb ON fb.fighter_id = b.fighter_b_id
           WHERE e.status = 'upcoming' AND b.status = 'scheduled'"""
    ).fetchall()
    return (
        [CardEvent(e[0], e[1], e[2]) for e in evs],
        [CardBout(r[0], r[1], r[2], r[3], r[4] or r[2], r[5] or r[3], r[6], r[7]) for r in rows],
    )


def _apply(
    con: duckdb.DuckDBPyConnection, diff: DiffResult, today: date, run_id: str | None, record: bool
) -> None:
    for bid in diff.cancelled_bout_ids:
        con.execute(
            "UPDATE bouts SET status = 'cancelled', updated_at = now()::TIMESTAMP WHERE bout_id = ?",
            [bid],
        )
    for new_id, old_id in diff.replaces.items():
        con.execute("UPDATE bouts SET replaces_bout_id = ? WHERE bout_id = ?", [old_id, new_id])
    for eid in diff.cancelled_event_ids:
        con.execute("UPDATE events SET status = 'cancelled' WHERE event_id = ?", [eid])
        con.execute(
            "UPDATE bouts SET status = 'cancelled' WHERE event_id = ? AND status = 'scheduled'",
            [eid],
        )
    # Upcoming events whose date has passed: results now come from the history source.
    con.execute("UPDATE events SET status = 'past' WHERE status = 'upcoming' AND date < ?", [today])
    con.execute(
        """UPDATE bouts SET status = 'past'
           WHERE status = 'scheduled' AND event_id IN (SELECT event_id FROM events WHERE status = 'past')"""
    )
    if not record or not diff.changes:
        return
    now = datetime.now(UTC).replace(tzinfo=None)
    df = pd.DataFrame(
        [
            {
                "change_id": uuid.uuid4().hex,
                "detected_at": now,
                "run_id": run_id,
                "event_id": c.event_id,
                "bout_id": c.bout_id,
                "change_type": c.change_type,
                "summary": c.summary,
                "old_value": json.dumps(c.old_value) if c.old_value else None,
                "new_value": json.dumps(c.new_value) if c.new_value else None,
            }
            for c in diff.changes
        ]
    )
    con.register("_cc", df)
    try:
        con.execute("INSERT INTO card_changes SELECT * FROM _cc")
    finally:
        con.unregister("_cc")
