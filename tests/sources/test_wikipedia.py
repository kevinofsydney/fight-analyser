from datetime import date

import httpx
import pytest

from fightdb.sources import http, wikipedia
from tests.conftest import FIXTURES

W = FIXTURES / "wikipedia"


def _text(name: str) -> str:
    return (W / name).read_text(encoding="utf-8")


def test_scheduled_events_table():
    events = {e.page: e for e in wikipedia.parse_scheduled_events(_text("list_scheduled.wikitext"))}
    ufc334 = events["UFC 334"]
    assert ufc334.name == "UFC 334: Gane vs. Hokit"
    assert ufc334.date == date(2026, 11, 14)
    assert ufc334.location == "Madison Square Garden, New York City, New York, U.S."
    assert events["UFC Fight Night 297"].location is None  # venue and city still TBA


def test_fight_card_known_bouts():
    card = wikipedia.parse_fight_card(_text("UFC_334.wikitext"))
    assert len(card) == 12
    main = card[0]
    assert (main.weight_class, main.fighter_a, main.fighter_b) == (
        "Heavyweight",
        "Ciryl Gane",
        "Josh Hokit",
    )
    # Accents kept, links resolved, "(c)" champion marks stripped.
    assert any(b.fighter_a == "Uroš Medić" for b in card)
    assert all("(c)" not in b.fighter_a + b.fighter_b for b in card)
    prochazka = wikipedia.parse_fight_card(_text("UFC_Fight_Night_Prochazka_vs_Stirling.wikitext"))
    assert prochazka[0].fighter_a == "Jiří Procházka" and len(prochazka) == 9


def test_bout_template_parsing():
    text = """{{MMAevent bout
|Lightweight
|[[Charles Oliveira]] (c)
|vs.
|[[Diego Lopes (fighter)|Diego Lopes]]<ref name="x"/>
|
|
|
|For the symbolic "[[List of UFC champions#BMF Title|BMF]]" title.
}}"""
    (b,) = wikipedia.parse_fight_card(text)
    assert (b.fighter_a, b.fighter_b) == ("Charles Oliveira", "Diego Lopes")


def test_missing_table_is_an_error():
    with pytest.raises(wikipedia.WikipediaFormatError):
        wikipedia.parse_scheduled_events("== Past events ==\n{| class=wikitable\n|}")


def test_query_response_follows_redirects():
    data = {
        "query": {
            "redirects": [{"from": "UFC 335", "to": "UFC 335: Oliveira vs. Lopes"}],
            "pages": [
                {
                    "title": "UFC 335: Oliveira vs. Lopes",
                    "revisions": [{"slots": {"main": {"content": "TEXT"}}}],
                },
                {"title": "Nope", "missing": True},
            ],
        }
    }
    assert wikipedia.parse_query_response(data, ["UFC 335", "Nope"]) == {"UFC 335": "TEXT"}
    with pytest.raises(wikipedia.WikipediaFormatError):
        wikipedia.parse_query_response({"error": "x"}, ["UFC 335"])


def test_fetch_upcoming_uses_two_requests():
    listing = _text("list_scheduled.wikitext")
    page = _text("UFC_334.wikitext")
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        titles = req.url.params["titles"].split("|")
        calls.append(titles)
        content = {wikipedia.LIST_PAGE: listing, "UFC 334": page}
        pages = [
            {"title": t, "revisions": [{"slots": {"main": {"content": content.get(t, "")}}}]}
            for t in titles
        ]
        return httpx.Response(200, json={"query": {"pages": pages}})

    with http.make_client(transport=httpx.MockTransport(handler)) as c:
        events = wikipedia.fetch_upcoming(c, date(2026, 11, 9), days=10)
    assert [e.page for e in events] == ["UFC 334"]
    assert len(events[0].bouts) == 12
    assert len(calls) == 2


def test_compare_cards():
    card = wikipedia.parse_fight_card(_text("UFC_334.wikitext"))
    ev = wikipedia.WikiEvent("UFC 334", "UFC 334", date(2026, 11, 14), None, card)
    stored = {date(2026, 11, 15): {frozenset((b.fighter_a, b.fighter_b)) for b in card}}
    assert wikipedia.compare_cards(stored, [ev]) == []
    # Accent and case differences don't count; a replaced opponent does.
    stored[date(2026, 11, 15)] = {
        frozenset(("CIRYL GANE", "Josh Hokit")),
        frozenset(("Uros Medic", "Kevin Holland")),
        frozenset(("Ciryl Gane", "Someone Else")),
    }
    notes = wikipedia.compare_cards(stored, [ev])
    assert any("Someone Else" in n for n in notes)
    assert any("Kayla Harrison vs. Amanda Nunes" in n for n in notes)
    assert not any("Medi" in n for n in notes)
    assert wikipedia.compare_cards({}, [ev]) == [
        "UFC 334 (2026-11-14): 12 bouts on Wikipedia, none stored"
    ]


def test_to_upcoming():
    card = wikipedia.parse_fight_card(_text("UFC_334.wikitext"))
    (ev,) = wikipedia.to_upcoming(
        [wikipedia.WikiEvent("UFC 334", "UFC 334: Gane vs. Hokit", date(2026, 11, 14), None, card)]
    )
    assert ev.source == "wikipedia" and ev.source_event_id == "UFC 334"
    main = ev.bouts[0]
    assert main.fighter_a.name == "Ciryl Gane" and main.fighter_a.source_fighter_id is None
    assert main.card_order == len(card) - 1  # main event has the highest card order, as on ESPN
    assert len({b.source_bout_id for b in ev.bouts}) == len(ev.bouts)


def test_compare_ignores_placeholders_and_name_order():
    ev = wikipedia.WikiEvent(
        "P",
        "Ev",
        date(2026, 11, 14),
        None,
        (wikipedia.WikiBout("x", "Yoo Joo-sang", "Marwan Rahiki", 0),),
    )
    stored = {
        date(2026, 11, 14): {
            frozenset(("Joo-sang Yoo", "Marwan Rahiki")),
            frozenset(("Some One", "TBA")),
        }
    }
    assert wikipedia.compare_cards(stored, [ev]) == []
