from datetime import datetime

import httpx
import pytest
from pydantic import ValidationError

from fightdb.pipeline import odds
from fightdb.sources import http, odds_api


def sample():
    return [
        {
            "id": "event-1",
            "commence_time": "2030-06-01T21:00:00Z",
            "home_team": "Alice Silva",
            "away_team": "Betty Jones",
            "bookmakers": [
                {
                    "key": "book1",
                    "title": "Example Book",
                    "last_update": "2030-05-30T12:00:00Z",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Alice Silva", "price": 1.75},
                                {"name": "Betty Jones", "price": 2.15},
                            ],
                        }
                    ],
                }
            ],
        }
    ]


def test_parser_and_single_daily_request():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=sample())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        parsed = odds_api.parse(http.upcoming_odds(client, "dummy-test-key"))
    assert len(requests) == 1
    assert requests[0].url.params["regions"] == "au"
    assert requests[0].url.params["markets"] == "h2h"
    assert parsed[0].bookmakers[0].markets[0].outcomes[0].price == 1.75
    with pytest.raises(ValidationError):
        odds_api.parse({"unexpected": "response"})


def _scheduled(con):
    con.execute(
        "INSERT INTO fighters (fighter_id,name,source) VALUES ('a','Alice Silva','test'),('b','Betty Jones','test')"
    )
    con.execute(
        "INSERT INTO events (event_id,name,date,status,source) VALUES ('e','Event','2030-06-01','upcoming','test')"
    )
    con.execute(
        "INSERT INTO bouts (bout_id,event_id,fighter_a_id,fighter_b_id,status,source) VALUES ('b','e','a','b','scheduled','test')"
    )


def test_odds_match_names_and_replace_snapshot(con):
    _scheduled(con)
    assert odds.store(con, odds_api.parse(sample()), datetime(2030, 5, 31)) == 2
    assert con.execute(
        "SELECT fighter_id, decimal_odds FROM upcoming_odds ORDER BY fighter_id"
    ).fetchall() == [("a", 1.75), ("b", 2.15)]
    payload = sample()
    payload[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = 1.9
    assert odds.store(con, odds_api.parse(payload), datetime(2030, 5, 31)) == 2
    assert (
        con.execute("SELECT decimal_odds FROM upcoming_odds WHERE fighter_id='a'").fetchone()[0]
        == 1.9
    )
    assert odds.store(con, [], datetime(2030, 5, 31)) == 0
    assert con.execute("SELECT count(*) FROM upcoming_odds").fetchone()[0] == 0


def test_odds_do_not_match_wrong_or_ambiguous_bout(con):
    _scheduled(con)
    payload = sample()
    payload[0]["away_team"] = "Betty Smith"
    assert odds.store(con, odds_api.parse(payload), datetime(2030, 5, 31)) == 0
    con.execute(
        "INSERT INTO bouts (bout_id,event_id,fighter_a_id,fighter_b_id,status,source) VALUES ('b2','e','a','b','scheduled','test')"
    )
    assert odds.store(con, odds_api.parse(sample()), datetime(2030, 5, 31)) == 0
    assert con.execute("SELECT count(*) FROM upcoming_odds").fetchone()[0] == 0


def test_odds_http_error_does_not_expose_key(monkeypatch, caplog):
    secret = "private-example-key"
    request = httpx.Request("GET", f"https://api.the-odds-api.com/v4/test?apiKey={secret}")
    response = httpx.Response(401, request=request)

    def fail(*args, **kwargs):
        raise httpx.HTTPStatusError("Unauthorized", request=request, response=response)

    monkeypatch.setattr(http, "get_json", fail)
    with (
        httpx.Client(transport=httpx.MockTransport(lambda req: response)) as client,
        pytest.raises(RuntimeError) as exc,
    ):
        http.upcoming_odds(client, secret)
    assert secret not in str(exc.value)
    assert secret not in caplog.text


def test_invalid_price_rejected():
    payload = sample()
    payload[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = float("inf")
    with pytest.raises(ValidationError):
        odds_api.parse(payload)
