"""Refresh history, data freshness, unmatched fighters and model calibration."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import json

import altair as alt
import pandas as pd
import streamlit as st

from fightdb.app import components, data

components.page("Status", "🩺")
st.title("Status")

runs = data.query(
    "SELECT started_at, finished_at, kind, status, source, source_version, max_event_date, error, counts "
    "FROM scrape_runs ORDER BY started_at DESC LIMIT 30"
)
st.subheader("Refresh runs")
st.dataframe(runs.drop(columns=["counts"]), hide_index=True, width="stretch")

if not runs.empty and runs.iloc[0]["counts"]:
    last = json.loads(runs.iloc[0]["counts"])
    unmatched = last.pop("unmatched", [])
    changes = last.pop("card_changes", [])
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Last run row counts")
        st.json(last)
    with c2:
        st.subheader("Unmatched upcoming fighters")
        st.caption(
            "Usually UFC debutants. If a veteran shows up here, add them to data/overrides/fighter_identity.yaml."
        )
        st.write(unmatched or "None")
        if changes:
            st.subheader("Card changes in last run")
            st.write(changes)

st.subheader("Table sizes")
tables = data.query("SELECT table_name FROM information_schema.tables ORDER BY 1")[
    "table_name"
].tolist()
sizes = pd.DataFrame(
    [
        {"table": t, "rows": int(data.query(f'SELECT count(*) AS n FROM "{t}"')["n"][0])}
        for t in tables
    ]
)
st.dataframe(sizes, hide_index=True)

st.subheader("Win model calibration (walk-forward: trained on earlier years, tested on each year)")
ev = data.query("SELECT * FROM model_evals ORDER BY test_year")
if ev.empty:
    st.caption("No model evaluations yet.")
else:
    tot = ev["n"].sum()
    w = lambda c: (ev[c] * ev["n"]).sum() / tot  # noqa: E731
    n_mk = ev["n_market"].sum()
    wm = (ev["brier_market"] * ev["n_market"]).sum() / n_mk if n_mk else float("nan")
    wmm = (ev["brier_model_on_market_rows"] * ev["n_market"]).sum() / n_mk if n_mk else float("nan")
    m = st.columns(4)
    m[0].metric("Brier score (lower is better)", f"{w('brier'):.4f}", help="Coin flip = 0.2500")
    m[1].metric("Accuracy", f"{w('accuracy'):.1%}")
    m[2].metric("Model on bouts with odds", f"{wmm:.4f}" if n_mk else "—")
    m[3].metric(
        "Closing betting line",
        f"{wm:.4f}" if n_mk else "—",
        help="The market is the benchmark to beat",
    )
    if n_mk and wmm > wm:
        st.caption(
            "Honest read: the model beats a coin flip but not the closing betting line. Treat it as a "
            "structured summary of the stats, not as an edge over the market."
        )
    elif n_mk:
        st.caption(
            "The model matched or beat the closing line on these years. Be sceptical and check for leakage."
        )
    st.dataframe(
        ev.drop(columns=["reliability", "created_at"], errors="ignore"),
        hide_index=True,
        width="stretch",
    )
    rel = pd.concat(
        [
            pd.DataFrame(json.loads(r)).assign(year=y)
            for y, r in zip(ev["test_year"], ev["reliability"], strict=True)
        ]
    )
    rel = rel.groupby("bin", as_index=False).apply(
        lambda g: pd.Series(
            {
                "mean_pred": (g.mean_pred * g.n).sum() / g.n.sum(),
                "mean_obs": (g.mean_obs * g.n).sum() / g.n.sum(),
                "n": g.n.sum(),
            }
        ),
        include_groups=False,
    )
    diag = (
        alt.Chart(pd.DataFrame({"x": [0, 1], "y": [0, 1]}))
        .mark_line(strokeDash=[4, 4], color="gray")
        .encode(x="x", y="y")
    )
    pts = (
        alt.Chart(rel)
        .mark_circle()
        .encode(
            x=alt.X(
                "mean_pred:Q", title="Predicted win probability", scale=alt.Scale(domain=[0, 1])
            ),
            y=alt.Y("mean_obs:Q", title="Observed win rate", scale=alt.Scale(domain=[0, 1])),
            size="n:Q",
            tooltip=["mean_pred", "mean_obs", "n"],
        )
    )
    st.altair_chart(diag + pts, width="stretch")
    st.caption(
        "Reliability chart: points on the dashed line mean the probabilities are well calibrated."
    )

priors = data.query("SELECT * FROM shrinkage_priors")
with st.expander("Shrinkage prior strengths"):
    st.caption(
        "k = how many attempts (or minutes) of division-average performance each fighter starts with."
    )
    st.dataframe(priors, hide_index=True)
