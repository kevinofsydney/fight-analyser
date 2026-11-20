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


# Expected return per $1 on the better side at which a bout earns each extra star: the fifths of
# that number over 2015+ bouts at the best closing price, so each star is about 20% of bouts.
# Returns by fifth were -1% to +14% with no steady rise, so more stars means the model disagrees
# with the market more, not that the bet has done better.
OPPORTUNITY_STEPS = (0.05, 0.15, 0.27, 0.50)


def opportunity(p: float, best_a: float, best_b: float) -> tuple[int, int, float]:
    """(stars 1-5, side 0 for A / 1 for B, expected return) for the better moneyline bet by the
    model's numbers; stars 0 when there is no price."""
    if any(math.isnan(x) for x in (p, best_a, best_b)):
        return 0, 0, math.nan
    ev_a, ev_b = p * best_a - 1, (1 - p) * best_b - 1
    side, ev = (0, ev_a) if ev_a >= ev_b else (1, ev_b)
    stars = 1 + sum(ev >= step for step in OPPORTUNITY_STEPS)
    return stars, side, ev


def opportunity_label(p: float, best_a: float, best_b: float, name_a: str, name_b: str) -> str:
    """e.g. "★★★☆☆  Navajo Stirling +20%", or "No odds yet"."""
    stars, side, ev = opportunity(p, best_a, best_b)
    if not stars:
        return "No odds yet"
    return f"{'★' * stars}{'☆' * (5 - stars)}  {name_a if side == 0 else name_b} {ev:+.0%}"
