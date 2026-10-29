"""Read-only data access for the Streamlit app.

Each query opens a short-lived read-only connection, so the daily refresh can swap the database
file underneath a running app. On free hosts, the database is downloaded from DATA_RELEASE_URL.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pandas as pd
import streamlit as st

from fightdb.config import get_settings

TTL = 600


def db_path() -> Path:
    s = get_settings()
    path = Path(s.db_path)
    if s.data_release_url:
        _ensure_download(s.data_release_url, path, s.db_max_age_hours)
    return path


@st.cache_resource(ttl=TTL, show_spinner="Downloading the latest database…")
def _ensure_download(url: str, path: Path, max_age_hours: int) -> float:
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < max_age_hours * 3600
    if not fresh:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".download")
        with httpx.stream("GET", url, follow_redirects=True, timeout=300) as resp:
            resp.raise_for_status()
            with tmp.open("wb") as fh:
                for chunk in resp.iter_bytes(1 << 20):
                    fh.write(chunk)
        tmp.replace(path)
    return path.stat().st_mtime


def _mtime() -> float:
    p = db_path()
    return p.stat().st_mtime if p.exists() else 0.0


def connect() -> duckdb.DuckDBPyConnection:
    """Read-only, with file and network access disabled so the SQL console cannot read
    arbitrary files on the server (e.g. `.env`) or call out to the internet."""
    return duckdb.connect(
        str(db_path()),
        read_only=True,
        config={"enable_external_access": False, "lock_configuration": True},
    )


@st.cache_data(ttl=TTL, show_spinner=False)
def _query(sql: str, params: tuple[Any, ...], mtime: float) -> pd.DataFrame:
    con = connect()
    try:
        return con.execute(sql, list(params)).df()
    finally:
        con.close()


def query(sql: str, params: list[Any] | tuple[Any, ...] = ()) -> pd.DataFrame:
    return _query(sql, tuple(params), _mtime())


def db_ready() -> bool:
    p = db_path()
    if not p.exists():
        return False
    try:
        return bool(
            query(
                "SELECT count(*) AS n FROM information_schema.tables WHERE table_name = 'bout_sides'"
            )["n"][0]
        )
    except duckdb.Error:
        return False


def upcoming_events() -> pd.DataFrame:
    return query(
        """SELECT e.event_id, e.name, e.date, e.location, count(b.bout_id) AS bouts
           FROM events e LEFT JOIN bouts b ON b.event_id = e.event_id AND b.status = 'scheduled'
           WHERE e.status = 'upcoming'
           GROUP BY ALL ORDER BY e.date"""
    )


def event_card(event_id: str) -> pd.DataFrame:
    return query(
        """SELECT bout_id, fighter_id, fighter_name, opp_id, opp_name, weight_class, division,
                  scheduled_rounds, card_order, self_p_win, self_elo, opp_elo, self_ufc_fights, opp_ufc_fights,
                  diff_age_years, diff_reach_cm
           FROM bout_sides WHERE event_id = ? AND is_a ORDER BY card_order""",
        [event_id],
    )


def bout_sides(bout_id: str) -> pd.DataFrame:
    return query("SELECT * FROM bout_sides WHERE bout_id = ? ORDER BY is_a DESC", [bout_id])


def recent_changes(days: int = 7) -> pd.DataFrame:
    return query(
        """SELECT c.detected_at, e.name AS event, c.change_type, c.summary
           FROM card_changes c LEFT JOIN events e USING (event_id)
           WHERE c.detected_at >= now()::TIMESTAMP - to_days(?)
           ORDER BY c.detected_at DESC""",
        [days],
    )


def last_run() -> pd.Series | None:
    df = query(
        "SELECT * FROM scrape_runs WHERE status <> 'failed' ORDER BY finished_at DESC LIMIT 1"
    )
    return None if df.empty else df.iloc[0]
