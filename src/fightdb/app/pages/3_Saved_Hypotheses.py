"""Every hypothesis YAML in hypotheses/, with its latest result and upcoming matches."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import logging

import altair as alt
import duckdb
import numpy as np
import pandas as pd
import streamlit as st
from pydantic import ValidationError
from sklearn.linear_model import LogisticRegression

from fightdb.analytics import hypothesis, schema
from fightdb.app import components, data
from fightdb.config import get_settings
from fightdb.features.stat_meta import BY_KEY, OUTCOMES

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
available = set(
    data.query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'bout_sides'"
    )["column_name"]
)


def plot_feature_label(feature: str) -> str:
    for prefix, label in (
        ("diff_", "Difference: "),
        ("self_", "Fighter: "),
        ("opp_", "Opponent: "),
    ):
        if feature.startswith(prefix):
            key = feature.removeprefix(prefix)
            stat = BY_KEY.get(key)
            if stat:
                return f"{label}{stat.label} ({stat.unit})"
    return feature.replace("_", " ").capitalize()


def outcome_plot(
    observations: pd.DataFrame, feature_label: str, outcome_label: str
) -> alt.LayerChart:
    """Show binary observed outcomes and a descriptive, one-variable logistic fit."""
    dots = observations.copy()
    jitter = (
        pd.util.hash_pandas_object(dots[["bout_id", "fighter_id"]], index=False) % 1000
    ) / 1000
    dots["display_outcome"] = np.where(
        dots["outcome_value"] == 1, 0.94 + 0.06 * jitter, 0.06 * jitter
    )
    dots["event_display"] = dots["event_date"].map(components.fmt_date)
    dots["outcome_display"] = np.where(dots["outcome_value"] == 1, "Yes", "No")
    plot = (
        alt.Chart(dots)
        .mark_circle(size=30, opacity=0.3)
        .encode(
            x=alt.X("feature_value:Q", title=feature_label),
            y=alt.Y(
                "display_outcome:Q",
                title=f"Observed outcome / fitted probability: {outcome_label}",
                scale=alt.Scale(domain=[0, 1]),
                axis=alt.Axis(format="%"),
            ),
            tooltip=[
                alt.Tooltip("event_display:N", title="Date"),
                alt.Tooltip("event_name:N", title="Event"),
                alt.Tooltip("fighter_name:N", title="Fighter"),
                alt.Tooltip("opp_name:N", title="Opponent"),
                alt.Tooltip("result:N", title="Fighter result"),
                alt.Tooltip("method:N", title="Method"),
                alt.Tooltip("feature_value:Q", title=feature_label, format=".2f"),
                alt.Tooltip("outcome_display:N", title=outcome_label),
            ],
        )
    )
    x = dots["feature_value"].to_numpy(dtype=float)
    y = dots["outcome_value"].to_numpy(dtype=int)
    if len(np.unique(x)) < 2 or len(np.unique(y)) < 2:
        return alt.layer(plot)
    mean, scale = x.mean(), x.std()
    model = LogisticRegression().fit(((x - mean) / scale).reshape(-1, 1), y)
    grid = np.linspace(x.min(), x.max(), 100)
    fit = pd.DataFrame(
        {
            "feature_value": grid,
            "probability": model.predict_proba(((grid - mean) / scale).reshape(-1, 1))[:, 1],
        }
    )
    line = (
        alt.Chart(fit)
        .mark_line(color="#ff4b4b", strokeWidth=3)
        .encode(
            x=alt.X("feature_value:Q", title=feature_label),
            y=alt.Y("probability:Q", scale=alt.Scale(domain=[0, 1])),
            tooltip=[
                alt.Tooltip("feature_value:Q", title=feature_label, format=".2f"),
                alt.Tooltip("probability:Q", title="Fitted probability", format=".1%"),
            ],
        )
    )
    return alt.layer(plot, line)


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
        st.markdown("**Explore historical outcomes**")
        candidates = list(
            dict.fromkeys(
                [c.feature for c in h.condition]
                + ["diff_height_cm", "diff_reach_cm", "diff_age_years", "self_days_since_last"]
                + sorted(schema.allowed_features())
            )
        )
        candidates = [column for column in candidates if column in available]
        chart_cols = st.columns(2)
        feature = chart_cols[0].selectbox(
            "Horizontal axis",
            candidates,
            format_func=plot_feature_label,
            key=f"plot_feature_{index}",
        )
        scope = chart_cols[1].radio(
            "Fights shown",
            ["Matching fights", "All eligible fights"],
            horizontal=True,
            key=f"plot_scope_{index}",
            help="All eligible fights uses the hypothesis population filters without its conditions.",
        )
        con = data.connect()
        try:
            observations = hypothesis.exploration_bout_sides(
                con, h, feature, matched_only=scope == "Matching fights"
            )
        finally:
            con.close()
        if observations.empty:
            st.caption("No fights have the data needed for this plot.")
        else:
            st.caption(
                f"{len(observations):,} fighter-sides with known values. Each dot represents an actual outcome "
                "(0% or 100%), spread slightly for visibility. The red line is a descriptive one-variable "
                "logistic fit, not the app's fight prediction or evidence of causation. Both fighters may appear for the same fight."
            )
            if (
                observations["feature_value"].nunique() < 2
                or observations["outcome_value"].nunique() < 2
            ):
                st.caption(
                    "No fitted line: at least two distinct values and both outcomes are needed."
                )
            st.altair_chart(
                outcome_plot(observations, plot_feature_label(feature), OUTCOMES[h.outcome]),
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
