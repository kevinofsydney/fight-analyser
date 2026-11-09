"""Show how the win model turned the gaps between two fighters into a win probability."""

from __future__ import annotations

import math

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

from fightdb.app import data
from fightdb.features.stat_meta import BY_KEY
from fightdb.models import outcome

PROJECTION_LABELS = {
    "x_sig_r": "Projected significant strikes landed / round",
    "x_td_r": "Projected takedowns / round",
    "x_kd_r": "Projected knockdowns / round",
    "x_ctrl_s_r": "Projected control seconds / round",
}
SIDE_COLORS = ["#3987e5", "#d95926"]


def load_model(model_year: int | None) -> outcome.FittedModels | None:
    """The final model (None) or a walk-forward year's model; None if not built yet."""
    try:
        if model_year is None:
            w = data.query("SELECT * FROM model_weights WHERE model_year IS NULL")
        else:
            w = data.query("SELECT * FROM model_weights WHERE model_year = ?", [model_year])
    except duckdb.Error:
        return None
    return None if w.empty else outcome.from_weights(w)


def model_year_for(status: str, year: int) -> int | None:
    """Which saved model produced a bout's probability: completed bouts were scored out of sample
    by the model trained on earlier years only."""
    return int(year) if status == "completed" else None


def factor_label(feature: str) -> str:
    key = feature.removeprefix("diff_")
    return BY_KEY[key].label if key in BY_KEY else PROJECTION_LABELS.get(key, key)


def _fmt(key: str, value: float, signed: bool = False) -> str:
    if value is None or math.isnan(value):
        return "—"
    decimals = 2 if key.startswith("x_") else 0 if key in BY_KEY and ".0f" in BY_KEY[key].fmt else 1
    text = f"{value:+.{decimals}f}" if signed else f"{value:.{decimals}f}"
    unit = BY_KEY[key].unit if key in BY_KEY else ""
    return f"{text} {unit}" if unit in ("years", "cm") else text


def render_win_breakdown(
    side: pd.Series, model: outcome.FittedModels, self_name: str, opp_name: str
) -> None:
    """Factor table, push chart and the arithmetic, from `side`'s point of view."""
    pushes = outcome.win_contributions(model, side.to_frame().T)
    total = float(pushes["push"].sum())
    p_win = 1 / (1 + math.exp(-total))

    st.markdown(
        f"Each factor compares **{self_name}** with **{opp_name}** (gap = {self_name} minus "
        f"{opp_name}). The model divides each gap by how big that gap typically is between UFC "
        "opponents, then multiplies by the factor's **weight**, which it learned from past "
        f"fights. The result is the factor's **push**: positive favours {self_name}, negative "
        f"favours {opp_name}. The pushes are added up and the total is turned into a win "
        "chance with 1 ÷ (1 + e^−total)."
    )
    st.markdown(
        f"**Total push {total:+.2f} → 1 ÷ (1 + e^{-total:.2f}) = {p_win:.0%}** for {self_name}"
        f" ({1 - p_win:.0%} for {opp_name})."
    )

    rows = []
    for r in pushes.itertuples():
        key = r.feature.removeprefix("diff_")
        rows.append(
            {
                "Factor": factor_label(r.feature),
                self_name: _fmt(key, float(side.get(f"self_{key}", math.nan))),
                opp_name: _fmt(key, float(side.get(f"opp_{key}", math.nan))),
                "Gap": _fmt(key, r.gap, signed=True),
                "Typical gap": _fmt(key, r.typical_gap),
                "Weight": r.weight,
                "Push": r.push,
                f"{self_name} win % on this alone": 1 / (1 + math.exp(-r.push)),
                "Favours": "—" if abs(r.push) < 0.005 else self_name if r.push > 0 else opp_name,
                "_abs": abs(r.push),
            }
        )
    table = pd.DataFrame(rows).sort_values("_abs", ascending=False)

    chart_rows = table[table["Favours"] != "—"]
    if not chart_rows.empty:
        st.altair_chart(
            alt.Chart(chart_rows)
            .mark_bar(cornerRadiusEnd=4, height=14)
            .encode(
                x=alt.X("Push:Q", title=f"Push (← {opp_name} · {self_name} →)"),
                y=alt.Y(
                    "Factor:N",
                    sort=list(chart_rows["Factor"]),
                    title=None,
                    axis=alt.Axis(labelLimit=320),
                ),
                color=alt.Color(
                    "Favours:N",
                    scale=alt.Scale(domain=[self_name, opp_name], range=SIDE_COLORS),
                    legend=alt.Legend(orient="top", title="Favours"),
                ),
                tooltip=[
                    "Factor:N",
                    alt.Tooltip(f"{self_name}:N"),
                    alt.Tooltip(f"{opp_name}:N"),
                    "Gap:N",
                    alt.Tooltip("Weight:Q", format="+.2f"),
                    alt.Tooltip("Push:Q", format="+.2f"),
                ],
            )
            .properties(height=max(120, 26 * len(chart_rows))),
            width="stretch",
        )
    st.dataframe(
        table.drop(columns="_abs"),
        hide_index=True,
        width="stretch",
        column_config={
            "Weight": st.column_config.NumberColumn(
                format="%+.2f",
                help="How much one typical gap moves the total, learned from past fights. "
                "Negative means a bigger value for this fighter lowers their chance.",
            ),
            "Push": st.column_config.NumberColumn(
                format="%+.2f", help="Weight × gap ÷ typical gap. The pushes add up to the total."
            ),
            f"{self_name} win % on this alone": st.column_config.NumberColumn(
                format="percent", help="The win chance if this were the only factor."
            ),
            "Typical gap": st.column_config.TextColumn(
                help="How far apart UFC opponents usually are on this factor (one standard "
                "deviation of the gap in the training fights)."
            ),
        },
    )
    st.caption(
        "Missing values count as no gap, as in training. Index values are relative to the "
        "division average (100); projected numbers are per round against this opponent. "
        "Not betting advice."
    )
