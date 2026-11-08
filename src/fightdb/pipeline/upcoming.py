"""Write upcoming cards into the database, recording what changed since last time.

ESPN is the primary source and drives change detection. Wikipedia is a fallback for days when
ESPN fails: it only *adds* bouts that aren't on the stored card (matched by date and fighter
names) and never cancels or rewrites anything, because a missing listing on a secondary source is
not evidence of a cancellation. When ESPN comes back, fallback events and bouts are folded into
the ESPN ones without being reported as changes.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pandas as pd

from fightdb.pipeline import upsert
from fightdb.pipeline.card_diff import CardBout, CardChange, CardEvent, DiffResult, diff_cards
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
    if any(ev.source != "espn" for ev in upcoming):
        return _ingest_fallback(con, upcoming, today, matcher, run_id)
    _adopt_fallback_events(con, upcoming)

    events: list[Event] = []
    bouts: list[Bout] = []
    new_fighters: list[Fighter] = []
    aliases: list[FighterAlias] = []
    unmatched: list[str] = []
    card_bouts: list[CardBout] = []

    for ev in upcoming:
        event_id = f"espn:{ev.source_event_id}"
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
                    fid = f"tba:{ub.source_bout_id}:{side}"
                    new_fighters.append(Fighter(fighter_id=fid, name="TBA", source="espn"))
                    ids.append(fid)
                    continue
                m = matcher.match(uf.source_fighter_id, uf.name, uf.record, division)
                if m.fighter_id is None:
                    fid = f"espn:{uf.source_fighter_id}"
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
                                source_id=str(uf.source_fighter_id),
                                fighter_id=fid,
                                name=uf.name,
                                method=m.method,
                            )
                        )
                ids.append(fid)
            bout_id = f"espn:{ub.source_bout_id}"
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


def _slug(text: str) -> str:
    return identity.normalise_name(text).replace(" ", "-") or "x"


def _ingest_fallback(
    con: duckdb.DuckDBPyConnection,
    upcoming: list[UpcomingEvent],
    today: date,
    matcher: identity.FighterMatcher,
    run_id: str | None,
) -> UpcomingResult:
    stored_events, stored_bouts = _stored_card(con)
    stored_pairs: dict[str, list[tuple[str, str]]] = {}
    known_names: dict[str, str] = {}  # normalised name -> fighter_id already on the card
    for sb in stored_bouts:
        stored_pairs.setdefault(sb.event_id, []).append((sb.a_name, sb.b_name))
        known_names.setdefault(identity.name_key(sb.a_name), sb.a_id)
        known_names.setdefault(identity.name_key(sb.b_name), sb.b_id)

    events: list[Event] = []
    bouts: list[Bout] = []
    new_fighters: list[Fighter] = []
    unmatched: list[str] = []
    changes: list[CardChange] = []
    n_events = n_bouts = 0
    for ev in upcoming:
        if ev.date < today:
            continue
        n_events += 1
        same_day = [
            e for e in stored_events if e.date is not None and abs((e.date - ev.date).days) <= 1
        ]
        if same_day:
            event_id = same_day[0].event_id
        else:
            event_id = f"{ev.source}:{_slug(ev.source_event_id)}"
            events.append(
                Event(
                    event_id=event_id,
                    name=ev.name,
                    date=ev.date,
                    location=ev.location,
                    status="upcoming",
                    source=ev.source,
                )
            )
            changes.append(
                CardChange(
                    "event_added",
                    f"New event (via {ev.source}): {ev.name} ({ev.date})",
                    event_id,
                    new_value={"date": str(ev.date)},
                )
            )
        have = stored_pairs.setdefault(event_id, [])
        for ub in ev.bouts:
            if ub.status == "cancelled":
                continue
            n_bouts += 1
            a_name, b_name = ub.fighter_a.name, ub.fighter_b.name
            if any(identity.same_matchup(a_name, b_name, x, y) for x, y in have):
                continue
            division, sex = units.division_from_weight_class(ub.weight_class)
            ids = []
            for uf in (ub.fighter_a, ub.fighter_b):
                m = matcher.match(None, uf.name, uf.record, division)
                fid = m.fighter_id or known_names.get(identity.name_key(uf.name))
                if fid is None:
                    fid = f"{ev.source}:{_slug(uf.name)}"
                    unmatched.append(f"{uf.name} ({ev.name})")
                    new_fighters.append(
                        Fighter(fighter_id=fid, name=uf.name, sex=sex, source=ev.source)  # type: ignore[arg-type]
                    )
                ids.append(fid)
            bout_id = f"{ev.source}:{_slug(ev.source_event_id)}:{_slug(ub.source_bout_id)}"
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
                    source=ev.source,
                )
            )
            have.append((a_name, b_name))
            changes.append(
                CardChange(
                    "bout_added",
                    f"Added (via {ev.source}): {a_name} vs. {b_name} ({ev.name})",
                    event_id,
                    bout_id,
                )
            )

    upsert.upsert_records(
        con, "fighters", [f for f in new_fighters if not _exists(con, f.fighter_id)]
    )
    upsert.upsert_records(con, "events", events)
    upsert.upsert_records(con, "bouts", bouts)
    first_run = not stored_events
    _apply(con, DiffResult(changes=changes), today, run_id, record=not first_run)
    return UpcomingResult(
        n_events, n_bouts, unmatched, [c.summary for c in changes] if not first_run else []
    )


def _adopt_fallback_events(con: duckdb.DuckDBPyConnection, upcoming: list[UpcomingEvent]) -> None:
    """Fold upcoming events only a fallback source knew about into the ESPN event on the same
    date, so ESPN's return doesn't read as one event cancelled and another added."""
    fallback = con.execute(
        """SELECT event_id, date FROM events
           WHERE status = 'upcoming' AND NOT starts_with(event_id, 'espn:')"""
    ).fetchall()
    for eid, day in fallback:
        ev = next((e for e in upcoming if day is not None and abs((e.date - day).days) <= 1), None)
        if ev is None:
            continue
        target = f"espn:{ev.source_event_id}"
        if not con.execute("SELECT 1 FROM events WHERE event_id = ?", [target]).fetchone():
            upsert.upsert_records(
                con,
                "events",
                [
                    Event(
                        event_id=target,
                        name=ev.name,
                        date=ev.date,
                        location=ev.location,
                        status="upcoming",
                        source="espn",
                    )
                ],
            )
        con.execute("UPDATE bouts SET event_id = ? WHERE event_id = ?", [target, eid])
        con.execute("UPDATE card_changes SET event_id = ? WHERE event_id = ?", [target, eid])
        con.execute("DELETE FROM events WHERE event_id = ?", [eid])


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
