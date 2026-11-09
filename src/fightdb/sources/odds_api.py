"""Parse upcoming moneyline quotes from The Odds API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class Quote(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    price: float = Field(gt=1, allow_inf_nan=False)


class Market(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    last_update: datetime | None = None
    outcomes: list[Quote]


class Bookmaker(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    title: str
    last_update: datetime | None = None
    markets: list[Market]


class OddsEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    commence_time: datetime
    home_team: str
    away_team: str
    bookmakers: list[Bookmaker]


def parse(payload: object) -> list[OddsEvent]:
    return TypeAdapter(list[OddsEvent]).validate_python(payload)
