import pytest
from scipy import stats as sps

from fightdb.analytics import stats


@pytest.mark.parametrize(("k", "n"), [(0, 0), (0, 1), (0, 25), (25, 25), (1, 1)])
def test_wilson_edges_stay_in_unit_interval(k, n):
    lo, hi = stats.wilson_ci(k, n)
    assert 0.0 <= lo <= hi <= 1.0


def test_binomial_edges():
    assert stats.binomial_test(0, 0, 0.5) == 1.0
    assert stats.binomial_test(0, 20, 0.5) < 1e-5
    assert stats.binomial_test(20, 20, 0.5) < 1e-5
    assert stats.binomial_test(7, 20, 0.35) == pytest.approx(sps.binomtest(7, 20, 0.35).pvalue)


def test_two_proportion_small_counts_use_fisher():
    p = stats.two_proportion_test(3, 8, 1, 9)
    assert p == pytest.approx(sps.fisher_exact([[3, 5], [1, 8]]).pvalue)


def test_two_proportion_large_counts_match_z_test():
    from statsmodels.stats.proportion import proportions_ztest

    p = stats.two_proportion_test(120, 200, 95, 200)
    assert p == pytest.approx(proportions_ztest([120, 95], [200, 200])[1])


def test_two_proportion_degenerate():
    assert stats.two_proportion_test(0, 0, 3, 10) == 1.0
    assert stats.two_proportion_test(0, 50, 0, 50) == 1.0


def test_describe_difference_reports_both_scales():
    d = stats.describe_difference(0.41, 0.50)
    assert d.pp == pytest.approx(-9.0)
    assert d.relative == pytest.approx(-0.18)
    assert stats.describe_difference(0.2, 0.0).relative is None
