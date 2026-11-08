import pytest
from pydantic import ValidationError
from statsmodels.stats.proportion import proportion_confint

from fightdb.analytics import hypothesis, schema, stats
from fightdb.config import get_settings


def test_wilson_matches_statsmodels():
    for k, n in [(44, 149), (0, 10), (10, 10), (517, 1199)]:
        lo, hi = stats.wilson_ci(k, n)
        ref = proportion_confint(k, n, method="wilson")
        assert lo == pytest.approx(ref[0]) and hi == pytest.approx(ref[1])
    assert stats.wilson_ci(0, 0) == (0.0, 1.0)


def test_tests_and_difference():
    assert stats.binomial_test(50, 100, 0.5) == pytest.approx(1.0)
    assert stats.binomial_test(80, 100, 0.5) < 1e-6
    assert stats.two_proportion_test(30, 100, 30, 100) == pytest.approx(1.0)
    assert stats.two_proportion_test(2, 5, 1, 6) > 0.05  # small counts use Fisher's exact test
    d = stats.describe_difference(0.6, 0.5)
    assert d.pp == pytest.approx(10) and d.relative == pytest.approx(0.2)


def _h(**kw):
    base = dict(
        id="t",
        title="t",
        population={"divisions": ["lightweight"], "date_from": "2010-01-01"},
        condition=[{"feature": "diff_age_years", "op": ">", "value": 0}],
    )
    base.update(kw)
    return schema.Hypothesis.model_validate(base)


def test_planted_effect_is_found(synthetic_db):
    r = hypothesis.evaluate(synthetic_db, _h())
    assert 0.64 < r.rate < 0.76
    assert r.p_value < 0.001 and r.significant
    assert "percentage points above" in r.verdict
    assert len(r.bout_ids) == r.n


def test_symmetry_and_complement(synthetic_db):
    younger = hypothesis.evaluate(
        synthetic_db, _h(condition=[{"feature": "diff_age_years", "op": "<", "value": 0}])
    )
    older = hypothesis.evaluate(synthetic_db, _h())
    assert older.rate + younger.rate == pytest.approx(1.0, abs=0.02)
    comp = hypothesis.evaluate(synthetic_db, _h(compare_to="complement"))
    assert comp.baseline == pytest.approx(younger.rate, abs=0.02)


def test_no_effect_when_condition_is_irrelevant(synthetic_db):
    r = hypothesis.evaluate(
        synthetic_db, _h(condition=[{"feature": "self_ufc_fights", "op": ">=", "value": 0}])
    )
    assert r.rate == pytest.approx(0.5, abs=0.01)
    assert any("50% by construction" in w for w in r.warnings) is False


def test_sweep(synthetic_db):
    h = _h(sweep={"feature": "diff_age_years", "from": 0, "to": 6, "step": 2})
    df = hypothesis.sweep(synthetic_db, h)
    assert list(df["threshold"]) == [0, 2, 4, 6]
    assert (df["n"].diff().dropna() <= 0).all()


def test_schema_rejects_bad_input():
    with pytest.raises(ValidationError):
        _h(condition=[{"feature": "diff_age_years; DROP TABLE bouts", "op": ">", "value": 1}])
    with pytest.raises(ValidationError):
        _h(outcome="won_by_vibes")
    with pytest.raises(ValidationError):
        _h(population={"divisions": ["middleweighty"]})
    with pytest.raises(ValidationError):
        _h(condition=[{"feature": "diff_age_years", "op": "between", "value": 3}])


def test_example_hypotheses_validate():
    loaded = schema.load_all(get_settings().hypotheses_dir)
    assert len(loaded) >= 5
    for _, h in loaded:
        assert schema.Hypothesis.model_validate_json(h.model_dump_json(by_alias=True)) == h


def test_yaml_round_trip():
    h = _h(sweep={"feature": "diff_age_years", "from": 0, "to": 6, "step": 2})
    import yaml

    assert schema.Hypothesis.model_validate(yaml.safe_load(schema.to_yaml(h))) == h


def test_division_rate_baseline(synthetic_db):
    r = hypothesis.evaluate(synthetic_db, _h(compare_to="division_rate", outcome="went_distance"))
    assert 0 < r.baseline < 1
    assert r.baseline_label == "all bouts in this population"
    assert 0 <= r.p_value <= 1


@pytest.mark.parametrize(
    "outcome", ["won_ko", "won_sub", "won_dec", "went_distance", "finished_r1"]
)
def test_every_outcome_evaluates(synthetic_db, outcome):
    r = hypothesis.evaluate(synthetic_db, _h(outcome=outcome))
    assert r.n > 0 and 0 <= r.k <= r.n
    assert r.ci_low <= r.rate <= r.ci_high


def test_between_is_inclusive_range(synthetic_db):
    between = hypothesis.evaluate(
        synthetic_db,
        _h(condition=[{"feature": "diff_age_years", "op": "between", "value": [2, 5]}]),
    )
    manual = hypothesis.evaluate(
        synthetic_db,
        _h(
            condition=[
                {"feature": "diff_age_years", "op": ">=", "value": 2},
                {"feature": "diff_age_years", "op": "<=", "value": 5},
            ]
        ),
    )
    assert between.n == manual.n > 0 and between.k == manual.k


def test_matches_upcoming(con):
    from datetime import date

    from fightdb.pipeline import upsert
    from fightdb.pipeline.build import build_all
    from fightdb.sources.models import Event
    from tests.conftest import make_bout, synthetic_history

    hist = synthetic_history()
    dobs = {f.fighter_id: f.dob for f in hist.fighters}
    older, younger = sorted(["f1", "f2"], key=lambda f: dobs[f])
    assert dobs[older] != dobs[younger]
    upcoming_event = Event(
        event_id="up1", name="Upcoming", date=date(2030, 1, 1), status="upcoming", source="test"
    )
    scheduled = make_bout("up-b1", "up1", older, younger).model_copy(
        update=dict(
            status="scheduled",
            result_a=None,
            winner_id=None,
            method=None,
            method_group=None,
            end_round=None,
            end_time_s=None,
            fight_seconds=None,
        )
    )
    hist = hist.model_copy(
        update=dict(events=[*hist.events, upcoming_event], bouts=[*hist.bouts, scheduled])
    )
    upsert.upsert_history(con, hist)
    build_all(con, train_models=False)
    m = hypothesis.matches_upcoming(con, _h())
    assert list(m["bout_id"]) == ["up-b1"]
    assert list(m["fighter_id"]) == [older]
