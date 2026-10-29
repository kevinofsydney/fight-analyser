import pytest

from fightdb.sources import units


@pytest.mark.parametrize(
    ("raw", "cm"),
    [("5' 11\"", 180.3), ("6' 0\"", 182.9), ("--", None), ("", None), (None, None)],
)
def test_parse_height(raw, cm):
    assert units.parse_height(raw) == cm


def test_reach_and_inches():
    assert units.parse_reach('72"') == 182.9
    assert units.parse_reach('72.0"') == 182.9
    assert units.inches_to_cm(72) == 182.9
    assert units.inches_to_cm(None) is None


def test_x_of_y_and_times():
    assert units.parse_x_of_y("24 of 47") == (24, 47)
    assert units.parse_x_of_y("---") == (None, None)
    assert units.parse_mmss("4:54") == 294
    assert units.parse_mmss("--") is None


def test_fight_seconds_uses_round_lengths():
    assert units.fight_seconds(2, 294, "5 Rnd (5-5-5-5-5)") == 594
    assert units.fight_seconds(3, 300, "3 Rnd (5-5-5)") == 900
    # Early UFC: one long round plus overtime
    assert units.fight_seconds(2, 60, "1 Rnd + OT (12-3)") == 12 * 60 + 60
    assert units.fight_seconds(1, 104, "No Time Limit") == 104
    assert units.fight_seconds(None, 10, "3 Rnd (5-5-5)") is None


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Lightweight Bout", ("lightweight", "M")),
        ("UFC Light Heavyweight Title Bout", ("light_heavyweight", "M")),
        ("Heavyweight Bout", ("heavyweight", "M")),
        ("Women's Strawweight Bout", ("w_strawweight", "F")),
        ("UFC Women's Featherweight Title Bout", ("w_featherweight", "F")),
        ("W Flyweight", ("w_flyweight", "F")),
        ("W Strawweight", ("w_strawweight", "F")),
        ("Catch Weight Bout", ("catchweight", None)),
        ("Open Weight Bout", ("openweight", None)),
        ("Ultimate Fighter 29 Middleweight Tournament Title Bout", ("middleweight", "M")),
        ("UFC 1 Tournament Title Bout", (None, None)),
        ("--", (None, None)),
    ],
)
def test_division_mapping(label, expected):
    assert units.division_from_weight_class(label) == expected


def test_every_fixture_weight_class_maps():
    import json
    import sqlite3

    from tests.conftest import FIXTURES

    labels = [
        r[0]
        for r in sqlite3.connect(FIXTURES / "canon_mini.db").execute(
            "SELECT DISTINCT weight_class FROM fights f JOIN events e USING (event_id) WHERE promotion = 'UFC' AND e.date > '2000-01-01'"
        )
    ]
    payload = json.loads((FIXTURES / "espn" / "scoreboard_range.json").read_text())
    labels += [
        c["type"]["abbreviation"]
        for e in payload["events"]
        for c in e["competitions"]
        if c.get("type")
    ]
    for label in labels:
        assert units.division_from_weight_class(label)[0] is not None, label


def test_title_and_method():
    assert units.is_title_bout("UFC Featherweight Title Bout")
    assert not units.is_title_bout("Road To UFC 1 Lightweight Tournament Title Bout")
    assert units.method_group("KO/TKO") == "ko_tko"
    assert units.method_group("TKO - Doctor's Stoppage") == "ko_tko"
    assert units.method_group("Decision - Split") == "dec"
    assert units.method_group("Submission") == "sub"
    assert units.method_group("Overturned") == "nc"
    assert units.method_group("Could Not Continue") == "other"


def test_american_odds():
    assert units.american_to_prob(-200) == pytest.approx(2 / 3)
    assert units.american_to_prob(150) == pytest.approx(0.4)
    assert units.american_to_prob(None) is None
