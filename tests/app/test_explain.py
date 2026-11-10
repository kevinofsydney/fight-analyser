import math

import pandas as pd
import pytest

from fightdb.app import explain


def test_market_view_removes_margin_and_skips_broken_prices():
    prices = pd.DataFrame(
        {
            "bookmaker": ["A", "A", "B", "B", "Exchange", "Exchange"],
            "fighter_id": ["x", "y"] * 3,
            "decimal_odds": [1.60, 2.40, 1.65, 2.30, 1.02, 1.01],
        }
    )
    view = explain.market_view(prices, ["x", "y"])
    assert view is not None and view.loc["x", "books"] == 2
    fair_a = (1 / 1.60) / (1 / 1.60 + 1 / 2.40)
    fair_b = (1 / 1.65) / (1 / 1.65 + 1 / 2.30)
    assert view.loc["x", "market_p"] == pytest.approx((fair_a + fair_b) / 2)
    assert view.loc["x", "best_odds"] == 1.65 and view.loc["x", "best_book"] == "B"
    assert view["market_p"].sum() == pytest.approx(1)


def test_edge_table_maths():
    t = explain.edge_table(["X", "Y"], [0.6, 0.4], [0.55, 0.45], [1.9, 2.1])
    assert t["Edge"].tolist() == pytest.approx([5.0, -5.0])
    assert t["Model fair odds"].iloc[0] == pytest.approx(1 / 0.6)
    assert t["Expected return per $1"].tolist() == pytest.approx([0.6 * 1.9 - 1, 0.4 * 2.1 - 1])


def _side(name, **kw):
    return pd.Series({"fighter_name": name, **kw})


def test_result_and_likely_outcomes():
    a = _side(
        "Jon Jones",
        won=1.0,
        lost=0.0,
        draw=0.0,
        no_contest=0.0,
        method="Submission",
        method_group="sub",
        end_round=1,
        fight_seconds=124,
        self_p_win_ko=0.2,
        self_p_win_sub=0.15,
        self_p_win_dec=0.44,
    )
    b = _side("Ciryl Gane", won=0.0, self_p_win_ko=0.08, self_p_win_sub=0.03, self_p_win_dec=0.10)
    assert explain.result_text(a, b) == "**Jon Jones** won by Submission in round 1 at 2:04."
    outcomes = explain.likely_outcomes(a, b)
    assert outcomes is not None
    assert outcomes.iloc[0]["label"] == "Jon Jones by decision"
    assert explain.actual_outcome(a, b) == "a_sub"
    assert explain.likely_outcomes(a, b.drop("self_p_win_ko")) is None or math.isnan(
        float(b.get("self_p_win_ko", math.nan))
    )
