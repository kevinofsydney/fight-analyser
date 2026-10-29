"""The hypothesis YAML format (docs/PRD.md §6.3)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from fightdb.features.stat_meta import OUTCOMES, feature_keys
from fightdb.models.matchup import MATCHUP_COLS
from fightdb.sources.units import DIVISIONS

Op = Literal[">", ">=", "<", "<=", "==", "!=", "between"]
CompareTo = Literal["baseline_50", "complement", "division_rate"]
Exclusion = Literal["draw", "no_contest", "dq"]
_DEFAULT_EXCLUDE: tuple[Exclusion, ...] = ("draw", "no_contest", "dq")

# Columns of bout_sides a condition may use (besides self_/opp_/diff_ feature columns).
_EXTRA_FEATURES = {
    "self_southpaw",
    "opp_southpaw",
    "self_moving_division",
    "opp_moving_division",
    "is_title",
    "scheduled_rounds",
    "year",
    "self_implied_prob",
    "self_p_win",
    "self_p_win_ko",
    "self_p_win_sub",
    "self_p_win_dec",
    "self_p_distance",
}


def allowed_features() -> set[str]:
    keys = feature_keys()
    cols = {f"{p}_{k}" for k in keys for p in ("self", "opp", "diff")}
    cols |= {f"{p}_{c}" for c in MATCHUP_COLS for p in ("self", "opp")}
    return cols | _EXTRA_FEATURES


class Population(BaseModel):
    divisions: list[str] = Field(default_factory=list)
    sex: Literal["M", "F"] | None = None
    date_from: date | None = date(2010, 1, 1)
    date_to: date | None = None
    title_only: bool = False
    scheduled_rounds: int | None = None
    min_ufc_fights_each: int = 0
    exclude_outcomes: list[Exclusion] = Field(default_factory=lambda: list(_DEFAULT_EXCLUDE))

    @field_validator("divisions")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        bad = [d for d in v if d not in DIVISIONS]
        if bad:
            raise ValueError(f"unknown division(s) {bad}; choose from {list(DIVISIONS)}")
        return v


class Clause(BaseModel):
    feature: str
    op: Op
    value: float | list[float]

    @field_validator("feature")
    @classmethod
    def _feature_exists(cls, v: str) -> str:
        if v not in allowed_features():
            raise ValueError(f"unknown feature '{v}'")
        return v

    @model_validator(mode="after")
    def _between(self) -> Clause:
        if self.op == "between":
            if not (isinstance(self.value, list) and len(self.value) == 2):
                raise ValueError("'between' needs value: [low, high]")
        elif isinstance(self.value, list):
            raise ValueError(f"op '{self.op}' needs a single number")
        return self


class Sweep(BaseModel):
    feature: str
    start: float = Field(alias="from")
    stop: float = Field(alias="to")
    step: float = 1.0
    op: Op = ">="

    model_config = {"populate_by_name": True}

    @field_validator("feature")
    @classmethod
    def _feature_exists(cls, v: str) -> str:
        if v not in allowed_features():
            raise ValueError(f"unknown feature '{v}'")
        return v


class Hypothesis(BaseModel):
    id: str
    title: str
    description: str | None = None
    population: Population = Field(default_factory=Population)
    condition: list[Clause] = Field(default_factory=list)
    outcome: str = "won"
    compare_to: CompareTo = "baseline_50"
    sweep: Sweep | None = None

    @field_validator("outcome")
    @classmethod
    def _outcome(cls, v: str) -> str:
        if v not in OUTCOMES:
            raise ValueError(f"unknown outcome '{v}'; choose from {list(OUTCOMES)}")
        return v


def load_hypothesis(path: Path) -> Hypothesis:
    return Hypothesis.model_validate(yaml.safe_load(path.read_text()))


def load_all(directory: Path) -> list[tuple[Path, Hypothesis]]:
    return [(p, load_hypothesis(p)) for p in sorted(directory.rglob("*.yaml"))]


def to_yaml(h: Hypothesis) -> str:
    data = h.model_dump(mode="json", by_alias=True, exclude_none=True)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
