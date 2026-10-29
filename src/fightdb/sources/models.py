"""Source-neutral records every adapter produces. Contract file: see AGENTS.md before changing.

Units: centimetres, seconds, `datetime.date`. Missing values are `None`, never 0.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

Result = Literal["W", "L", "D", "NC"]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True)


class Event(_Record):
    event_id: str
    name: str
    date: date | None
    location: str | None = None
    status: Literal["upcoming", "completed", "cancelled", "past"]
    source: str


class Fighter(_Record):
    fighter_id: str
    name: str
    nickname: str | None = None
    dob: date | None = None
    height_cm: float | None = None
    reach_cm: float | None = None
    stance: str | None = None
    sex: Literal["M", "F"] | None = None
    pro_wins: int | None = None
    pro_losses: int | None = None
    pro_draws: int | None = None
    nationality: str | None = None
    source: str


class FighterAlias(_Record):
    source: str
    source_id: str
    fighter_id: str
    name: str | None = None
    method: str = "source"


class Bout(_Record):
    bout_id: str
    event_id: str
    weight_class: str | None
    division: str | None
    sex: Literal["M", "F"] | None
    is_title: bool = False
    scheduled_rounds: int | None = None
    card_order: int | None = None
    fighter_a_id: str
    fighter_b_id: str
    status: Literal["scheduled", "completed", "cancelled"]
    result_a: Result | None = None
    winner_id: str | None = None
    method: str | None = None
    method_group: str | None = None
    details: str | None = None
    end_round: int | None = None
    end_time_s: int | None = None
    fight_seconds: int | None = None
    time_format: str | None = None
    referee: str | None = None
    source: str


class RoundStats(_Record):
    bout_id: str
    fighter_id: str
    round: int
    kd: int | None = None
    sig_l: int | None = None
    sig_a: int | None = None
    tot_l: int | None = None
    tot_a: int | None = None
    td_l: int | None = None
    td_a: int | None = None
    sub_att: int | None = None
    rev: int | None = None
    ctrl_s: int | None = None
    head_l: int | None = None
    head_a: int | None = None
    body_l: int | None = None
    body_a: int | None = None
    leg_l: int | None = None
    leg_a: int | None = None
    dist_l: int | None = None
    dist_a: int | None = None
    clinch_l: int | None = None
    clinch_a: int | None = None
    ground_l: int | None = None
    ground_a: int | None = None


class OddsLine(_Record):
    bout_id: str
    fighter_id: str
    open_american: int | None = None
    close_low: int | None = None
    close_high: int | None = None
    implied_prob_close: float | None = None


class HistoryBundle(BaseModel):
    """Everything a history adapter yields in one import."""

    source: str
    source_version: str | None = None
    events: list[Event] = []
    fighters: list[Fighter] = []
    aliases: list[FighterAlias] = []
    bouts: list[Bout] = []
    rounds: list[RoundStats] = []
    odds: list[OddsLine] = []


class UpcomingFighter(_Record):
    espn_id: str
    name: str
    record: str | None = None


class UpcomingBout(_Record):
    espn_bout_id: str
    weight_class: str | None
    card_order: int | None
    fighter_a: UpcomingFighter
    fighter_b: UpcomingFighter
    scheduled_rounds: int | None = None
    is_title: bool = False
    status: Literal["scheduled", "completed", "cancelled"] = "scheduled"


class UpcomingEvent(_Record):
    espn_event_id: str
    name: str
    date: date
    location: str | None = None
    bouts: list[UpcomingBout]
