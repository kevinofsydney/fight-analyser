"""Fighter profile: bio, UFC history, and how their numbers evolved."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import altair as alt
import pandas as pd
import streamlit as st

from fightdb.app import components, data
from fightdb.features.snapshots import _wear_by_day
from fightdb.features.stat_meta import BY_KEY

components.page("Fighter", "👤")
st.title("Fighter")

fighters = data.query(
    """SELECT f.fighter_id, f.name, count(*) AS fights, max(s.event_date) AS last
       FROM fighters f JOIN bout_sides s ON s.fighter_id = f.fighter_id
       GROUP BY ALL ORDER BY last DESC"""
)
ids = fighters["fighter_id"].tolist()
labels = dict(
    zip(
        fighters["fighter_id"],
        fighters["name"] + " (" + fighters["fights"].astype(str) + ")",
        strict=True,
    )
)
fid = st.selectbox("Fighter", ids, format_func=labels.get, index=None, placeholder="Type a name…")
if not fid:
    st.stop()

bio = data.query("SELECT * FROM fighters WHERE fighter_id = ?", [fid]).iloc[0]
hist = data.query("SELECT * FROM bout_sides WHERE fighter_id = ? ORDER BY event_date", [fid])
latest = hist.iloc[-1]

st.header(bio["name"] + (f' "{bio["nickname"]}"' if bio["nickname"] else ""))
c = st.columns(5)
c[0].metric(
    "Pro record (current)",
    f"{bio['pro_wins'] or 0:.0f}-{bio['pro_losses'] or 0:.0f}-{bio['pro_draws'] or 0:.0f}"
    if bio["pro_wins"] == bio["pro_wins"]
    else "—",
)
c[1].metric("Height", components.fmt_stat("height_cm", bio["height_cm"]))
c[2].metric("Reach", components.fmt_stat("reach_cm", bio["reach_cm"]))
c[3].metric("Stance", bio["stance"] or "—")
c[4].metric("Elo now", f"{latest['self_elo']:.0f}")

# Build a current profile from completed fights, including the most recent fight.
wear_input = data.query(
    """SELECT s.fighter_id, s.bout_id, s.event_date AS date, s.status, s.result,
              b.method_group, b.end_round, b.fight_seconds,
              count(r.round) AS opp_rounds_recorded, count(r.sig_l) AS opp_sig_rounds,
              count(r.kd) AS opp_kd_rounds, sum(r.sig_l) AS opp_sig_l, sum(r.kd) AS opp_kd
       FROM bout_sides s JOIN bouts b USING (bout_id)
       LEFT JOIN round_stats r ON r.bout_id = s.bout_id AND r.fighter_id = s.opp_id
       WHERE s.fighter_id = ? AND s.status = 'completed'
       GROUP BY ALL""",
    [fid],
)
st.subheader("Fight wear (UFC history)")
if wear_input.empty or wear_input["result"].isna().all():
    st.caption("No completed UFC fights with a recorded result.")
else:
    wear_input["date"] = pd.to_datetime(wear_input["date"])
    current = _wear_by_day(wear_input).sort_values("date").iloc[-1]
    cols = st.columns(5)
    for col, label, key in zip(
        cols,
        [
            "Strikes absorbed",
            "Knockdowns absorbed",
            "Fights: 50+ strikes",
            "Fights: 75+ strikes",
            "Fights: 100+ strikes",
        ],
        [
            "career_sig_absorbed",
            "career_kd_absorbed",
            "wear_fights_50",
            "wear_fights_75",
            "wear_fights_100",
        ],
        strict=True,
    ):
        col.metric(label, "—" if pd.isna(current[key]) else f"{current[key]:.0f}")
    recent = current["recent3_sig_absorbed"]
    st.caption(
        f"Last 3 UFC fights: {'—' if pd.isna(recent) else f'{recent:.0f}'} significant strikes absorbed. Complete strike stats: {int(current['wear_stats_fights'])} of {len(wear_input)} fights."
    )
    st.caption(
        "These are recorded UFC exposures, not a medical diagnosis. Missing round statistics make totals unavailable."
    )

st.subheader("UFC fights")
done = hist[hist["status"] == "completed"]
fight_rows = done.sort_values("event_date", ascending=False).reset_index(drop=True)
rounds_complete = (
    wear_input["end_round"].notna()
    & (wear_input["opp_rounds_recorded"] == wear_input["end_round"])
    & (wear_input["opp_sig_rounds"] == wear_input["end_round"])
)
absorption = wear_input[["bout_id", "opp_sig_l"]].copy()
absorption["opp_sig_l"] = absorption["opp_sig_l"].where(rounds_complete)
fight_rows = fight_rows.merge(absorption, on="bout_id", how="left")
shown = components.format_dates(
    fight_rows[
        [
            "event_date",
            "event_name",
            "opp_name",
            "result",
            "method",
            "opp_sig_l",
            "end_round",
            "self_elo",
            "self_p_win",
        ]
    ],
    dates=("event_date",),
).rename(
    columns={
        "opp_sig_l": "Sig. strikes absorbed",
        "self_elo": "Elo before",
        "self_p_win": "Model win % (out-of-sample)",
    }
)
selection = st.dataframe(
    shown,
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
)
if selection.selection.rows:
    st.session_state["bout_id"] = fight_rows.iloc[selection.selection.rows[0]]["bout_id"]
    st.switch_page("pages/1_Bout.py")
st.caption("Select a fight to review its statistics.")

st.subheader("Trends (as of each fight)")
options = [
    "elo",
    "slpm_s",
    "sapm_s",
    "td_def_s",
    "td_acc_s",
    "sig_acc_s",
    "idx_str_off",
    "idx_wrest_off",
    "idx_durability",
]
picked = st.multiselect("Stats", options, default=["elo"], format_func=lambda k: BY_KEY[k].label)
if picked:
    long = hist.melt(
        id_vars=["event_date"],
        value_vars=[f"self_{k}" for k in picked],
        var_name="stat",
        value_name="value",
    )
    long["stat"] = long["stat"].str.removeprefix("self_").map(lambda k: BY_KEY[k].label)
    st.altair_chart(
        alt.Chart(long)
        .mark_line(point=True)
        .encode(x="event_date:T", y=alt.Y("value:Q", scale=alt.Scale(zero=False)), color="stat:N")
        .facet(row="stat:N")
        .resolve_scale(y="independent"),
        width="stretch",
    )
