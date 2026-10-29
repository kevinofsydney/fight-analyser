"""Fighter profile: bio, UFC history, and how their numbers evolved."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import altair as alt
import streamlit as st

from fightdb.app import components, data
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

st.subheader("UFC fights")
done = hist[hist["status"] == "completed"]
st.dataframe(
    done[
        [
            "event_date",
            "event_name",
            "opp_name",
            "result",
            "method",
            "end_round",
            "self_elo",
            "self_p_win",
        ]
    ]
    .sort_values("event_date", ascending=False)
    .rename(columns={"self_elo": "Elo before", "self_p_win": "Model win % (out-of-sample)"}),
    hide_index=True,
    width="stretch",
)

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
