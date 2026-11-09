"""Every hypothesis YAML in hypotheses/, with its latest result and upcoming matches."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fightdb.app.freshness import reload_changed_modules

reload_changed_modules()

import logging

import altair as alt
import duckdb
import numpy as np
import pandas as pd
import streamlit as st
from pydantic import ValidationError
from sklearn.linear_model import LogisticRegression

from fightdb.analytics import hypothesis, schema, stats
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


def group_observations(observations: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Choose one eligible fighter per bout and summarise observed outcomes in ranges."""
    fights = (
        observations.sort_values(
            ["feature_value", "fighter_id"], ascending=[False, True], kind="stable"
        )
        .drop_duplicates("bout_id")
        .copy()
    )
    values = fights["feature_value"]
    if values.nunique() == 1:
        fights["range_id"] = 0
    else:
        fights["range_id"] = pd.qcut(
            values, q=min(8, values.nunique()), labels=False, duplicates="drop"
        ).astype(int)
    rows = []
    for range_id, group in fights.groupby("range_id", sort=True):
        n = len(group)
        successes = int(group["outcome_value"].sum())
        low, high = stats.wilson_ci(successes, n)
        minimum, maximum = group["feature_value"].min(), group["feature_value"].max()
        rows.append(
            {
                "range_id": int(range_id),
                "range": f"{minimum:.1f}–{maximum:.1f}" if minimum != maximum else f"{minimum:.1f}",
                "midpoint": group["feature_value"].mean(),
                "rate": successes / n,
                "count": n,
                "successes": successes,
                "ci_low": low,
                "ci_high": high,
            }
        )
    return fights, pd.DataFrame(rows)


def outcome_plot(
    groups: pd.DataFrame,
    feature_label: str,
    outcome_label: str,
    baseline: float,
    fights: pd.DataFrame,
    show_trend: bool,
) -> alt.LayerChart:
    """Show grouped observed outcomes and Wilson intervals; select a range to inspect fights."""
    selection = alt.selection_point(name="fight_range", fields=["range_id"], on="click")
    x = alt.X("midpoint:Q", title=feature_label, scale=alt.Scale(zero=False))
    tooltip = [
        alt.Tooltip("range:N", title="Range"),
        alt.Tooltip("rate:Q", title=outcome_label, format=".1%"),
        alt.Tooltip("successes:Q", title="Successful outcomes"),
        alt.Tooltip("count:Q", title="Fights"),
        alt.Tooltip("ci_low:Q", title="95% interval lower", format=".1%"),
        alt.Tooltip("ci_high:Q", title="95% interval upper", format=".1%"),
    ]
    base = alt.Chart(groups).encode(x=x)
    intervals = base.mark_rule().encode(
        y=alt.Y(
            "ci_low:Q",
            title=f"Observed rate: {outcome_label}",
            scale=alt.Scale(domain=[0, 1]),
            axis=alt.Axis(format="%"),
        ),
        y2="ci_high:Q",
        tooltip=tooltip,
    )
    points = (
        base.mark_circle(size=100)
        .encode(
            y=alt.Y("rate:Q", scale=alt.Scale(domain=[0, 1])),
            tooltip=tooltip,
            opacity=alt.condition(selection, alt.value(1), alt.value(0.35)),
        )
        .add_params(selection)
    )
    reference = (
        alt.Chart(pd.DataFrame({"baseline": [baseline]}))
        .mark_rule(color="#888888", strokeDash=[5, 4])
        .encode(y="baseline:Q")
    )
    layers = [reference, intervals, points]
    x_values = fights["feature_value"].to_numpy(dtype=float)
    y_values = fights["outcome_value"].to_numpy(dtype=int)
    if show_trend and len(np.unique(x_values)) > 1 and len(np.unique(y_values)) > 1:
        mean, scale = x_values.mean(), x_values.std()
        model = LogisticRegression().fit(((x_values - mean) / scale).reshape(-1, 1), y_values)
        grid = np.linspace(x_values.min(), x_values.max(), 100)
        fit = pd.DataFrame(
            {
                "midpoint": grid,
                "probability": model.predict_proba(((grid - mean) / scale).reshape(-1, 1))[:, 1],
            }
        )
        layers.append(
            alt.Chart(fit)
            .mark_line(color="#ff4b4b")
            .encode(
                x=x,
                y=alt.Y("probability:Q", scale=alt.Scale(domain=[0, 1])),
                tooltip=[
                    alt.Tooltip("midpoint:Q", title=feature_label, format=".2f"),
                    alt.Tooltip("probability:Q", title="Fitted probability", format=".1%"),
                ],
            )
        )
    return alt.layer(*layers)


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
            fights, groups = group_observations(observations)
            st.caption(
                f"{len(fights):,} fights with known values. Each dot shows the observed outcome "
                "rate for a range, with a 95% Wilson interval; the dashed line is the hypothesis "
                "baseline. Select a dot to see its fights. Ranges have roughly equal numbers of fights."
            )
            st.caption(
                "One eligible fighter is shown per fight: if both qualify, the fighter with the "
                "higher horizontal-axis value is selected (ties are resolved consistently). "
                "This is descriptive, not a model prediction or evidence of causation. Intervals "
                "do not account for fighters appearing in multiple bouts."
            )
            show_trend = st.checkbox(
                "Show fitted trend",
                key=f"show_trend_{index}",
                help="A descriptive one-variable logistic fit, not the app's prediction model.",
            )
            if show_trend:
                st.caption(
                    "The red curve is a descriptive logistic fit, not the app's prediction model."
                )
            selected = st.altair_chart(
                outcome_plot(
                    groups,
                    plot_feature_label(feature),
                    OUTCOMES[h.outcome],
                    r.baseline,
                    fights,
                    show_trend,
                ),
                width="stretch",
                on_select="rerun",
                selection_mode="fight_range",
                key=f"outcome_chart_{index}",
            )
            chosen = selected.selection.get("fight_range", [])
            if chosen:
                range_id = chosen[0]["range_id"]
                selected_fights = fights.loc[fights["range_id"] == range_id].sort_values(
                    "event_date", ascending=False
                )
                st.markdown("**Fights in selected range**")
                st.caption(f"{len(selected_fights):,} fights; select a row to open its bout.")
                shown_range = components.format_dates(
                    selected_fights[
                        [
                            "event_date",
                            "event_name",
                            "fighter_name",
                            "opp_name",
                            "result",
                            "method",
                            "feature_value",
                        ]
                    ],
                    dates=("event_date",),
                ).rename(
                    columns={
                        "event_date": "Date",
                        "event_name": "Event",
                        "fighter_name": "Fighter",
                        "opp_name": "Opponent",
                        "result": "Result",
                        "method": "Method",
                        "feature_value": plot_feature_label(feature),
                    }
                )
                picked = st.dataframe(
                    shown_range,
                    hide_index=True,
                    width="stretch",
                    on_select="rerun",
                    selection_mode="single-row",
                    key=f"range_fights_{index}_{range_id}",
                )
                if picked.selection.rows:
                    st.session_state["bout_id"] = selected_fights.iloc[picked.selection.rows[0]][
                        "bout_id"
                    ]
                    st.switch_page("pages/1_Bout.py")
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
