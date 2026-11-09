"""Every hypothesis YAML in hypotheses/, with its latest result and upcoming matches."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import logging

import duckdb
import pandas as pd
import streamlit as st
from pydantic import ValidationError

from fightdb.analytics import hypothesis, schema
from fightdb.app import components, data
from fightdb.config import get_settings

components.page("Saved hypotheses", "📚")
st.title("Saved hypotheses")
directory = get_settings().hypotheses_dir
st.caption(
    f"Loaded from `{directory}/`. Add a YAML file there (the Hypothesis Lab writes one for you) and commit it."
)

paths = sorted(Path(directory).rglob("*.yaml"))
if not paths:
    st.info("No hypotheses saved yet.")
    st.stop()

con = data.connect()
rows, details = [], []
try:
    for p in paths:
        try:
            h = schema.load_hypothesis(p)
        except (ValidationError, ValueError) as exc:
            st.error(f"{p.name}: {exc}")
            continue
        try:
            r = hypothesis.evaluate(con, h, keep_bouts=False)
            up = hypothesis.matches_upcoming(con, h)
        except duckdb.Error as exc:  # one bad hypothesis must not break the page
            logging.getLogger(__name__).warning("hypothesis %s failed: %s", p.name, exc)
            st.warning(f"{p.name}: can't be evaluated against this database ({exc}).")
            continue
        rows.append(
            {
                "Hypothesis": h.title,
                "n": r.n,
                "Rate": r.rate,
                "Baseline": r.baseline,
                "Diff (pp)": r.diff_pp,
                "p-value": r.p_value,
                "Upcoming matches": len(up),
                "File": p.name,
            }
        )
        details.append((h, r, up))
finally:
    con.close()

st.dataframe(
    pd.DataFrame(rows).style.format(
        {"Rate": "{:.1%}", "Baseline": "{:.1%}", "Diff (pp)": "{:+.1f}", "p-value": "{:.3f}"}
    ),
    hide_index=True,
    width="stretch",
)
for index, (h, r, up) in enumerate(details):
    with st.expander(h.title, key=f"hypothesis_{index}", on_change="rerun") as panel:
        if not panel.open:
            continue
        if h.description:
            st.write(h.description)
        st.write(r.verdict)
        for w in r.warnings:
            st.caption(f"⚠️ {w}")
        if not up.empty:
            st.dataframe(
                components.format_dates(
                    up[["event_name", "event_date", "fighter_name", "opp_name"]],
                    dates=("event_date",),
                ),
                hide_index=True,
                width="stretch",
            )
        st.markdown("**Previous matching fights**")
        st.caption(
            "Each row is the fighter who matched the condition; the result is from their perspective. "
            "Select a row to open the fight."
        )
        if not r.n:
            st.caption("No previous fights match this hypothesis.")
        else:
            page_count = (r.n + 99) // 100
            page = int(
                st.number_input(
                    "Page",
                    min_value=1,
                    max_value=page_count,
                    value=1,
                    key=f"history_page_{index}",
                    disabled=page_count == 1,
                    help=f"Showing up to 100 rows per page, newest first; {r.n:,} matching fighter-sides.",
                )
            )
            con = data.connect()
            try:
                previous = hypothesis.matching_bout_sides(
                    con, h, limit=100, offset=(page - 1) * 100
                )
            finally:
                con.close()
            shown = components.format_dates(
                previous.drop(columns=["bout_id"]), dates=("event_date",)
            ).rename(
                columns={
                    "event_date": "Date",
                    "event_name": "Event",
                    "fighter_name": "Matching fighter",
                    "opp_name": "Opponent",
                    "result": "Result",
                    "method": "Method",
                }
            )
            selection = st.dataframe(
                shown,
                hide_index=True,
                width="stretch",
                on_select="rerun",
                selection_mode="single-row",
                key=f"history_fights_{index}_{page}",
            )
            if selection.selection.rows:
                st.session_state["bout_id"] = previous.iloc[selection.selection.rows[0]]["bout_id"]
                st.switch_page("pages/1_Bout.py")
        st.code(schema.to_yaml(h), language="yaml")
