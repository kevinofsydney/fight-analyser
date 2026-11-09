"""Matchup: the model's projection for any two fighters, as they stand today."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fightdb.app.freshness import reload_changed_modules

reload_changed_modules()

import math

import duckdb
import pandas as pd
import streamlit as st

from fightdb.app import components, data, explain
from fightdb.features import bout_sides as bs

components.page("Matchup", "⚖️")
st.title("Matchup")
st.caption(
    "Pick any two fighters to see who the win model favours and why, using each fighter's "
    "ratings as of the last daily refresh."
)

try:
    fighters = data.query(
        """SELECT c.fighter_id, f.name, c.division, c.ufc_fights, last.last_fight
           FROM fighter_current c
           JOIN fighters f USING (fighter_id)
           JOIN (SELECT fighter_id, max(event_date) AS last_fight FROM bout_sides
                 WHERE status = 'completed' GROUP BY fighter_id) last USING (fighter_id)
           ORDER BY last.last_fight DESC, f.name"""
    )
except duckdb.Error:
    fighters = pd.DataFrame()
win_model = explain.load_model(None)
if fighters.empty or win_model is None:
    st.info("Matchups become available after the next daily refresh.")
    st.stop()

info = fighters.set_index("fighter_id")
label = {
    fid: f"{r['name']} · {str(r['division'] or '').replace('_', ' ').title()}"
    for fid, r in info.iterrows()
}
ids = fighters["fighter_id"].tolist()
c1, c2 = st.columns(2)
a_id = c1.selectbox(
    "Fighter", ids, format_func=label.get, index=None, placeholder="Type a name…", key="mu_a"
)
b_id = c2.selectbox(
    "Opponent", ids, format_func=label.get, index=None, placeholder="Type a name…", key="mu_b"
)
if not a_id or not b_id:
    st.stop()
if a_id == b_id:
    st.warning("Pick two different fighters.")
    st.stop()

current = data.query("SELECT * FROM fighter_current WHERE fighter_id IN (?, ?)", [a_id, b_id])
sides = bs.hypothetical_sides(current, a_id, b_id)
preds = win_model.predict(sides).set_index("fighter_id")
a_name, b_name = str(info.loc[a_id, "name"]), str(info.loc[b_id, "name"])

today = pd.Timestamp.today().normalize()
warnings = []
div_a, div_b = info.loc[a_id, "division"], info.loc[b_id, "division"]
if div_a != div_b:
    warnings.append(
        f"Different divisions ({str(div_a).replace('_', ' ')} vs {str(div_b).replace('_', ' ')}). "
        "The model learned from same-division fights and has no weight or size input beyond "
        "reach, so treat this as a rough guide."
    )
for fid, name in ((a_id, a_name), (b_id, b_name)):
    last = pd.Timestamp(info.loc[fid, "last_fight"])
    years = (today - last).days / 365.25
    if years >= 2:
        warnings.append(
            f"{name} last fought in the UFC on {components.fmt_date(last)} ({years:.0f} years "
            "ago). Ratings are as of that fight; age is as of today."
        )
    if float(info.loc[fid, "ufc_fights"]) < 3:
        warnings.append(
            f"{name} has {int(info.loc[fid, 'ufc_fights'])} UFC fights: ratings are mostly the "
            "division average and noisy."
        )
for w in warnings:
    st.warning(w)

cols = st.columns(2)
for col, fid, name in ((cols[0], a_id, a_name), (cols[1], b_id, b_name)):
    p = preds.loc[fid]
    with col:
        st.metric(name, f"{p['p_win']:.0%} to win")
        st.dataframe(
            pd.DataFrame(
                {
                    "Method": ["Wins by KO/TKO", "Wins by Submission", "Wins by Decision"],
                    "Probability": [
                        f"{p[c]:.0%}" if not math.isnan(p[c]) else "—"
                        for c in ("p_win_ko", "p_win_sub", "p_win_dec")
                    ],
                }
            ),
            hide_index=True,
            width="stretch",
        )
st.caption(
    f"Chance the fight goes the distance: {preds.loc[a_id, 'p_distance']:.0%}. "
    "A hypothetical fight on neutral terms; not betting advice."
)

st.subheader("Why the model favours " + (a_name if preds.loc[a_id, "p_win"] >= 0.5 else b_name))
explain.render_win_breakdown(sides.iloc[0], win_model, a_name, b_name)
