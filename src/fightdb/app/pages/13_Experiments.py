"""Experiments: every idea tested on the models, what happened and whether it shipped."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fightdb.app.freshness import reload_changed_modules

reload_changed_modules()

import streamlit as st

from fightdb.app import components
from fightdb.app.experiments import EXPERIMENTS, Experiment

components.page("Experiments")
st.title("Experiments")
st.markdown(
    "Every idea we've tested on the prediction models, including the ones that didn't work. "
    "Each idea is tested the same way: the model is retrained with and without it, every past "
    "year is predicted using only earlier years, and the idea ships only if accuracy improves "
    "by more than chance and the probabilities stay honest (calibrated). For how the model is "
    "doing now, see **Model performance**."
)
st.caption(
    "Log loss measures how good the predicted chances are; lower is better. The win model "
    "scores about 0.637 over 2012–2026 and the bookmakers' closing line about 0.606. A change of "
    "0.001 is small; the biggest gain so far was 0.015. Full tables are in docs/NEXT_TASKS.md."
)

BADGES = {
    "shipped": (":green-badge[Shipped]"),
    "no gain": (":gray-badge[No gain]"),
    "harmful": (":red-badge[Made things worse]"),
    "not tested": (":orange-badge[Not tested, by choice]"),
}


def show(e: Experiment) -> None:
    with st.container(border=True):
        st.markdown(f"**{e.title}** {BADGES[e.verdict]}  \n:small[{components.fmt_date(e.when)}]")
        st.markdown(f"**The idea:** {e.idea}")
        st.markdown(f"**What we found:** {e.found}")
        st.markdown(f":small[**Numbers:** {e.numbers}]")
        if e.lesson:
            st.markdown(f"**Takeaway:** {e.lesson}")


failed = [e for e in EXPERIMENTS if e.verdict != "shipped"]
shipped = [e for e in EXPERIMENTS if e.verdict == "shipped"]
tabs = st.tabs([f"Didn't help ({len(failed)})", f"Shipped ({len(shipped)})"])
with tabs[0]:
    st.markdown(
        "Mostly the same story: an idea that sounds right (momentum, peak age, ring rust) turns "
        "out to be something the model or the bookmakers already account for through fighter "
        "quality. The gains came from new information, such as records outside the UFC."
    )
    for e in sorted(failed, key=lambda x: x.when, reverse=True):
        show(e)
with tabs[1]:
    for e in sorted(shipped, key=lambda x: x.when, reverse=True):
        show(e)
