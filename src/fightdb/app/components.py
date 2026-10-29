"""Shared page furniture: password gate, freshness banner, formatting."""

from __future__ import annotations

import hmac
import math
from datetime import UTC, datetime
from typing import Any

import streamlit as st

from fightdb.app import data
from fightdb.config import get_settings
from fightdb.features.stat_meta import BY_KEY

CM_PER_IN = 2.54


def page(title: str, icon: str = "🥊") -> None:
    st.set_page_config(page_title=f"{title} · Fight Analyser", page_icon=icon, layout="wide")
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
        st.caption(f"Last refreshed {finished:%Y-%m-%d %H:%M} UTC ({age_h:.0f} h ago)")
        st.caption(f"History source: {run['source']} {run['source_version'] or ''}")
        if age_h > get_settings().stale_after_hours:
            st.warning(
                f"Data is {age_h:.0f} hours old. The daily refresh may be failing; see Status."
            )


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
    except (ValueError, TypeError):
        return str(value)


def fmt_diff_cm(cm: float) -> str:
    if cm is None or (isinstance(cm, float) and math.isnan(cm)):
        return "—"
    return f"{cm:+.1f} cm / {cm / CM_PER_IN:+.1f} in"


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
