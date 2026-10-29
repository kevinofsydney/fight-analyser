"""Small, well-tested statistics helpers for hypothesis results."""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy import stats


def wilson_ci(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    z = stats.norm.ppf(1 - alpha / 2)
    p = k / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def binomial_test(k: int, n: int, p0: float) -> float:
    if n == 0:
        return 1.0
    return float(stats.binomtest(k, n, p0).pvalue)


def two_proportion_test(k1: int, n1: int, k2: int, n2: int) -> float:
    """Two-sided test that two proportions differ. Fisher's exact test for small counts."""
    if n1 == 0 or n2 == 0:
        return 1.0
    table = [[k1, n1 - k1], [k2, n2 - k2]]
    expected_min = min(
        (k1 + k2) * n1 / (n1 + n2),
        (k1 + k2) * n2 / (n1 + n2),
        (n1 + n2 - k1 - k2) * n1 / (n1 + n2),
        (n1 + n2 - k1 - k2) * n2 / (n1 + n2),
    )
    if expected_min < 5:
        return float(stats.fisher_exact(table).pvalue)
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = (k1 / n1 - k2 / n2) / se
    return float(2 * stats.norm.sf(abs(z)))


@dataclass(frozen=True)
class Difference:
    pp: float  # percentage points
    relative: float | None  # relative change, e.g. 0.2 = 20% higher


def describe_difference(rate: float, baseline: float) -> Difference:
    rel = (rate / baseline - 1) if baseline > 0 else None
    return Difference(pp=(rate - baseline) * 100, relative=rel)
