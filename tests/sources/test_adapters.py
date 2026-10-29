import json

import pytest

from fightdb.sources import espn, fight_canon, greco
from tests.conftest import FIXTURES

UFC333 = "cc18abc046b382c9"
VOLK = "Alexander Volkanovski"


@pytest.fixture(scope="module")
def canon():
    return fight_canon.load(FIXTURES / "canon_mini.db", "test")


@pytest.fixture(scope="module")
def grec():
    return greco.load(FIXTURES / "greco", "test")


def test_canon_filters_to_ufc(canon):
    names = {e.name for e in canon.events}
    assert "Inka 62 - Sudamerica En Lo Mas Alto" not in names
    assert len(canon.events) == 3
    assert all(e.status == "completed" for e in canon.events)


def test_canon_known_values(canon):
    volk = next(f for f in canon.fighters if f.name == VOLK)
    assert volk.reach_cm == pytest.approx(180.3)  # 71 in
    main = next(
        b
        for b in canon.bouts
        if b.event_id == UFC333
        and volk.fighter_id in (b.fighter_a_id, b.fighter_b_id)
        and b.is_title
    )
    assert main.method_group == "ko_tko"
    assert main.end_round == 2
    assert main.fight_seconds == 300 + 294
    assert main.winner_id == volk.fighter_id
    r1 = next(
        r
        for r in canon.rounds
        if r.bout_id == main.bout_id and r.fighter_id == volk.fighter_id and r.round == 1
    )
    assert (r1.sig_l, r1.sig_a, r1.ctrl_s) == (24, 47, 18)


def test_canon_results_and_odds(canon):
    results = {b.result_a for b in canon.bouts}
    assert {"W", "D", "NC"} <= results | {"L"}
    by_bout: dict[str, float] = {}
    for o in canon.odds:
        if o.implied_prob_close is not None:
            by_bout[o.bout_id] = by_bout.get(o.bout_id, 0) + o.implied_prob_close
    assert by_bout, "fixture should include odds"
    assert all(abs(v - 1) < 1e-3 for v in by_bout.values())


def test_greco_matches_canon(canon, grec):
    cb = {b.bout_id: b for b in canon.bouts}
    gb = {b.bout_id: b for b in grec.bouts}
    common = cb.keys() & gb.keys()
    assert len(common) >= 0.9 * len(cb)
    for k in common:
        a, b = cb[k], gb[k]
        assert (a.result_a, a.method_group, a.fight_seconds, a.division) == (
            b.result_a,
            b.method_group,
            b.fight_seconds,
            b.division,
        ), k
    cr = {(r.bout_id, r.fighter_id, r.round): r for r in canon.rounds}
    gr = {(r.bout_id, r.fighter_id, r.round): r for r in grec.rounds}
    shared = cr.keys() & gr.keys()
    assert len(shared) >= 0.9 * len(cr)
    mismatched = [k for k in shared if cr[k] != gr[k]]
    assert len(mismatched) <= 0.01 * len(shared)


def test_espn_parse_range():
    payload = json.loads((FIXTURES / "espn" / "scoreboard_range.json").read_text())
    events = espn.parse_scoreboard(payload)
    assert events
    first = events[0]
    main = next(b for b in first.bouts if b.card_order == 0)
    assert main.scheduled_rounds == 5
    assert all(b.fighter_a.espn_id and b.fighter_b.espn_id for b in first.bouts)
    assert sorted(b.card_order for b in first.bouts) == list(range(len(first.bouts)))


def test_espn_rejects_unknown_shape():
    with pytest.raises(espn.EspnFormatError):
        espn.parse_scoreboard({"nothing": []})
    with pytest.raises(espn.EspnFormatError):
        espn.parse_scoreboard(
            {
                "events": [
                    {
                        "id": "1",
                        "name": "x",
                        "date": "2026-01-01T00:00Z",
                        "competitions": [{"id": "2", "competitors": [{}]}],
                    }
                ]
            }
        )
