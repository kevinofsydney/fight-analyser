"""Bet suggestions for one bout: the model's chance for each market against a price, staked by
edge (fractional Kelly) in units."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

MIN_EDGE = 0.05  # expected return per $1 below this is noise given how often the market wins
KELLY_FRACTION = 0.25
BANKROLL_UNITS = 100.0
MAX_UNITS = 3.0


@dataclass(frozen=True)
class Selection:
    market: str
    selection: str
    p: float
    method: str | None = None  # "ko", "sub" or "dec": which calibration gate applies


def selections(a: pd.Series, b: pd.Series) -> list[Selection]:
    """Every market the model prices for this bout. Round betting isn't here: the model doesn't
    predict which round a finish comes in."""

    def num(v: object) -> float:
        try:
            return float(v)  # type: ignore[arg-type]
        except TypeError, ValueError:
            return math.nan

    na, nb = str(a.fighter_name), str(b.fighter_name)
    out = [
        Selection("Moneyline", na, num(a.self_p_win)),
        Selection("Moneyline", nb, num(b.self_p_win)),
    ]
    names = {"ko": "KO/TKO", "sub": "submission", "dec": "decision"}
    for side, name in ((a, na), (b, nb)):
        for m, label in names.items():
            out.append(
                Selection(
                    "Method of victory", f"{name} by {label}", num(side[f"self_p_win_{m}"]), m
                )
            )
    for m, label in names.items():
        p = num(a[f"self_p_win_{m}"]) + num(b[f"self_p_win_{m}"])
        out.append(Selection("How the fight ends", f"By {label}", p, m))
    dist = num(a.self_p_distance)
    out.append(Selection("Goes the distance", "Yes", dist, "dec"))
    out.append(Selection("Goes the distance", "No", 1 - dist, "dec"))
    return [s for s in out if not math.isnan(s.p)]


def stake_units(p: float, odds: float) -> float:
    """Fractional Kelly on a 100-unit bankroll, in quarter units, capped."""
    if math.isnan(odds) or odds <= 1 or math.isnan(p):
        return 0.0
    f = (p * odds - 1) / (odds - 1)
    if f <= 0:
        return 0.0
    return min(MAX_UNITS, math.floor(4 * BANKROLL_UNITS * KELLY_FRACTION * f) / 4)


def recommend(p: float, odds: float, calibrated: bool = True) -> tuple[float, str]:
    """(units, reason). Units are 0 when there's no bet."""
    if odds is None or math.isnan(odds):
        return 0.0, "No odds found. Enter a price to check it."
    if odds <= 1:
        return 0.0, "Decimal odds must be above 1."
    ev = p * odds - 1
    if not calibrated:
        return 0.0, (
            "No bet: the model's chances for this kind of finish haven't passed their "
            "calibration check."
        )
    if ev < MIN_EDGE:
        if ev < 0:
            return 0.0, f"No bet: the price is shorter than the model's fair odds ({1 / p:.2f})."
        return 0.0, f"No bet: edge {ev:+.0%} is below the {MIN_EDGE:.0%} minimum."
    units = stake_units(p, odds)
    if units <= 0:
        return 0.0, f"No bet: edge {ev:+.0%} is too small to stake."
    return units, f"Bet: expected return {ev:+.0%} per $1."
