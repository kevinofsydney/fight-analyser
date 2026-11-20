"""Shared page furniture: password gate, freshness banner, formatting."""

from __future__ import annotations

import hmac
import html
import math
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitPageNotFoundError

from fightdb import errorlog
from fightdb.app import data
from fightdb.config import get_settings
from fightdb.features.stat_meta import BY_KEY

CM_PER_IN = 2.54


def page(title: str, icon: str | None = None) -> None:
    """Page setup. `icon` is ignored: every page shares one tab icon, and nothing decorative is
    shown in the page itself."""
    st.set_page_config(page_title=f"{title} · Fight Analyser", page_icon="🥊", layout="wide")
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
        st.caption(f"Data updated {fmt_date(finished, with_time=True)} UTC")
        if age_h > get_settings().stale_after_hours:
            st.warning(
                f"Data is {age_h:.0f} hours old. The daily refresh may be failing; see Status."
            )


def footer(betting: bool = False) -> None:
    """The page's one disclaimer line."""
    st.divider()
    st.caption(
        "Model numbers are estimates for analysis, not betting advice."
        if betting
        else "Model numbers are estimates for analysis."
    )


def fmt_date(value: Any, *, with_time: bool = False) -> str:
    """Format dates for display without changing their timezone or precision."""
    if value is None or pd.isna(value):
        return "—"
    if not isinstance(value, date):
        value = pd.Timestamp(value)
    result = f"{value.day} {value:%b %Y}"
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


def espn_id_for(fighter_id: str, source_ids: Any) -> str | None:
    """The fighter's ESPN athlete ID from their ESPN alias IDs, or None unless exactly one fits
    (an ambiguous ID could show someone else's photo)."""
    ids = sorted({str(v) for v in source_ids if str(v).isascii() and str(v).isdigit()})
    if not ids and fighter_id.startswith("espn:"):
        ids = [fighter_id.removeprefix("espn:")]
    return ids[0] if len(ids) == 1 else None


def fighter_portrait(fighter_name: str, espn_id: str | None) -> str:
    """ESPN portrait with an initials placeholder when an ID or image is unavailable."""
    initials = "".join(part[0] for part in fighter_name.split()[:2]).upper() or "?"
    initials = html.escape(initials)
    if not espn_id or not espn_id.isascii() or not espn_id.isdigit():
        image = ""
    else:
        url = f"https://a.espncdn.com/i/headshots/mma/players/full/{espn_id}.png"
        image = (
            f'<div style="position:absolute;inset:0;background:url({url}) center bottom / '
            'contain no-repeat" aria-hidden="true"></div>'
        )
    label = html.escape(f"{fighter_name} portrait, if available", quote=True)
    return (
        f'<div role="img" aria-label="{label}" style="position:relative;height:210px;'
        "overflow:hidden;border-radius:10px;background:linear-gradient(145deg,#262a35,#14171e);"
        'display:flex;align-items:center;justify-content:center;">'
        f'<span style="font-size:4rem;font-weight:700;color:#69717f;">{initials}</span>'
        f"{image}</div>"
    )


def pro_record(wins: Any, losses: Any, draws: Any) -> str:
    values = (wins, losses, draws)
    if any(value is None or pd.isna(value) for value in values):
        return "—"
    return "–".join(str(int(value)) for value in values)


def odds_range(prices: pd.Series) -> str:
    values = pd.to_numeric(prices, errors="coerce").dropna()
    if values.empty:
        return "—"
    low, high = float(values.min()), float(values.max())
    return f"{low:.2f}" if low == high else f"{low:.2f}–{high:.2f}"


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


FIGHTER_PAGE = "pages/8_Fighter.py"
BOUT_PAGE = "pages/1_Bout.py"
NAV_HINT = "Click a fighter's name to open their page, or any other cell to open the bout."


def open_fighter(fighter_id: Any) -> None:
    """Go to the Fighter page in this session (a plain link would start a new session and ask
    for the password again)."""
    st.switch_page(FIGHTER_PAGE, query_params={"fighter": str(fighter_id)})


def open_bout(bout_id: Any) -> None:
    st.session_state["bout_id"] = bout_id
    for key in ("bout_event_selector", "bout_selector"):
        st.session_state.pop(key, None)
    st.switch_page(BOUT_PAGE)


def fighter_link(name: Any, fighter_id: Any, *, container: Any = st) -> None:
    if fighter_id is None or pd.isna(fighter_id):
        container.markdown(f"**{name}**")
        return
    page_link(container, FIGHTER_PAGE, str(name), query_params={"fighter": str(fighter_id)})


def page_link(
    container: Any, page: str, label: str, query_params: dict[str, str] | None = None
) -> None:
    """`st.page_link`, or plain bold text when the page can't be resolved: paths are relative
    to the entrypoint, so they only resolve when the app runs from Home.py (not in page tests)."""
    try:
        container.page_link(page, label=label, query_params=query_params)
    except StreamlitPageNotFoundError:
        container.markdown(f"**{label}**")


def nav_table(
    shown: pd.DataFrame,
    *,
    key: str,
    fighter_ids: dict[str, pd.Series] | None = None,
    bout_ids: pd.Series | None = None,
    **kwargs: Any,
) -> None:
    """A table where clicking a fighter-name cell opens that fighter and any other cell opens
    the bout. `fighter_ids` maps a column of `shown` to the IDs behind it, row for row."""
    fighter_ids = fighter_ids or {}
    kwargs.setdefault("hide_index", True)
    kwargs.setdefault("width", "stretch")
    event = st.dataframe(shown, key=key, on_select="rerun", selection_mode="single-cell", **kwargs)
    cells = event.selection.get("cells") or []
    if not cells:
        return
    row, col = cells[0]
    if col in fighter_ids:
        fid = fighter_ids[col].iloc[int(row)]
        if fid is not None and not pd.isna(fid):
            open_fighter(fid)
    elif bout_ids is not None:
        open_bout(bout_ids.iloc[int(row)])
