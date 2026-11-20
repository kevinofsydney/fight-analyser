"""Weigh-in misses from the hand-kept `data/overrides/ufc_weight_misses.csv`, linked to fighters
and bouts.

The CSV lists only exceptions from 2018 on, so a fighter with no row is "no miss recorded", not
"always made weight", and nothing before 2018 is known. The table is rebuilt from the file on
every refresh; the file is the source of truth.
"""

from __future__ import annotations

import csv
import logging
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal

import duckdb
import pandas as pd
import yaml
from pydantic import BaseModel, field_validator

from fightdb.sources.identity import name_key

log = logging.getLogger(__name__)

FILE_NAME = "ufc_weight_misses.csv"
COVERAGE_START = date(2018, 1, 1)
RecordType = Literal["official_miss", "weight_unavailable", "revised_limit", "title_eligibility"]


class WeightMiss(BaseModel):
    card_date: date
    fighter_name: str
    card_name: str
    record_type: RecordType
    pounds_over: float | None = None
    notes: str | None = None
    source_url: str

    @field_validator("pounds_over", "notes", mode="before")
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v


def read_csv(path: Path) -> list[WeightMiss]:
    with path.open(encoding="utf-8", newline="") as f:
        return [WeightMiss.model_validate(row) for row in csv.DictReader(f)]


TABLE = """CREATE OR REPLACE TABLE weight_misses (
    card_date     DATE NOT NULL,
    fighter_name  VARCHAR NOT NULL,
    card_name     VARCHAR NOT NULL,
    record_type   VARCHAR NOT NULL,
    pounds_over   DOUBLE,
    notes         VARCHAR,
    source_url    VARCHAR NOT NULL,
    fighter_id    VARCHAR,
    event_id      VARCHAR,
    bout_id       VARCHAR,
    opp_id        VARCHAR,
    link          VARCHAR      -- 'bout' | 'fighter' | NULL (unlinked)
)"""


def _card_fighters(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Every fighter on every bout, with the event date and bout status."""
    return con.execute(
        """SELECT e.event_id, e.date, b.bout_id, b.status, s.fighter_id, s.opp_id, f.name
           FROM bouts b JOIN events e USING (event_id),
           LATERAL (VALUES (b.fighter_a_id, b.fighter_b_id), (b.fighter_b_id, b.fighter_a_id))
               AS s(fighter_id, opp_id)
           JOIN fighters f ON f.fighter_id = s.fighter_id
           WHERE e.date IS NOT NULL"""
    ).df()


def _names(con: duckdb.DuckDBPyConnection) -> dict[str, set[str]]:
    rows = con.execute(
        "SELECT name, fighter_id FROM fighters UNION SELECT name, fighter_id FROM fighter_aliases "
        "WHERE name IS NOT NULL"
    ).fetchall()
    out: dict[str, set[str]] = {}
    for name, fid in rows:
        out.setdefault(name_key(name), set()).add(fid)
    return out


def _similar(a: str, b: str) -> bool:
    """Loose match, safe only within one card: "Phil Rowe" / "Philip Rowe", "Rongzhu" /
    "Rong Zhu", "Isabela de Padua" / "Isabella de Pádua"."""
    if a.replace(" ", "") == b.replace(" ", ""):
        return True
    ta, tb = set(a.split()), set(b.split())
    if len(ta & tb) >= 2 and (ta <= tb or tb <= ta):  # "José Miguel Delgado" / "Jose Delgado"
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.85


# A card can be stored twice (the completed history import and the old upcoming-feed copy);
# the completed bout is the real one.
_STATUS_RANK = {"completed": 0, "scheduled": 1}


def load_name_overrides(path: Path) -> dict[str, str]:
    """`names:` in `fighter_identity.yaml`: {<name as written in a hand-kept file>: <fighter_id>},
    for names no spelling rule can match (e.g. a fighter who changed surname)."""
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {name_key(str(k)): str(v) for k, v in (data.get("names") or {}).items()}


def link(
    con: duckdb.DuckDBPyConnection,
    misses: list[WeightMiss],
    name_overrides: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Match each miss to the fighter's bout on that card (event within a day; same name, or a
    close spelling among that card's fighters), else to the fighter alone when the name is
    unambiguous (e.g. the bout was cancelled and never stored)."""
    cards = _card_fighters(con)
    cards["key"] = cards["name"].map(name_key)
    cards["date"] = pd.to_datetime(cards["date"])
    cards["rank"] = cards["status"].map(_STATUS_RANK).fillna(9)
    names = _names(con)
    rows = []
    for m in misses:
        key = name_key(m.fighter_name)
        row = m.model_dump() | dict(
            fighter_id=None, event_id=None, bout_id=None, opp_id=None, link=None
        )
        day = pd.Timestamp(m.card_date)
        card = cards[(cards["date"] - day).abs() <= pd.Timedelta(days=1)]
        forced = (name_overrides or {}).get(key)
        hit = card[card["fighter_id"] == forced] if forced else card[card["key"] == key]
        if hit.empty and not forced:
            hit = card[card["key"].map(lambda k, key=key: _similar(k, key)).astype(bool)]
        if hit["fighter_id"].nunique() == 1:
            r = hit.sort_values(["rank", "event_id"]).iloc[0]
            row.update(
                fighter_id=r["fighter_id"],
                event_id=r["event_id"],
                bout_id=r["bout_id"] if r["rank"] == 0 else None,
                opp_id=r["opp_id"] if r["rank"] == 0 else None,
                link="bout" if r["rank"] == 0 else "fighter",
            )
        elif forced:
            row.update(fighter_id=forced, link="fighter")
        elif len(names.get(key, ())) == 1:
            row.update(fighter_id=next(iter(names[key])), link="fighter")
        rows.append(row)
    return pd.DataFrame(rows)


def load(con: duckdb.DuckDBPyConnection, overrides_dir: Path) -> dict[str, int]:
    """Rebuild `weight_misses` from the CSV. A missing file leaves an empty table."""
    con.execute(TABLE)
    path = overrides_dir / FILE_NAME
    if not path.exists():
        log.warning("%s not found; weight_misses left empty", path)
        return {"weight_misses": 0}
    overrides = load_name_overrides(overrides_dir / "fighter_identity.yaml")
    df = link(con, read_csv(path), overrides)
    if not df.empty:
        con.register("wm_df", df)
        con.execute(
            "INSERT INTO weight_misses SELECT card_date, fighter_name, card_name, record_type, "
            "pounds_over, notes, source_url, fighter_id, event_id, bout_id, opp_id, link "
            "FROM wm_df"
        )
        con.unregister("wm_df")
    unlinked = int(df["link"].isna().sum()) if not df.empty else 0
    if unlinked:
        names = ", ".join(df.loc[df["link"].isna(), "fighter_name"].head(10))
        log.warning("%d weight misses not linked to a fighter: %s", unlinked, names)
    return {"weight_misses": len(df), "weight_misses_unlinked": unlinked}
