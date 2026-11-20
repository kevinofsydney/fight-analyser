"""Experiments: every idea tested on the models, what happened and whether it shipped."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fightdb.app.freshness import reload_changed_modules

reload_changed_modules()

import altair as alt
import pandas as pd
import streamlit as st

from fightdb.app import components, explain
from fightdb.app.experiments import EXPERIMENTS, Experiment

components.page("Experiments")
st.title("Experiments")

shipped = [e for e in EXPERIMENTS if e.verdict == "shipped"]
failed = [e for e in EXPERIMENTS if e.verdict != "shipped"]
c = st.columns(3)
c[0].metric("Ideas tested", len(EXPERIMENTS))
c[1].metric("Shipped", len(shipped))
c[2].metric(
    "Winners picked",
    "64.0%",
    help="Out of sample, 2012–2026. It was 60.9% before the shipped changes; the bookmakers' "
    "favourite wins 66.8%.",
)
st.markdown(
    "Each idea is retrained into the model and tested on years it never saw. It ships only if "
    "it improves accuracy by more than chance and keeps the probabilities honest. Most ideas "
    "that sounded right turned out to be things the model or the bookmakers already knew; the "
    "gains came from new information, like records outside the UFC."
)

measured = [e for e in EXPERIMENTS if e.effect is not None]
if measured:
    effects = pd.DataFrame(
        {
            "Idea": [e.title for e in measured],
            "Change": [e.effect[0] for e in measured if e.effect],
            "Low": [e.effect[1] for e in measured if e.effect],
            "High": [e.effect[2] for e in measured if e.effect],
            "Result": ["Shipped" if e.verdict == "shipped" else "No gain" for e in measured],
        }
    ).sort_values("Change")
    y = alt.Y("Idea:N", sort=list(effects["Idea"]), title=None, axis=alt.Axis(labelLimit=360))
    color = alt.Color(
        "Result:N",
        scale=alt.Scale(domain=["Shipped", "No gain"], range=[explain.SIDE_COLORS[0], "#8b929c"]),
        legend=None,
    )
    tooltip = [
        "Idea:N",
        alt.Tooltip("Change:Q", format="+.4f"),
        alt.Tooltip("Low:Q", title="95% low", format="+.4f"),
        alt.Tooltip("High:Q", title="95% high", format="+.4f"),
    ]
    chart = (
        alt.Chart(pd.DataFrame({"x": [0.0]})).mark_rule(color="#8b929c").encode(x="x:Q")
        + alt.Chart(effects)
        .mark_rule(strokeWidth=2)
        .encode(x="Low:Q", x2="High:Q", y=y, color=color)
        + alt.Chart(effects)
        .mark_point(filled=True, size=70)
        .encode(
            x=alt.X("Change:Q", title="Change in log loss (left is better)"),
            y=y,
            color=color,
            tooltip=tooltip,
        )
    )
    st.altair_chart(chart.properties(height=alt.Step(34)), width="stretch")
    st.caption(
        "The ideas measured with an uncertainty range: each line is the 95% range, and a range "
        "that crosses zero means no real change."
    )

SHIPPED_ORDER = [
    "All-promotion rating and pro record",
    "How fighters win and lose across every promotion (method model)",
    "Rating from share of each fight won",
    "Stronger opponent adjustment",
]
TAGS = {
    "shipped": ":green-badge[Shipped]",
    "no gain": ":gray-badge[No gain]",
    "harmful": ":red-badge[Made it worse]",
    "not tested": ":orange-badge[Not tested]",
}


def row(e: Experiment) -> None:
    with st.expander(f"{e.title}  {TAGS[e.verdict]}  {e.headline}"):
        st.markdown(f"**Idea.** {e.idea}")
        st.markdown(f"**Result.** {e.found}")
        if e.lesson:
            st.markdown(f"**Takeaway.** {e.lesson}")
        st.caption(f"{e.numbers} Tested {components.fmt_date(e.when)}.")


show = st.segmented_control(
    "Show", ["All", "Didn't help", "Shipped"], default="All", label_visibility="collapsed"
)
ordered = sorted(
    shipped, key=lambda e: SHIPPED_ORDER.index(e.title) if e.title in SHIPPED_ORDER else 99
)
if show in ("All", "Shipped", None):
    for e in ordered:
        row(e)
if show in ("All", "Didn't help", None):
    for e in failed:
        row(e)
