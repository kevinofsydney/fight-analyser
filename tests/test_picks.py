from datetime import date

import numpy as np
import pandas as pd
import pytest

from fightdb import picks

TODAY = date(2026, 11, 12)
FIGHT_DAY = date(2026, 11, 14)


def _pick(**changes):
    base = {
        "bout_id": "espn:1",
        "event_name": "UFC Test",
        "event_date": FIGHT_DAY,
        "fighter_a_id": "a",
        "fighter_a_name": "Alex Able",
        "fighter_b_id": "b",
        "fighter_b_name": "Ben Baker",
        "pick_id": "a",
        "p_pick": 0.7,
        "model_p_pick": 0.6,
        "market_p_pick": 0.65,
    }
    return picks.Pick(**{**base, **changes})


def _result(**changes):
    base = {
        "bout_id": "hex1",
        "event_date": pd.Timestamp(FIGHT_DAY),
        "fighter_id": "a",
        "fighter_name": "Alex Able",
        "opp_id": "b",
        "opp_name": "Ben Baker",
        "won": 1.0,
        "lost": 0.0,
        "draw": 0.0,
        "no_contest": 0.0,
        "method": "KO/TKO",
        "self_implied_prob": 0.8,
    }
    return {**base, **changes}


def test_missing_file_is_no_picks_and_is_not_created(tmp_path):
    path = tmp_path / "picks.sqlite"
    assert picks.load(path).empty
    picks.delete(path, "espn:1", TODAY)
    assert not path.exists()


def test_save_then_change_keeps_one_pick_per_bout(tmp_path):
    path = tmp_path / "sub" / "picks.sqlite"
    picks.save(path, _pick(), TODAY)
    picks.save(path, _pick(pick_id="b", p_pick=0.55), TODAY)
    saved = picks.load(path)
    assert len(saved) == 1
    assert saved.iloc[0]["pick_id"] == "b"
    assert saved.iloc[0]["p_pick"] == pytest.approx(0.55)
    assert saved.iloc[0]["event_date"] == pd.Timestamp(FIGHT_DAY)


def test_picks_lock_after_the_event_date(tmp_path):
    path = tmp_path / "picks.sqlite"
    picks.save(path, _pick(), today=FIGHT_DAY)  # the day itself is still open
    after = date(2026, 11, 15)
    with pytest.raises(picks.PickLockedError):
        picks.save(path, _pick(pick_id="b"), today=after)
    with pytest.raises(picks.PickLockedError):
        picks.delete(path, "espn:1", today=after)
    # Re-dating the bout does not reopen a pick stored for a past date.
    with pytest.raises(picks.PickLockedError):
        picks.save(path, _pick(event_date=date(2026, 12, 1)), today=after)
    assert picks.load(path).iloc[0]["pick_id"] == "a"


def test_pick_must_be_a_fighter_in_the_bout_and_at_least_even(tmp_path):
    with pytest.raises(ValueError, match="one of the two"):
        picks.save(tmp_path / "p.sqlite", _pick(pick_id="z"), TODAY)
    with pytest.raises(ValueError):
        _pick(p_pick=0.4)


def test_delete_removes_an_open_pick(tmp_path):
    path = tmp_path / "picks.sqlite"
    picks.save(path, _pick(), TODAY)
    picks.delete(path, "espn:1", TODAY)
    assert picks.load(path).empty


def _scored(tmp_path, pick, results):
    path = tmp_path / "picks.sqlite"
    picks.save(path, pick, TODAY)
    return picks.score(picks.load(path), pd.DataFrame(results, columns=list(_result()))).iloc[0]


def test_score_finds_the_result_under_its_new_bout_id(tmp_path):
    s = _scored(tmp_path, _pick(), [_result()])
    assert (s["outcome"], s["y"], s["result_bout_id"]) == ("won", 1.0, "hex1")
    assert s["market_p"] == pytest.approx(0.8)  # closing line beats the price at save time
    assert s["model_p"] == pytest.approx(0.6)
    assert s["pick_name"] == "Alex Able"


def test_score_flips_sides_when_the_result_lists_the_other_fighter_first(tmp_path):
    flipped = _result(fighter_id="b", fighter_name="Ben Baker", opp_id="a", opp_name="Alex Able")
    s = _scored(tmp_path, _pick(), [flipped])  # Baker listed first and won: the pick lost
    assert (s["outcome"], s["y"]) == ("lost", 0.0)
    assert s["market_p"] == pytest.approx(0.2)


def test_score_follows_a_debutant_whose_id_changed(tmp_path):
    pick = _pick(fighter_b_id="espn:99", pick_id="espn:99", fighter_b_name="Bén Baker")
    s = _scored(tmp_path, pick, [_result(opp_id="hex-new", won=0.0, lost=1.0)])
    assert (s["outcome"], s["y"]) == ("won", 1.0)


def test_score_leaves_other_bouts_and_far_dates_pending(tmp_path):
    rematch = _result(event_date=pd.Timestamp("2027-03-01"))
    other = _result(bout_id="hex2", opp_id="c", opp_name="Carl Cole")
    s = _scored(tmp_path, _pick(), [rematch, other])
    assert s["outcome"] == "pending"
    assert np.isnan(s["y"])
    assert s["market_p"] == pytest.approx(0.65)  # nothing better than the saved price yet


def test_score_draws_and_no_contests_are_not_counted(tmp_path):
    s = _scored(tmp_path, _pick(), [_result(won=0.0, draw=1.0)])
    assert s["outcome"] == "draw"
    assert np.isnan(s["y"])
    scored = pd.DataFrame([s])
    assert picks.summarise(scored).empty


def test_summarise_compares_on_the_same_picks():
    scored = pd.DataFrame(
        {
            "y": [1.0, 0.0, 1.0, np.nan],
            "p_pick": [0.9, 0.6, 0.7, 0.8],
            "model_p": [0.6, 0.4, 0.3, 0.5],
            "market_p": [0.8, np.nan, 0.6, 0.5],
        }
    )
    out = picks.summarise(scored).set_index(["who", "on"])
    you = out.loc[("You", "all your picks")]
    assert you["n"] == 3
    assert you["accuracy"] == pytest.approx(2 / 3)
    assert you["log_loss"] == pytest.approx(-(np.log(0.9) + np.log(0.4) + np.log(0.7)) / 3)
    assert you["brier"] == pytest.approx((0.01 + 0.36 + 0.09) / 3)
    # The model called the second fight for the other side (0.4) and was right; wrong on the third.
    assert out.loc[("Model", "picks it priced"), "accuracy"] == pytest.approx(2 / 3)
    assert out.loc[("Bookmakers", "picks it priced"), "n"] == 2
    assert out.loc[("You", "same picks as bookmakers"), "n"] == 2
    assert ("You", "same picks as model") not in out.index


def test_log_loss_is_capped_for_a_certain_miss():
    scored = pd.DataFrame({"y": [0.0], "p_pick": [0.99], "model_p": [1.0], "market_p": [np.nan]})
    out = picks.summarise(scored).set_index("who")
    assert out.loc["Model", "log_loss"] == pytest.approx(-np.log(picks.P_CLIP))
