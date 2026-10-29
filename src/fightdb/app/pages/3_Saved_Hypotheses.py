"""Every hypothesis YAML in hypotheses/, with its latest result and upcoming matches."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

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
        r = hypothesis.evaluate(con, h, keep_bouts=False)
        up = hypothesis.matches_upcoming(con, h)
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
for h, r, up in details:
    with st.expander(h.title):
        if h.description:
            st.write(h.description)
        st.write(r.verdict)
        for w in r.warnings:
            st.caption(f"⚠️ {w}")
        if not up.empty:
            st.dataframe(
                up[["event_name", "event_date", "fighter_name", "opp_name"]],
                hide_index=True,
                width="stretch",
            )
        st.code(schema.to_yaml(h), language="yaml")
