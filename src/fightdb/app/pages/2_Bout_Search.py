"""Bout search: find any completed UFC bout and open it on the Bout page."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fightdb.app.freshness import reload_changed_modules

reload_changed_modules()

import pandas as pd
import streamlit as st

from fightdb.app import components, data

components.page("Bout search", "🔎")
st.title("Bout search")

METHODS = {"ko_tko": "KO/TKO", "sub": "Submission", "dec": "Decision"}
LIMIT = 500

span = data.query(
    "SELECT min(event_date) AS first, max(event_date) AS last, list(DISTINCT division) AS divs "
    "FROM bout_sides WHERE status = 'completed'"
).iloc[0]
divisions = sorted(d for d in span["divs"] if d)

t1, t2, t3 = st.columns(3)
fighter_text = t1.text_input("Fighter", placeholder="e.g. Gane")
opponent_text = t2.text_input("Opponent", placeholder="optional, e.g. Jones")
event_text = t3.text_input("Event", placeholder="e.g. UFC 285")
c1, c2, c3 = st.columns([2, 2, 1])
dates = c1.date_input(
    "Date range",
    value=(span["first"], span["last"]),
    min_value=span["first"],
    max_value=span["last"],
    format="DD/MM/YYYY",
)
picked_divisions = c2.multiselect(
    "Division", divisions, format_func=lambda d: d.replace("_", " ").title()
)
picked_methods = c3.multiselect("Method", list(METHODS), format_func=METHODS.get)
title_only = st.checkbox("Title fights only")

# Each bout appears from both sides; a fighter search keeps the side of the fighter searched for.
where = ["status = 'completed'"]
params: list[object] = []
for column, value in (
    ("fighter_name", fighter_text),
    ("opp_name", opponent_text),
    ("event_name", event_text),
):
    if value.strip():
        where.append(f"{column} ILIKE ?")
        params.append(f"%{value.strip()}%")
if isinstance(dates, tuple) and len(dates) == 2:
    where.append("event_date BETWEEN ? AND ?")
    params += [dates[0], dates[1]]
if picked_divisions:
    where.append(f"division IN ({', '.join('?' * len(picked_divisions))})")
    params += picked_divisions
if picked_methods:
    where.append(f"method_group IN ({', '.join('?' * len(picked_methods))})")
    params += picked_methods
if title_only:
    where.append("is_title")

found = data.query(
    f"""SELECT *, count(*) OVER () AS total FROM (
            SELECT bout_id, event_date, event_name, fighter_name, opp_name, won, lost, draw,
                   no_contest, method, method_group, end_round, division, is_title, card_order
            FROM bout_sides WHERE {" AND ".join(where)}
            QUALIFY row_number() OVER (PARTITION BY bout_id ORDER BY is_a DESC) = 1
        )
        ORDER BY event_date DESC, card_order LIMIT {LIMIT}""",
    params,
)
if found.empty:
    st.info("No bouts match these filters.")
    st.stop()

total = int(found["total"].iloc[0])
winner = (
    found["fighter_name"]
    .where(found["won"] == 1)
    .fillna(found["opp_name"].where(found["lost"] == 1))
    .fillna(found["draw"].map({1.0: "Draw"}))
    .fillna(found["no_contest"].map({1.0: "No contest"}))
    .fillna("—")
)
shown = pd.DataFrame(
    {
        "Date": found["event_date"].map(components.fmt_date),
        "Event": found["event_name"],
        "Fighter": found["fighter_name"],
        "Opponent": found["opp_name"],
        "Winner": winner,
        "Method": found["method"].fillna(found["method_group"].map(METHODS)).fillna("—"),
        "Round": found["end_round"].map(lambda r: "—" if pd.isna(r) else f"{r:.0f}"),
        "Division": found["division"].fillna("").str.replace("_", " ").str.title(),
        "Title": found["is_title"].map({True: "Title"}).fillna(""),
    }
)
st.caption(
    f"{total:,} bouts match"
    + (
        f"; showing the {LIMIT} most recent. Narrow the filters to see others."
        if total > LIMIT
        else "."
    )
)
selection = st.dataframe(
    shown,
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
    key="search_results",
)
if selection.selection.rows:
    st.session_state["bout_id"] = found.iloc[selection.selection.rows[0]]["bout_id"]
    for key in ("bout_event_selector", "bout_selector"):
        st.session_state.pop(key, None)
    st.switch_page("pages/1_Bout.py")
st.caption("Select a bout to open it on the Bout page.")
