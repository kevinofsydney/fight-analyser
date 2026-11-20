import math

import pandas as pd

from fightdb.app import bet_recs


def test_stake_is_fractional_kelly_in_quarter_units_and_capped():
    # p 0.6 at 2.0: Kelly f = 0.2, quarter Kelly on 100 units = 5 units, capped at 3.
    assert bet_recs.stake_units(0.6, 2.0) == bet_recs.MAX_UNITS
    # p 0.55 at 2.0: f = 0.1 -> 2.5 units.
    assert bet_recs.stake_units(0.55, 2.0) == 2.5
    assert bet_recs.stake_units(0.4, 2.0) == 0
    assert bet_recs.stake_units(0.5, math.nan) == 0


def test_recommend_needs_odds_edge_and_calibration():
    assert bet_recs.recommend(0.5, math.nan)[1].startswith("No odds found")
    assert bet_recs.recommend(0.5, 1.9)[0] == 0  # price shorter than fair odds
    assert "below" in bet_recs.recommend(0.5, 2.06)[1]  # +3% edge
    assert bet_recs.recommend(0.55, 2.0)[0] == 2.5
    assert bet_recs.recommend(0.55, 2.0, calibrated=False)[0] == 0


def test_selections_cover_moneyline_method_and_distance():
    a = pd.Series(
        {
            "fighter_name": "A",
            "self_p_win": 0.6,
            "self_p_win_ko": 0.2,
            "self_p_win_sub": 0.1,
            "self_p_win_dec": 0.3,
            "self_p_distance": 0.5,
        }
    )
    b = pd.Series(
        {
            "fighter_name": "B",
            "self_p_win": 0.4,
            "self_p_win_ko": 0.1,
            "self_p_win_sub": 0.1,
            "self_p_win_dec": 0.2,
            "self_p_distance": 0.5,
        }
    )
    s = {(x.market, x.selection): x.p for x in bet_recs.selections(a, b)}
    assert s[("Moneyline", "A")] == 0.6
    assert s[("Method of victory", "B by submission")] == 0.1
    assert abs(s[("How the fight ends", "By KO/TKO")] - 0.3) < 1e-12
    assert s[("Goes the distance", "No")] == 0.5
    assert len(s) == 2 + 6 + 3 + 2
