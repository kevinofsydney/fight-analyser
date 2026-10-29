"""Empirical-Bayes shrinkage: pull small-sample rates towards the division average.

shrunk = (successes + k * prior_rate) / (exposure + k)

`k` (prior strength, in the stat's exposure units) is estimated from career totals by the
method of moments, so it reflects how much fighters genuinely differ on that stat.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

K_MIN = 1.0
# Caps keep a noisy estimate from erasing real differences entirely:
# proportions in attempts, rates in minutes (150 min = about ten full fights).
K_MAX_PROP, K_MAX_RATE = 1000.0, 150.0


def overdispersion(
    fights: pd.DataFrame, x: str, t: str, kind: str, by: str = "fighter_id"
) -> float:
    """Quasi-likelihood dispersion (Pearson chi-square / dof) of fight-level counts around each
    fighter's own rate. Strikes come in bursts, so fight-to-fight noise is larger than Poisson
    or binomial noise; ignoring that would under-shrink."""
    f = fights[[by, x, t]].dropna()
    f = f[f[t] > 0]
    if f.empty:
        return 1.0
    tot = f.groupby(by)[[x, t]].transform("sum")
    rate = tot[x] / tot[t]
    expected = rate * f[t]
    var = expected if kind == "rate" else f[t] * rate * (1 - rate)
    ok = var > 0
    chi2 = float((((f[x] - expected) ** 2)[ok] / var[ok]).sum())
    dof = int(ok.sum()) - f[by].nunique()
    return float(max(chi2 / dof, 1.0)) if dof > 0 else 1.0


def estimate_k_proportion(
    x: pd.Series, n: pd.Series, min_n: float = 1.0, phi: float = 1.0
) -> float:
    """Beta-binomial prior strength for a proportion x/n (e.g. takedowns landed / attempted)."""
    mask = (n >= min_n) & n.notna() & x.notna()
    x, n = x[mask].astype(float), n[mask].astype(float)
    if len(n) < 10 or n.sum() == 0:
        return 20.0
    mu = x.sum() / n.sum()
    if mu <= 0 or mu >= 1:
        return 20.0
    p = x / n
    observed_var = float(np.average((p - mu) ** 2, weights=n))
    sampling_var = float(np.average(phi * mu * (1 - mu) / n, weights=n))
    between = observed_var - sampling_var
    if between <= 1e-9:
        return K_MAX_PROP
    return float(np.clip(mu * (1 - mu) / between - 1, K_MIN, K_MAX_PROP))


def estimate_k_rate(x: pd.Series, t: pd.Series, min_t: float = 1.0, phi: float = 1.0) -> float:
    """Gamma-Poisson prior strength (in exposure units) for a rate x/t (e.g. strikes per minute)."""
    mask = (t >= min_t) & t.notna() & x.notna()
    x, t = x[mask].astype(float), t[mask].astype(float)
    if len(t) < 10 or t.sum() == 0:
        return 10.0
    mu = x.sum() / t.sum()
    if mu <= 0:
        return 10.0
    lam = x / t
    observed_var = float(np.average((lam - mu) ** 2, weights=t))
    sampling_var = float(np.average(phi * mu / t, weights=t))
    between = observed_var - sampling_var
    if between <= 1e-12:
        return K_MAX_RATE
    return float(np.clip(mu / between, K_MIN, K_MAX_RATE))


def shrink(x: pd.Series, exposure: pd.Series, prior: pd.Series | float, k: float) -> pd.Series:
    return (x.fillna(0) + k * prior) / (exposure.fillna(0) + k)
