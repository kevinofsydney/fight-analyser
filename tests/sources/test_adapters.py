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
    diffs = {}
    for k in shared:
        d = _round_diff(cr[k].model_dump(), gr[k].model_dump())
        if d:
            diffs[k] = d
    # The two upstreams occasionally disagree after a stat correction on one side, so a couple of
    # rows may differ; more than that points at an adapter bug.
    assert len(diffs) <= max(2, 0.01 * len(shared)), diffs


CTRL_TOLERANCE_S = 5


def _round_diff(a: dict, b: dict) -> dict:
    out = {}
    for f in a:
        if a[f] == b[f]:
            continue
        if (
            f == "ctrl_s"
            and a[f] is not None
            and b[f] is not None
            and abs(a[f] - b[f]) <= CTRL_TOLERANCE_S
        ):
            continue
        out[f] = (a[f], b[f])
    return out


def test_espn_parse_range():
    payload = json.loads((FIXTURES / "espn" / "scoreboard_range.json").read_text(encoding="utf-8"))
    events = espn.parse_scoreboard(payload)
    assert events
    first = events[0]
    main = next(b for b in first.bouts if b.card_order == 0)
    assert main.scheduled_rounds == 5
    assert all(b.fighter_a.source_fighter_id and b.fighter_b.source_fighter_id for b in first.bouts)
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


def test_canon_judge_scores(canon, grec):
    names = {f.fighter_id: f.name for f in canon.fighters}
    js = [j for j in canon.judge_scores if j.bout_id == "1714950933f88ab9"]
    # Garbrandt vs. Barcelos went three rounds to a unanimous decision: 3 judges x 3 rounds x 2.
    assert len(js) == 18
    bell_r1 = {names[j.fighter_id]: j.score for j in js if j.judge == "Mike Bell" and j.round == 1}
    assert bell_r1["Raoni Barcelos"] == 9
    assert all(j.score in (7, 8, 9, 10) for j in canon.judge_scores)
    assert grec.judge_scores == []  # Greco has no scorecards


def test_canon_pro_fights_cover_every_promotion(canon):
    promotions = {p.promotion for p in canon.pro_fights}
    assert "UFC" in promotions and len(promotions) > 1  # the Inka card is in, as results only
    assert all(p.result_a in ("W", "L", "D") for p in canon.pro_fights)
    ufc = {b.bout_id: b for b in canon.bouts}
    shared = [p for p in canon.pro_fights if p.fight_id in ufc]
    assert shared
    for p in shared:
        b = ufc[p.fight_id]
        assert (p.fighter_a_id, p.fighter_b_id, p.result_a) == (
            b.fighter_a_id,
            b.fighter_b_id,
            b.result_a,
        )
        assert p.is_finish == (b.method_group in ("ko_tko", "sub"))
    no_contests = {b.bout_id for b in canon.bouts if b.result_a == "NC"}
    assert no_contests and not no_contests & {p.fight_id for p in canon.pro_fights}


def test_canon_pro_fighters_name_everyone_in_pro_fights(canon):
    in_fights = {x for p in canon.pro_fights for x in (p.fighter_a_id, p.fighter_b_id)}
    named = {p.fighter_id for p in canon.pro_fighters}
    assert named == in_fights
    assert any(x.startswith("fc-fighter:") for x in named)  # never fought in the UFC
    keys = [(p.fighter_id, p.name) for p in canon.pro_fighters]
    assert len(keys) == len(set(keys))
    # A fighter's main name matches the one the UFC import uses, and other spellings are kept.
    ufc = {f.fighter_id: f.name for f in canon.fighters}
    spellings: dict[str, set[str]] = {}
    for p in canon.pro_fighters:
        spellings.setdefault(p.fighter_id, set()).add(p.name)
    assert all(ufc[x] in spellings[x] for x in ufc if x in spellings)
    assert any(len(v) > 1 for v in spellings.values())
