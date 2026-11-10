import numpy as np
import pandas as pd
import pytest

from fightdb.models import matchup, outcome, ratings, shrinkage


def test_log5_worked_example():
    """docs/ANALYSIS_IDEAS.md §1.1: 45% attacker vs 50%-allowing defender at a 36.3% league rate."""
    p = matchup.log5(0.45, 0.50, 0.363)
    assert p == pytest.approx(0.589, abs=0.001)
    assert 6.0 * p == pytest.approx(3.5, abs=0.05)


def test_log5_neutral_cases():
    assert matchup.log5(0.4, 0.3, 0.3) == pytest.approx(0.4)
    assert matchup.log5(0.3, 0.3, 0.3) == pytest.approx(0.3)


def test_shrinkage_beats_raw_on_simulated_data():
    rng = np.random.default_rng(1)
    true = rng.beta(30, 50, 400)
    n = rng.integers(2, 40, 400)
    x = rng.binomial(n, true)
    k = shrinkage.estimate_k_proportion(pd.Series(x), pd.Series(n))
    mu = x.sum() / n.sum()
    shrunk = shrinkage.shrink(pd.Series(x), pd.Series(n), mu, k)
    assert np.mean((shrunk - true) ** 2) < np.mean((x / n - true) ** 2)
    assert 30 < k < 200  # true prior strength is 80


def test_shrink_with_no_data_returns_prior():
    out = shrinkage.shrink(pd.Series([0.0]), pd.Series([0.0]), 0.36, 15)
    assert out.iloc[0] == pytest.approx(0.36)


def test_overdispersion_detects_bursty_counts():
    rng = np.random.default_rng(2)
    rows = []
    for f in range(100):
        rate = rng.gamma(5, 0.8)
        for _ in range(8):
            burst = rng.gamma(0.5, 2)  # fight-to-fight variation beyond Poisson
            rows.append({"fighter_id": f, "x": rng.poisson(rate * burst * 15), "t": 15.0})
    phi = shrinkage.overdispersion(pd.DataFrame(rows), "x", "t", "rate")
    assert phi > 2


def test_elo_winner_gains_and_is_zero_sum():
    bouts = pd.DataFrame(
        [
            {
                "bout_id": "1",
                "date": pd.Timestamp("2020-01-01"),
                "fighter_a_id": "a",
                "fighter_b_id": "b",
                "result_a": "W",
                "method_group": "dec",
            },
            {
                "bout_id": "2",
                "date": pd.Timestamp("2020-02-01"),
                "fighter_a_id": "a",
                "fighter_b_id": "c",
                "result_a": "L",
                "method_group": "ko_tko",
            },
        ]
    )
    pre, now = ratings.compute_elo(bouts)
    assert now["a"] < 1500 + 16 and now["b"] == pytest.approx(1484)
    assert sum(now.values()) == pytest.approx(1500 * 3)
    assert pre.loc[(pre.bout_id == "2") & (pre.fighter_id == "a"), "elo"].iloc[0] == pytest.approx(
        1516
    )


def test_outcome_model_is_symmetric(synthetic_db):
    sides = synthetic_db.execute("SELECT * FROM bout_sides").df()
    sides["self_p_win"] = np.nan
    model = outcome.fit(sides)
    p = model.predict(sides).merge(
        sides[["bout_id", "fighter_id", "opp_id"]], on=["bout_id", "fighter_id"]
    )
    pair = p.merge(
        p, left_on=["bout_id", "fighter_id"], right_on=["bout_id", "opp_id"], suffixes=("", "_o")
    )
    assert np.allclose(pair["p_win"] + pair["p_win_o"], 1.0)
    methods = pair[["p_win_ko", "p_win_sub", "p_win_dec"]].sum(axis=1)
    assert np.allclose(methods, pair["p_win"])
    # The planted effect (older fighter wins 70%) should be learned through the age feature.
    older = sides.merge(p, on=["bout_id", "fighter_id"])
    assert older.loc[older["diff_age_years"] > 3, "p_win"].mean() > 0.55


def _evals(bins, name=outcome.METHOD_MODEL, years=(2024, 2025, 2026)):
    import json

    import pandas as pd

    return pd.DataFrame(
        [{"model_name": name, "test_year": y, "reliability": json.dumps(bins)} for y in years]
    )


def test_is_calibrated_accepts_close_bins():
    bins = [{"bin": 0.1, "mean_pred": 0.12, "mean_obs": 0.15, "n": 60}]
    assert outcome.is_calibrated(_evals(bins), outcome.METHOD_MODEL)


def test_is_calibrated_rejects_a_well_populated_bad_bin():
    bins = [
        {"bin": 0.1, "mean_pred": 0.12, "mean_obs": 0.13, "n": 60},
        {"bin": 0.3, "mean_pred": 0.30, "mean_obs": 0.38, "n": 50},
    ]
    assert not outcome.is_calibrated(_evals(bins), outcome.METHOD_MODEL)


def test_is_calibrated_ignores_sparse_bins_and_other_models():
    bins = [
        {"bin": 0.1, "mean_pred": 0.12, "mean_obs": 0.13, "n": 60},
        {"bin": 0.8, "mean_pred": 0.80, "mean_obs": 0.60, "n": 10},
    ]
    assert outcome.is_calibrated(_evals(bins), outcome.METHOD_MODEL)
    assert not outcome.is_calibrated(_evals(bins), outcome.WIN_MODEL)


def test_walk_forward_evaluates_method_model(synthetic_db):
    sides = synthetic_db.execute("SELECT * FROM bout_sides").df()
    sides["year"] = sides["year"] + 2  # synthetic data starts in 2011; tests start in 2012
    _, evals = outcome.walk_forward(sides)
    method = evals[evals["model_name"] == outcome.METHOD_MODEL]
    assert not method.empty
    assert (method["brier"] > 0).all() and (method["brier"] < 2).all()
    win = evals[evals["model_name"] == outcome.WIN_MODEL]
    assert set(method["test_year"]) == set(win["test_year"])


def test_method_reliability_counts_each_probability_once(synthetic_db):
    sides = synthetic_db.execute("SELECT * FROM bout_sides").df()
    sides["self_p_win"] = np.nan
    model = outcome.fit(sides)
    p = model.predict(sides)
    decided = sides[(sides["won"] + sides["lost"]) == 1]
    hits, wins = outcome._win_by_method(decided, p)
    # one row per fighter per decisive bout, three "wins by X" columns, no mirrored loss_* copies
    assert hits.shape == wins.shape == (len(decided), 3)
    assert hits.sum() == len(decided) / 2  # exactly one winner per bout
    pooled = outcome._method_reliability(decided, p)
    assert sum(b["n"] for b in pooled) == 3 * len(decided)


def test_walk_forward_writes_per_method_reliability(synthetic_db):
    sides = synthetic_db.execute("SELECT * FROM bout_sides").df()
    sides["year"] = sides["year"] + 2
    _, evals = outcome.walk_forward(sides)
    win_years = set(evals.loc[evals["model_name"] == outcome.WIN_MODEL, "test_year"])
    for m in outcome.METHODS:
        rows = evals[evals["model_name"] == outcome.method_model_name(m)]
        assert set(rows["test_year"]) == win_years
        assert rows["reliability"].notna().all()


def test_elo_breakdown_reproduces_compute_elo():
    bouts = pd.DataFrame(
        [
            ("1", "2020-01-01", "a", "b", "W", "dec"),
            ("2", "2020-02-01", "a", "c", "L", "ko_tko"),
            ("3", "2020-03-01", "c", "a", None, None),
            ("4", "2020-04-01", "b", "a", "L", "sub"),
        ],
        columns=["bout_id", "date", "fighter_a_id", "fighter_b_id", "result_a", "method_group"],
    ).assign(date=lambda d: pd.to_datetime(d["date"]))
    pre, now = ratings.compute_elo(bouts)
    elo = pre.set_index(["bout_id", "fighter_id"])["elo"]
    flip = {"W": "L", "L": "W"}
    history = pd.DataFrame(
        [
            {
                "bout_id": b.bout_id,
                "event_date": b.date,
                "opp_name": opp,
                "self_elo": elo[(b.bout_id, "a")],
                "opp_elo": elo[(b.bout_id, opp)],
                "result": b.result_a if b.fighter_a_id == "a" else flip.get(b.result_a),
                "method_group": b.method_group,
            }
            for b in bouts.itertuples()
            for opp in [b.fighter_b_id if b.fighter_a_id == "a" else b.fighter_a_id]
        ]
    )
    steps = ratings.elo_breakdown(history)
    assert steps["k"].tolist()[:2] == [32.0, 40.0] and pd.isna(steps["k"].iloc[2])
    assert steps["change"].iloc[2] == 0.0
    assert steps["elo_after"].iloc[-1] == pytest.approx(now["a"])
    assert 1500 + steps["change"].sum() == pytest.approx(now["a"])


def test_saved_weights_rebuild_identical_models_and_pushes_add_up(synthetic_db):
    sides = synthetic_db.execute("SELECT * FROM bout_sides WHERE status = 'completed'").df()
    model = outcome.fit(sides)
    weights = outcome.model_weights(model, None)
    assert weights["model_year"].isna().all()
    assert set(weights["model"]) == {"win", "method"}
    rebuilt = outcome.from_weights(weights)

    expected = model.predict(sides)
    got = rebuilt.predict(sides)
    cols = ["p_win", "p_win_ko", "p_win_sub", "p_win_dec", "p_distance"]
    assert np.allclose(got[cols].to_numpy(), expected[cols].to_numpy(), equal_nan=True)

    p = expected.set_index(["bout_id", "fighter_id"])["p_win"]
    for _, row in sides.head(25).iterrows():
        pushes = outcome.win_contributions(rebuilt, row.to_frame().T)
        assert list(pushes["feature"]) == outcome.DIFF_FEATURES
        total = pushes["push"].sum()
        assert 1 / (1 + np.exp(-total)) == pytest.approx(p[(row.bout_id, row.fighter_id)])


def test_current_snapshots_add_rows_without_changing_bout_snapshots(synthetic_db):
    from fightdb.features import snapshots

    plain, _ = snapshots.build_snapshots(synthetic_db)
    both, _ = snapshots.build_snapshots(synthetic_db, pd.Timestamp("2030-01-01"))
    current = both[both["bout_id"].str.startswith(snapshots.CURRENT_PREFIX)]
    rest = both[~both["bout_id"].str.startswith(snapshots.CURRENT_PREFIX)]
    key = ["bout_id", "fighter_id"]
    pd.testing.assert_frame_equal(
        plain.sort_values(key).reset_index(drop=True),
        rest.sort_values(key).reset_index(drop=True),
        check_dtype=False,
    )
    done = synthetic_db.execute(
        """SELECT count(DISTINCT fighter_id) FROM bout_sides WHERE status = 'completed'"""
    ).fetchone()[0]
    assert len(current) == done and current["fighter_id"].is_unique
    _, elo_now = ratings.compute_elo(
        synthetic_db.execute(
            """SELECT b.bout_id, e.date, b.fighter_a_id, b.fighter_b_id, b.result_a, b.method_group
               FROM bouts b JOIN events e USING (event_id) WHERE b.status = 'completed'"""
        )
        .df()
        .assign(date=lambda d: pd.to_datetime(d["date"]))
    )
    assert current.set_index("fighter_id")["elo"].to_dict() == pytest.approx(elo_now)


def test_feature_importance_scores_every_input_out_of_sample(synthetic_db):
    sides = synthetic_db.execute("SELECT * FROM bout_sides").df()
    imp = outcome.feature_importance(sides)
    assert list(imp.columns) == outcome.IMPORTANCE_COLUMNS
    assert list(imp["feature"]) == outcome.DIFF_FEATURES
    assert (imp["n"] > 0).all()
    assert imp["log_loss_none"].iloc[0] == pytest.approx(np.log(2))
    assert imp[["log_loss_without", "log_loss_alone", "accuracy_alone"]].notna().all().all()
