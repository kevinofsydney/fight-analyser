"""Wikipedia (MediaWiki API) -> scheduled UFC cards. The fallback when the ESPN feed fails.

Two requests per run: the wikitext of "List of UFC events" (scheduled table), then the wikitext of
every upcoming event page in one batched `action=query`. Content is CC BY-SA.

Parsing is strict about the structure we rely on (the scheduled-events table and the
`{{MMAevent bout}}` template): if neither can be found, `WikipediaFormatError` is raised instead
of returning an empty card.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from fightdb.sources import http
from fightdb.sources.models import UpcomingBout, UpcomingEvent, UpcomingFighter

API_URL = "https://en.wikipedia.org/w/api.php"
LIST_PAGE = "List of UFC events"
MAX_PAGES_PER_REQUEST = 50

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}


class WikipediaFormatError(ValueError):
    pass


@dataclass(frozen=True)
class WikiBout:
    weight_class: str
    fighter_a: str
    fighter_b: str
    card_order: int


@dataclass(frozen=True)
class WikiEvent:
    page: str
    name: str
    date: date
    location: str | None
    bouts: tuple[WikiBout, ...] = ()


# --- fetching -------------------------------------------------------------------------------


def _wikitext_of(client: httpx.Client, titles: list[str]) -> dict[str, str]:
    """Page title -> wikitext, following redirects (keys are the requested titles)."""
    out: dict[str, str] = {}
    for i in range(0, len(titles), MAX_PAGES_PER_REQUEST):
        chunk = titles[i : i + MAX_PAGES_PER_REQUEST]
        data = http.get_json(
            client,
            API_URL,
            params={
                "action": "query",
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
                "redirects": "1",
                "format": "json",
                "formatversion": "2",
                "titles": "|".join(chunk),
            },
        )
        out.update(parse_query_response(data, chunk))
    return out


def parse_query_response(data: Any, requested: list[str]) -> dict[str, str]:
    try:
        query = data["query"]
        pages = {p["title"]: p for p in query["pages"]}
    except (KeyError, TypeError) as exc:
        raise WikipediaFormatError(f"unexpected query response: {exc}") from exc
    alias = {t: t for t in requested}
    for r in query.get("normalized", []) + query.get("redirects", []):
        for k, v in list(alias.items()):
            if v == r["from"]:
                alias[k] = r["to"]
    out = {}
    for title, resolved in alias.items():
        page = pages.get(resolved)
        if not page or page.get("missing"):
            continue
        try:
            out[title] = page["revisions"][0]["slots"]["main"]["content"]
        except (KeyError, IndexError) as exc:
            raise WikipediaFormatError(f"no content for {resolved}: {exc}") from exc
    return out


def fetch_upcoming(client: httpx.Client, today: date, days: int = 120) -> list[WikiEvent]:
    listing = _wikitext_of(client, [LIST_PAGE]).get(LIST_PAGE)
    if listing is None:
        raise WikipediaFormatError(f"page not found: {LIST_PAGE}")
    events = [
        e
        for e in parse_scheduled_events(listing)
        if today <= e.date <= today + timedelta(days=days)
    ]
    pages = _wikitext_of(client, [e.page for e in events])
    return [
        WikiEvent(e.page, e.name, e.date, e.location, parse_fight_card(pages[e.page]))
        if e.page in pages
        else e
        for e in events
    ]


# --- parsing --------------------------------------------------------------------------------


_LINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_TEMPLATE_DTS = re.compile(r"\{\{\s*dts\s*\|\s*(\d{4})\s*\|\s*([A-Za-z]+)\s*\|\s*(\d{1,2})", re.I)
_REF = re.compile(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", re.S)


def _plain(text: str) -> str:
    """Wikitext cell -> display text: links resolved, refs and markup dropped."""
    text = _REF.sub("", text)
    text = _LINK.sub(lambda m: m.group(2) or m.group(1), text)
    text = re.sub(r"'{2,}", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _link_target(text: str) -> str | None:
    m = _LINK.search(text)
    return m.group(1).strip() if m else None


def _parse_dts(text: str) -> date | None:
    m = _TEMPLATE_DTS.search(text)
    if m:
        month = _MONTHS.get(m.group(2)[:3].lower())
        if month:
            return date(int(m.group(1)), month, int(m.group(3)))
    try:
        return datetime.strptime(_plain(text), "%B %d, %Y").date()
    except ValueError:
        return None


def parse_scheduled_events(wikitext: str) -> list[WikiEvent]:
    start = wikitext.find('id="Scheduled events"')
    if start < 0:
        raise WikipediaFormatError("no 'Scheduled events' table on the list page")
    end = wikitext.find("|}", start)
    table = wikitext[start:end]
    events = []
    for row in table.split("|-")[1:]:
        cells = [c.strip() for c in re.split(r"\n\|", "\n" + row.strip()) if c.strip()]
        if len(cells) < 4:
            continue
        page = _link_target(cells[0])
        day = _parse_dts(cells[1])
        if not page or day is None:
            raise WikipediaFormatError(f"cannot read scheduled event row: {cells[:2]}")
        location = ", ".join(x for x in (_plain(cells[2]), _plain(cells[3])) if x and x != "TBA")
        events.append(WikiEvent(page, _plain(cells[0]), day, location or None))
    if not events:
        raise WikipediaFormatError("scheduled events table is empty")
    return events


_BOUT = re.compile(r"\{\{\s*MMAevent bout\s*\|(.*?)\}\}", re.S | re.I)
_TITLE_MARK = re.compile(r"\((?:c|ic|uc)\)", re.I)


def _split_params(body: str) -> list[str]:
    """Split template parameters on top-level pipes (not those inside [[links]] or {{templates}})."""
    parts, depth, cur = [], 0, []
    i = 0
    while i < len(body):
        two = body[i : i + 2]
        if two in ("[[", "{{"):
            depth += 1
            cur.append(two)
            i += 2
            continue
        if two in ("]]", "}}"):
            depth -= 1
            cur.append(two)
            i += 2
            continue
        if body[i] == "|" and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(body[i])
        i += 1
    parts.append("".join(cur))
    return [p.strip() for p in parts]


def _fighter(cell: str) -> str:
    return _TITLE_MARK.sub("", _plain(cell)).strip()


def parse_fight_card(wikitext: str) -> tuple[WikiBout, ...]:
    """Bouts from `{{MMAevent bout|weight|A|vs./def.|B|...}}`, main event first."""
    bouts: list[WikiBout] = []
    for m in _BOUT.finditer(_REF.sub("", wikitext)):
        p = _split_params(m.group(1))
        if len(p) < 4:
            raise WikipediaFormatError(f"MMAevent bout with {len(p)} parameters: {p}")
        a, b = _fighter(p[1]), _fighter(p[3])
        if not a or not b:
            continue
        bouts.append(WikiBout(_plain(p[0]), a, b, card_order=len(bouts)))
    return tuple(bouts)


# --- conversion -----------------------------------------------------------------------------


def to_upcoming(events: list[WikiEvent]) -> list[UpcomingEvent]:
    """Wikipedia has no athlete or bout IDs: the page title identifies an event and the fighter
    pair identifies a bout. Champions' bouts are flagged as title fights (5 rounds)."""
    out = []
    for ev in events:
        bouts = []
        for b in ev.bouts:
            pair = sorted((_norm(b.fighter_a), _norm(b.fighter_b)))
            bouts.append(
                UpcomingBout(
                    source_bout_id="-".join(pair),
                    weight_class=b.weight_class,
                    card_order=len(ev.bouts) - 1 - b.card_order,
                    fighter_a=UpcomingFighter(source_fighter_id=None, name=b.fighter_a),
                    fighter_b=UpcomingFighter(source_fighter_id=None, name=b.fighter_b),
                )
            )
        out.append(
            UpcomingEvent(
                source="wikipedia",
                source_event_id=ev.page,
                name=ev.name,
                date=ev.date,
                location=ev.location,
                bouts=bouts,
            )
        )
    return out


# --- card check -----------------------------------------------------------------------------


def _norm(name: str) -> str:
    from fightdb.sources.identity import name_key

    return name_key(name)


def compare_cards(
    stored: dict[date, set[frozenset[str]]], wiki: list[WikiEvent], tolerance_days: int = 1
) -> list[str]:
    """Differences between our stored upcoming bouts (fighter-name pairs by event date) and
    Wikipedia. Only events Wikipedia has a card for are compared; TBA opponents are ignored."""
    from fightdb.sources.identity import is_placeholder, same_matchup

    notes = []
    for ev in wiki:
        if not ev.bouts:
            continue
        ours: list[tuple[str, str]] = []
        for d, pairs in stored.items():
            if abs((d - ev.date).days) <= tolerance_days:
                for p in pairs:
                    names = sorted(p) if len(p) == 2 else [*p, *p]
                    if not any(is_placeholder(n) for n in names):
                        ours.append((names[0], names[1]))
        theirs = [(b.fighter_a, b.fighter_b) for b in ev.bouts]
        if not ours:
            notes.append(f"{ev.name} ({ev.date}): {len(theirs)} bouts on Wikipedia, none stored")
            continue
        for a, b in theirs:
            if not any(same_matchup(a, b, x, y) for x, y in ours):
                notes.append(f"{ev.name}: Wikipedia lists {a} vs. {b}, we don't")
        for x, y in ours:
            if not any(same_matchup(x, y, a, b) for a, b in theirs):
                notes.append(f"{ev.name}: we list {x} vs. {y}, Wikipedia doesn't")
    return notes
