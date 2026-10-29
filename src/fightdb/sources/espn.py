"""ESPN public scoreboard JSON -> upcoming UFC events.

Undocumented endpoint, so parsing is strict: any shape we do not recognise raises
`EspnFormatError` instead of silently producing an empty card.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import httpx

from fightdb.config import ESPN_SCOREBOARD_URL
from fightdb.sources import http
from fightdb.sources.models import UpcomingBout, UpcomingEvent, UpcomingFighter

_STATUS = {
    "STATUS_SCHEDULED": "scheduled",
    "STATUS_IN_PROGRESS": "scheduled",
    "STATUS_FINAL": "completed",
    "STATUS_CANCELED": "cancelled",
    "STATUS_CANCELLED": "cancelled",
    "STATUS_POSTPONED": "cancelled",
}


class EspnFormatError(ValueError):
    pass


def fetch_scoreboard(client: httpx.Client, start: date, days: int = 120) -> Any:
    end = start + timedelta(days=days)
    return http.get_json(
        client, ESPN_SCOREBOARD_URL, params={"dates": f"{start:%Y%m%d}-{end:%Y%m%d}"}
    )


def parse_scoreboard(payload: Any, today: date | None = None) -> list[UpcomingEvent]:
    if not isinstance(payload, dict) or "events" not in payload:
        raise EspnFormatError("scoreboard payload has no 'events' key")
    out = []
    for ev in payload["events"]:
        parsed = _parse_event(ev)
        if today is None or parsed.date >= today:
            out.append(parsed)
    return out


def _parse_event(ev: dict[str, Any]) -> UpcomingEvent:
    try:
        start = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
        comps = ev["competitions"]
    except (KeyError, ValueError, AttributeError) as exc:
        raise EspnFormatError(f"bad event: {exc}") from exc
    venue = (ev.get("venues") or [{}])[0]
    addr = venue.get("address") or {}
    location = (
        ", ".join(
            x
            for x in (
                venue.get("fullName"),
                addr.get("city"),
                addr.get("state"),
                addr.get("country"),
            )
            if x
        )
        or None
    )

    bouts = []
    n = len(comps)
    for idx, comp in enumerate(comps):
        bouts.append(_parse_bout(comp, card_order=n - 1 - idx))
    return UpcomingEvent(
        espn_event_id=str(ev["id"]),
        name=ev["name"],
        date=start.date(),
        location=location,
        bouts=bouts,
    )


def _parse_bout(comp: dict[str, Any], card_order: int) -> UpcomingBout:
    try:
        competitors = sorted(comp["competitors"], key=lambda c: c.get("order", 0))
        if len(competitors) != 2:
            raise EspnFormatError(f"bout {comp.get('id')} has {len(competitors)} competitors")
        fighters = [_parse_fighter(c) for c in competitors]
        status_name = comp["status"]["type"]["name"]
    except KeyError as exc:
        raise EspnFormatError(f"bad competition {comp.get('id')}: missing {exc}") from exc
    periods = ((comp.get("format") or {}).get("regulation") or {}).get("periods")
    notes = " ".join(str(n.get("headline", "")) for n in comp.get("notes") or [])
    return UpcomingBout(
        espn_bout_id=str(comp["id"]),
        weight_class=(comp.get("type") or {}).get("abbreviation"),
        card_order=card_order,
        fighter_a=fighters[0],
        fighter_b=fighters[1],
        scheduled_rounds=int(periods) if periods else None,
        is_title="title" in notes.lower(),
        status=_STATUS.get(status_name, "scheduled"),  # type: ignore[arg-type]
    )


def _parse_fighter(c: dict[str, Any]) -> UpcomingFighter:
    athlete = c["athlete"]
    record = next(
        (r.get("summary") for r in c.get("records") or [] if r.get("type") == "total"), None
    )
    return UpcomingFighter(espn_id=str(c["id"]), name=athlete["displayName"], record=record)
