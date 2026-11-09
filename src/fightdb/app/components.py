"""Shared page furniture: password gate, freshness banner, formatting."""

from __future__ import annotations

import hmac
import math
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
import streamlit as st

from fightdb import errorlog
from fightdb.app import data
from fightdb.config import get_settings
from fightdb.features.stat_meta import BY_KEY

CM_PER_IN = 2.54


def page(title: str, icon: str = "🥊") -> None:
    st.set_page_config(page_title=f"{title} · Fight Analyser", page_icon=icon, layout="wide")
    # Streamlit logs uncaught page exceptions on a non-propagating logger; catch them there too.
    errorlog.install("app", "streamlit.error_util")
    errorlog.bind(page=title, query=dict(st.query_params))
    _password_gate()
    if not data.db_ready():
        st.title(title)
        st.warning("No database yet. Run `make backfill` (or the GitHub Actions refresh) first.")
        st.stop()
    _freshness_sidebar()


def _password_gate() -> None:
    password = get_settings().app_password
    if not password or st.session_state.get("auth_ok"):
        return
    st.title("Fight Analyser")
    entered = st.text_input("Password", type="password")
    if entered and hmac.compare_digest(entered, password):
        st.session_state["auth_ok"] = True
        st.rerun()
    elif entered:
        st.error("Wrong password")
    st.stop()


def _freshness_sidebar() -> None:
    run = data.last_run()
    with st.sidebar:
        if run is None:
            st.caption("No refresh recorded yet.")
            return
        finished = run["finished_at"]
        age_h = (datetime.now(UTC).replace(tzinfo=None) - finished).total_seconds() / 3600
        st.caption(f"Last refreshed {fmt_date(finished, with_time=True)} UTC ({age_h:.0f} h ago)")
        st.caption(f"History source: {run['source']} {run['source_version'] or ''}")
        if age_h > get_settings().stale_after_hours:
            st.warning(
                f"Data is {age_h:.0f} hours old. The daily refresh may be failing; see Status."
            )


def fmt_date(value: Any, *, with_time: bool = False) -> str:
    """Format dates for display without changing their timezone or precision."""
    if value is None or pd.isna(value):
        return "—"
    if not isinstance(value, date):
        value = pd.Timestamp(value)
    day = value.day
    suffix = "th" if 10 <= day % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    result = f"{day}{suffix} {value:%B %Y}"
    if with_time:
        result += (
            f", {value.hour % 12 or 12}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"
        )
    return result


def format_dates(
    frame: pd.DataFrame, *, dates: tuple[str, ...] = (), timestamps: tuple[str, ...] = ()
) -> pd.DataFrame:
    """Format selected display columns, leaving the original frame intact."""
    result = frame.copy()
    for column in dates:
        if column in result:
            result[column] = result[column].map(fmt_date)
    for column in timestamps:
        if column in result:
            result[column] = result[column].map(lambda value: fmt_date(value, with_time=True))
    return result


def fmt_stat(key: str, value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    s = BY_KEY.get(key)
    if s is None:
        return f"{value:.2f}" if isinstance(value, float) else str(value)
    if s.unit == "cm":
        units = get_settings().units
        inches = value / CM_PER_IN
        if units == "imperial":
            return f"{inches:.1f} in"
        if units == "both":
            return f"{value:.0f} cm ({inches:.1f} in)"
    try:
        return s.fmt.format(value)
    except ValueError, TypeError:
        return str(value)


def fmt_diff_cm(cm: float) -> str:
    if cm is None or (isinstance(cm, float) and math.isnan(cm)):
        return "—"
    return f"{cm:+.1f} cm / {cm / CM_PER_IN:+.1f} in"


def format_american_odds(value: Any) -> str:
    if value is None or pd.isna(value) or value == 0:
        return "—"
    return f"{int(value):+d}"


def decimal_odds_from_american(value: Any) -> float | None:
    if value is None or pd.isna(value) or value == 0:
        return None
    return 1 + (value / 100 if value > 0 else 100 / abs(value))


def format_closing_range(low: Any, high: Any) -> str:
    values = sorted({int(x) for x in (low, high) if x is not None and not pd.isna(x) and x != 0})
    if not values:
        return "—"
    american = " to ".join(format_american_odds(value) for value in values)
    decimal = " to ".join(f"{decimal_odds_from_american(value):.2f}" for value in values)
    return f"{decimal} decimal ({american} American)"


def level_vs_baseline(p: float, base: float) -> str:
    """'low / medium / high' relative to the division baseline (DECISIONS.md D26)."""
    if p is None or base is None or base <= 0 or math.isnan(p) or math.isnan(base):
        return "—"
    r = p / base
    if r >= 1.5:
        return "high"
    if r >= 1.15:
        return "medium-high"
    if r > 0.85:
        return "average"
    if r > 0.67:
        return "medium-low"
    return "low"
