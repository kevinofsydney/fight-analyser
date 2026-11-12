"""The owner's own pre-fight picks, and how they scored against the model and the bookmakers.

Picks live in their own small SQLite file (`PICKS_PATH`), not the main database: the app opens
that read-only and the daily refresh replaces the whole file.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from fightdb.sources.identity import name_key, same_matchup

_SCHEMA = """
CREATE TABLE IF NOT EXISTS picks (
    bout_id         TEXT PRIMARY KEY,
    event_name      TEXT,
    event_date      TEXT NOT NULL,
    fighter_a_id    TEXT NOT NULL,
    fighter_a_name  TEXT NOT NULL,
    fighter_b_id    TEXT NOT NULL,
    fighter_b_name  TEXT NOT NULL,
    pick_id         TEXT NOT NULL,
    p_pick          REAL NOT NULL,
    model_p_pick    REAL,
    market_p_pick   REAL,
    saved_at        TEXT NOT NULL
)
"""
COLUMNS = [
    "bout_id",
    "event_name",
    "event_date",
    "fighter_a_id",
    "fighter_a_name",
    "fighter_b_id",
    "fighter_b_name",
    "pick_id",
    "p_pick",
    "model_p_pick",
    "market_p_pick",
    "saved_at",
]
# A scheduled bout gets a new ID once its result arrives from the history source, and events
# occasionally move by a day, so results are matched on the two fighters and a nearby date.
MATCH_DAYS = 3
# Keeps one confident miss from costing an infinite log loss.
P_CLIP = 0.01


class PickLockedError(ValueError):
    """The event date has passed: the pick can no longer be added, changed or removed."""


class Pick(BaseModel):
    bout_id: str
    event_name: str | None = None
    event_date: date
    fighter_a_id: str
    fighter_a_name: str
    fighter_b_id: str
    fighter_b_name: str
    pick_id: str
    # The chance given to the picked fighter. Below 50% would be a pick for the other side.
    p_pick: float = Field(ge=0.5, le=0.99)
    # What the model and the bookmakers gave the picked fighter when the pick was saved.
    model_p_pick: float | None = None
    market_p_pick: float | None = None


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(_SCHEMA)
    return con


def _check_open(event_date: date, today: date) -> None:
    if event_date < today:
        raise PickLockedError(f"picks for {event_date} are locked")


def save(path: Path, pick: Pick, today: date | None = None, now: datetime | None = None) -> None:
    today = today or datetime.now(UTC).date()
    _check_open(pick.event_date, today)
    if pick.pick_id not in (pick.fighter_a_id, pick.fighter_b_id):
        raise ValueError("the pick must be one of the two fighters")
    row = pick.model_dump()
    row["event_date"] = pick.event_date.isoformat()
    row["saved_at"] = (now or datetime.now(UTC)).replace(tzinfo=None).isoformat(timespec="seconds")
    updates = ", ".join(f"{c} = excluded.{c}" for c in COLUMNS if c != "bout_id")
    with closing(_connect(path)) as con, con:
        stored = con.execute(
            "SELECT event_date FROM picks WHERE bout_id = ?", [pick.bout_id]
        ).fetchone()
        if stored:
            _check_open(date.fromisoformat(stored[0]), today)
        con.execute(
            f"""INSERT INTO picks ({", ".join(COLUMNS)})
                VALUES ({", ".join("?" * len(COLUMNS))})
                ON CONFLICT (bout_id) DO UPDATE SET {updates}""",
            [row[c] for c in COLUMNS],
        )


def delete(path: Path, bout_id: str, today: date | None = None) -> None:
    if not path.exists():
        return
    today = today or datetime.now(UTC).date()
    with closing(_connect(path)) as con, con:
        stored = con.execute("SELECT event_date FROM picks WHERE bout_id = ?", [bout_id]).fetchone()
        if stored:
            _check_open(date.fromisoformat(stored[0]), today)
            con.execute("DELETE FROM picks WHERE bout_id = ?", [bout_id])


def load(path: Path) -> pd.DataFrame:
    """Every saved pick, newest event first. A missing file is simply no picks."""
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    with closing(_connect(path)) as con:
        rows = con.execute(
            f"SELECT {', '.join(COLUMNS)} FROM picks ORDER BY event_date DESC, saved_at DESC"
        ).fetchall()
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["event_date"] = pd.to_datetime(df["event_date"])
    df["saved_at"] = pd.to_datetime(df["saved_at"])
    for c in ("p_pick", "model_p_pick", "market_p_pick"):
        df[c] = df[c].astype(float)
    return df


SCORED_COLUMNS = [
    *COLUMNS,
    "pick_name",
    "result_bout_id",
    "outcome",
    "y",
    "model_p",
    "market_p",
    "method",
]


def _pick_is_listed(c: Any, p: Any) -> bool | None:
    """Is the picked fighter the one the result row is written for? None: a different bout."""
    ids = {c.fighter_id, c.opp_id}
    if ids == {p.fighter_a_id, p.fighter_b_id}:
        return bool(c.fighter_id == p.pick_id)
    if not same_matchup(c.fighter_name, c.opp_name, p.fighter_a_name, p.fighter_b_name):
        return None
    # A debutant's ID changes when the history source first lists them, so fall back on
    # whichever ID survived, then on names.
    picked_a = p.pick_id == p.fighter_a_id
    other_id = p.fighter_b_id if picked_a else p.fighter_a_id
    if p.pick_id in ids:
        return bool(c.fighter_id == p.pick_id)
    if other_id in ids:
        return bool(c.opp_id == other_id)
    pick_key = name_key(p.fighter_a_name if picked_a else p.fighter_b_name)
    other_key = name_key(p.fighter_b_name if picked_a else p.fighter_a_name)
    listed, opp = name_key(c.fighter_name), name_key(c.opp_name)
    if listed == pick_key or opp == other_key:
        return True
    if opp == pick_key or listed == other_key:
        return False
    return None


def score(picks: pd.DataFrame, completed: pd.DataFrame) -> pd.DataFrame:
    """Attach results to picks.

    `completed` is one row per finished bout (the A side of `bout_sides`): bout_id, event_date,
    fighter_id, fighter_name, opp_id, opp_name, won, lost, draw, no_contest, method,
    self_implied_prob.

    Adds `outcome` ('won' | 'lost' | 'draw' | 'nc' | 'pending'), `y` (1 / 0, NaN unless a fighter
    won), `model_p` (the model's chance for the picked fighter when the pick was saved) and
    `market_p` (the closing line for the picked fighter, else the price when the pick was saved).
    """
    out = picks.copy()
    out["pick_name"] = np.where(
        out["pick_id"] == out["fighter_a_id"], out["fighter_a_name"], out["fighter_b_name"]
    )
    results = []
    dates = pd.to_datetime(completed["event_date"])
    for p in out.itertuples(index=False):
        near = completed[(dates - p.event_date).abs() <= pd.Timedelta(days=MATCH_DAYS)]
        found = None
        for c in near.itertuples(index=False):
            pick_is_listed = _pick_is_listed(c, p)
            if pick_is_listed is not None:
                found = (c, pick_is_listed)
                break
        if found is None:
            results.append((None, "pending", np.nan, np.nan, None))
            continue
        c, pick_is_listed = found
        win, loss = (c.won, c.lost) if pick_is_listed else (c.lost, c.won)
        if win == 1:
            outcome, y = "won", 1.0
        elif loss == 1:
            outcome, y = "lost", 0.0
        elif c.draw == 1:
            outcome, y = "draw", np.nan
        else:
            outcome, y = "nc", np.nan
        closing_p = c.self_implied_prob if pick_is_listed else 1 - c.self_implied_prob
        results.append((c.bout_id, outcome, y, closing_p, c.method))
    res = pd.DataFrame(
        results, columns=["result_bout_id", "outcome", "y", "closing_p", "method"], index=out.index
    )
    out = pd.concat([out, res], axis=1)
    out["model_p"] = out["model_p_pick"]
    out["market_p"] = out["closing_p"].astype(float).fillna(out["market_p_pick"])
    return out[SCORED_COLUMNS]


def _metrics(p: np.ndarray, y: np.ndarray) -> dict[str, float]:
    clipped = np.clip(p, P_CLIP, 1 - P_CLIP)
    return {
        "n": float(len(y)),
        "accuracy": float(np.mean((p > 0.5) == (y == 1))),
        "log_loss": float(-np.mean(y * np.log(clipped) + (1 - y) * np.log(1 - clipped))),
        "brier": float(np.mean((p - y) ** 2)),
    }


def summarise(scored: pd.DataFrame) -> pd.DataFrame:
    """You, the model and the bookmakers on decided picks. Each rival is compared on the picks
    it has a number for, with your own score on those same picks beside it."""
    decided = scored[scored["y"].notna()]
    rows = []
    if not decided.empty:
        y = decided["y"].to_numpy(dtype=float)
        rows.append(
            {"who": "You", "on": "all your picks", **_metrics(decided["p_pick"].to_numpy(), y)}
        )
    for label, col in (("Model", "model_p"), ("Bookmakers", "market_p")):
        sub = decided[decided[col].notna()]
        if sub.empty:
            continue
        y = sub["y"].to_numpy(dtype=float)
        rows.append({"who": label, "on": "picks it priced", **_metrics(sub[col].to_numpy(), y)})
        if len(sub) < len(decided):
            rows.append(
                {
                    "who": "You",
                    "on": f"same picks as {label.lower()}",
                    **_metrics(sub["p_pick"].to_numpy(), y),
                }
            )
    return pd.DataFrame(rows, columns=["who", "on", "n", "accuracy", "log_loss", "brier"])
