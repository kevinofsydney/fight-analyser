import math

import pandas as pd
import pytest

from fightdb.analytics.hypothesis import HypothesisResult
from fightdb.analytics.schema import Hypothesis
from fightdb.app import fight_projection, hypothesis_view


def test_expected_rounds_mixes_distance_and_average_finish():
    assert fight_projection.expected_rounds(1.0, 3, 400) == 3
    assert fight_projection.expected_rounds(0.0, 3, 450) == pytest.approx(1.5)
    assert fight_projection.expected_rounds(0.5, 5, 600) == pytest.approx(3.5)
    assert fight_projection.expected_rounds(math.nan, 3, 450) == 3


def test_totals_scale_per_round_projections():
    a = pd.Series(
        {
            "fighter_name": "A",
            "self_x_sig_r": 10.0,
            "self_p_sig_land": 0.5,
            "self_x_td_att_r": 2.0,
            "self_x_td_r": 0.5,
            "self_x_kd_r": 0.1,
            "self_x_sub_att_r": 0.2,
            "self_x_ctrl_s_r": 60.0,
        }
    )
    b = a.copy()
    b["fighter_name"], b["self_x_td_att_r"], b["self_x_td_r"] = "B", 1.0, 0.25
    t = fight_projection.totals(a, b, 3).set_index("Stat")
    assert t.loc["Significant strikes landed", "A"] == 30
    assert t.loc["Significant strikes thrown", "A"] == 60
    assert t.loc["Takedowns landed", "A"] == 1.5
    # A defends B's attempts that fail: 3 attempts, 0.75 landed.
    assert t.loc["Takedowns defended", "A"] == pytest.approx(2.25)
    assert t.loc["Control time (min)", "A"] == 3


def _result(p_value: float, diff_pp: float) -> HypothesisResult:
    return HypothesisResult(
        hypothesis_id="h",
        n=1000,
        k=400,
        rate=0.4,
        ci_low=0.37,
        ci_high=0.43,
        baseline=0.5,
        baseline_label="50%",
        diff_pp=diff_pp,
        diff_relative=None,
        p_value=p_value,
        excluded_missing=0,
        verdict="",
    )


def test_hypothesis_so_what_says_whether_the_model_already_knows():
    age = Hypothesis(
        id="age",
        title="Older by 5",
        condition=[{"feature": "diff_age_years", "op": ">=", "value": 5}],
    )
    wear = Hypothesis(
        id="wear",
        title="Wear",
        condition=[{"feature": "self_wear_fights_75", "op": ">=", "value": 3}],
    )
    assert hypothesis_view.model_inputs_used(age) == ["Age"]
    assert hypothesis_view.model_inputs_used(wear) == []
    assert "already uses age" in hypothesis_view.so_what(age, _result(0.001, -13), "X", 0.3)
    assert "doesn't use this" in hypothesis_view.so_what(wear, _result(0.001, -9), "X", 0.3)
    assert "Ignore it" in hypothesis_view.so_what(wear, _result(0.2, -2), "X", 0.3)
    table = hypothesis_view.table(
        [
            hypothesis_view.row(age, _result(0.001, -13), "X", 0.3),
            hypothesis_view.row(wear, _result(0.001, -9), "X", 0.3),
        ]
    )
    assert table["Pattern"].iloc[0] == "Wear"
