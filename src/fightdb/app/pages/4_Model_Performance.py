"""Model performance: how the win model has done on fights it never saw, and what drives it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fightdb.app.freshness import reload_changed_modules

reload_changed_modules()

import altair as alt
import duckdb
import numpy as np
import pandas as pd
import streamlit as st

from fightdb.app import components, data, explain
from fightdb.models import outcome

components.page("Model performance", "📈")
st.title("Model performance")
st.caption(
    "Every number here is out of sample: each fight from 2012 on was scored by a model trained "
    "only on fights from earlier years, the same way upcoming fights are scored now."
)
COLORS = explain.SIDE_COLORS

fights = data.query(
    """SELECT a.bout_id, a.year, a.self_p_win AS p, a.won, a.self_implied_prob AS market,
              a.method_group, a.self_p_win_ko AS a_ko, a.self_p_win_sub AS a_sub,
              a.self_p_win_dec AS a_dec, b.self_p_win_ko AS b_ko, b.self_p_win_sub AS b_sub,
              b.self_p_win_dec AS b_dec,
              o.close_low, o.close_high, ob.close_low AS opp_close_low,
              ob.close_high AS opp_close_high
       FROM bout_sides a
       JOIN bout_sides b ON b.bout_id = a.bout_id AND b.fighter_id = a.opp_id
       LEFT JOIN odds o ON o.bout_id = a.bout_id AND o.fighter_id = a.fighter_id
       LEFT JOIN odds ob ON ob.bout_id = a.bout_id AND ob.fighter_id = a.opp_id
       WHERE a.status = 'completed' AND a.is_a AND a.self_p_win IS NOT NULL
         AND a.won + a.lost = 1"""
)
if fights.empty:
    st.info("No scored fights yet. The model needs a daily refresh with enough history.")
    st.stop()

y = fights["won"].to_numpy(dtype=float)
p = fights["p"].to_numpy(dtype=float)
model_right = (p > 0.5) == (y == 1)
brier = float(np.mean((p - y) ** 2))
has_market = fights["market"].notna().to_numpy()
m = fights.loc[has_market, "market"].to_numpy(dtype=float)
market_right = (m > 0.5) == (y[has_market] == 1)

st.subheader("Headline")
c = st.columns(4)
c[0].metric(
    "Fights scored", f"{len(fights):,}", help=f"{fights['year'].min()}–{fights['year'].max()}"
)
c[1].metric("Model picked the winner", f"{model_right.mean():.1%}")
c[2].metric(
    "Bookmakers picked the winner",
    f"{market_right.mean():.1%}" if has_market.any() else "—",
    help="Closing-line favourite, on the fights that have closing odds.",
)
c[3].metric(
    "Brier score",
    f"{brier:.3f}",
    help="Average squared gap between the predicted chance and what happened (1 or 0). "
    "Lower is better; always saying 50% scores 0.250.",
)
if has_market.any():
    st.caption(
        f"On the {has_market.sum():,} fights with closing odds the model picked "
        f"{model_right[has_market].mean():.1%} of winners and the bookmakers "
        f"{market_right.mean():.1%}; Brier {np.mean((p[has_market] - y[has_market]) ** 2):.3f} "
        f"for the model vs {np.mean((m - y[has_market]) ** 2):.3f} for the bookmakers. "
        "The market is the stronger predictor."
    )

by_year = (
    pd.DataFrame({"year": fights["year"], "Model": model_right})
    .groupby("year")["Model"]
    .mean()
    .to_frame()
)
market_year = (
    pd.DataFrame({"year": fights.loc[has_market, "year"], "Bookmakers": market_right})
    .groupby("year")["Bookmakers"]
    .mean()
)
by_year = by_year.join(market_year).reset_index().melt("year", var_name="Who", value_name="acc")
st.altair_chart(
    alt.Chart(by_year.dropna(), title="Winners picked, by year")
    .mark_line(point=alt.OverlayMarkDef(size=50, filled=True), strokeWidth=2)
    .encode(
        x=alt.X("year:O", title=None),
        y=alt.Y("acc:Q", title=None, axis=alt.Axis(format="%"), scale=alt.Scale(zero=False)),
        color=alt.Color(
            "Who:N",
            scale=alt.Scale(domain=["Model", "Bookmakers"], range=COLORS),
            legend=alt.Legend(orient="top", title=None),
        ),
        tooltip=["year:O", "Who:N", alt.Tooltip("acc:Q", title="Winners picked", format=".1%")],
    )
    .properties(height=260),
    width="stretch",
)

st.subheader("Are the percentages honest?")
st.caption(
    "Calibration: of all the times the model said about 70%, did that fighter win about 70% of "
    "the time? Points on the dashed line mean yes. Both fighters of each bout are counted."
)
both_p = np.r_[p, 1 - p]
both_y = np.r_[y, 1 - y]
bins = np.clip((both_p * 10).astype(int), 0, 9)
calib = (
    pd.DataFrame({"bin": bins, "p": both_p, "y": both_y})
    .groupby("bin")
    .agg(predicted=("p", "mean"), actual=("y", "mean"), fights=("y", "size"))
    .reset_index()
)
calib = calib[calib["fights"] >= 20]
diag = pd.DataFrame({"predicted": [0, 1], "actual": [0, 1]})
st.altair_chart(
    (
        alt.Chart(diag)
        .mark_line(strokeDash=[4, 4], color="#888")
        .encode(x="predicted:Q", y="actual:Q")
        + alt.Chart(calib)
        .mark_line(point=alt.OverlayMarkDef(size=70, filled=True), color=COLORS[0], strokeWidth=2)
        .encode(
            x=alt.X("predicted:Q", title="Model's chance", axis=alt.Axis(format="%")),
            y=alt.Y("actual:Q", title="How often that fighter won", axis=alt.Axis(format="%")),
            tooltip=[
                alt.Tooltip("predicted:Q", title="Model said", format=".0%"),
                alt.Tooltip("actual:Q", title="Won", format=".0%"),
                alt.Tooltip("fights:Q", title="Fighters"),
            ],
        )
    ).properties(height=300),
    width="stretch",
)
fav = np.maximum(p, 1 - p)
buckets = pd.cut(fav, [0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 1.0], include_lowest=True)
conf = (
    pd.DataFrame({"Model's favourite had": buckets, "right": model_right})
    .groupby("Model's favourite had", observed=True)["right"]
    .agg(["size", "mean"])
    .reset_index()
)
conf["Model's favourite had"] = conf["Model's favourite had"].map(
    lambda i: f"{i.left:.0%}–{i.right:.0%}"
)
st.dataframe(
    conf.rename(columns={"size": "Fights", "mean": "Favourite won"}),
    hide_index=True,
    width="stretch",
    column_config={"Favourite won": st.column_config.NumberColumn(format="percent")},
)

outcome_cols = ["a_ko", "a_sub", "a_dec", "b_ko", "b_sub", "b_dec"]
scored = fights.dropna(subset=outcome_cols)
key = {"ko_tko": "ko", "sub": "sub", "dec": "dec"}
actual = np.where(scored["won"] == 1, "a_", "b_") + scored["method_group"].map(key).fillna("other")
predicted = scored[outcome_cols].idxmax(axis=1)
known = ~actual.str.endswith("other")
if known.any():
    exact = (predicted[known] == actual[known]).mean()
    common = actual[known].value_counts(normalize=True).iloc[0]
    st.caption(
        f"Who wins **and** how: the model's most likely of the six endings happened in "
        f"{exact:.1%} of fights (always guessing the most common ending would get {common:.1%})."
    )

st.subheader("Which inputs matter")
try:
    imp = data.query("SELECT * FROM feature_importance")
except duckdb.Error:
    imp = pd.DataFrame()
if imp.empty:
    st.info("Input importance appears after the next daily refresh.")
else:
    gain = float(imp["log_loss_none"].iloc[0] - imp["log_loss_all"].iloc[0])
    model = explain.load_model(None)
    weights = dict(zip(outcome.DIFF_FEATURES, model.win.coef_[0], strict=True)) if model else {}
    table = pd.DataFrame(
        {
            "Input": imp["feature"].map(explain.factor_label),
            "Lost if removed": (imp["log_loss_without"] - imp["log_loss_all"]) / gain,
            "On its own": (imp["log_loss_none"] - imp["log_loss_alone"]) / gain,
            "Accuracy on its own": imp["accuracy_alone"],
            "Weight now": imp["feature"].map(weights),
        }
    ).sort_values("Lost if removed", ascending=False)
    st.markdown(
        "Each input's value is measured the same out-of-sample way, as a share of the model's "
        "total improvement over a coin flip:\n"
        "- **Lost if removed**: how much of that improvement disappears when the model is "
        "retrained without this input. Near zero means other inputs already carry the same "
        "information (or it adds nothing); below zero means the model does slightly better "
        "without it.\n"
        "- **On its own**: how much a model using only this input achieves.\n"
        "- **Weight now**: the input's weight in today's model, per typical gap (see the Bout "
        "page breakdown). Positive means a bigger value than the opponent helps."
    )
    st.altair_chart(
        alt.Chart(table)
        .mark_bar(cornerRadiusEnd=4, height={"band": 0.7}, color=COLORS[0])
        .encode(
            x=alt.X(
                "Lost if removed:Q",
                title="Share of improvement lost if removed",
                axis=alt.Axis(format="%"),
            ),
            y=alt.Y(
                "Input:N",
                sort=list(table["Input"]),
                title=None,
                axis=alt.Axis(labelLimit=320, labelOverlap=False),
            ),
            tooltip=[
                "Input:N",
                alt.Tooltip("Lost if removed:Q", format=".1%"),
                alt.Tooltip("On its own:Q", format=".1%"),
                alt.Tooltip("Accuracy on its own:Q", format=".1%"),
            ],
        )
        .properties(height=alt.Step(28)),
        width="stretch",
    )
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_config={
            "Lost if removed": st.column_config.NumberColumn(format="percent"),
            "On its own": st.column_config.NumberColumn(format="percent"),
            "Accuracy on its own": st.column_config.NumberColumn(format="percent"),
            "Weight now": st.column_config.NumberColumn(format="%+.2f"),
        },
    )
    st.caption(
        f"Model with every input: {imp['accuracy_all'].iloc[0]:.1%} of winners picked over "
        f"{int(imp['n'].iloc[0]):,} fights. The model has no weigh-in weight input: fights are "
        "compared within a division, and size enters through reach. Control time enters as "
        "projected control seconds per round."
    )

st.subheader("Model vs bookmakers: was the edge real?")
best_a = fights[["close_low", "close_high"]].max(axis=1).map(components.decimal_odds_from_american)
best_b = (
    fights[["opp_close_low", "opp_close_high"]]
    .max(axis=1)
    .map(components.decimal_odds_from_american)
)
rows = fights.assign(best_a=best_a, best_b=best_b).dropna(subset=["market", "best_a", "best_b"])
if rows.empty:
    st.caption("No fights with both closing odds and a model prediction.")
else:
    edge_a = rows["p"] - rows["market"]
    pick_a = edge_a >= 0
    edge = edge_a.abs()
    price = np.where(pick_a, rows["best_a"], rows["best_b"])
    won = np.where(pick_a, rows["won"] == 1, rows["won"] == 0)
    result = pd.DataFrame(
        {
            "Edge": pd.cut(
                100 * edge,
                [0, 2.5, 5, 10, 15, 100],
                labels=["0–2.5 pts", "2.5–5 pts", "5–10 pts", "10–15 pts", "15+ pts"],
                include_lowest=True,
            ),
            "won": won,
            "return": np.where(won, price - 1, -1.0),
        }
    )
    summary = (
        result.groupby("Edge", observed=True)
        .agg(
            Fights=("won", "size"), **{"Won": ("won", "mean"), "Return per $1": ("return", "mean")}
        )
        .reset_index()
    )
    st.markdown(
        "For every past fight with closing odds: back whichever fighter the model rated higher "
        "than the bookmakers did, at the best closing price, and group by how big that gap was."
    )
    st.dataframe(
        summary,
        hide_index=True,
        width="stretch",
        column_config={
            "Won": st.column_config.NumberColumn(format="percent"),
            "Return per $1": st.column_config.NumberColumn(format="%+.3f"),
        },
    )
    st.caption(
        "A negative return means those bets lost money on average. Closing odds come from "
        "fight-canon-db; best price is the better end of the closing range. Not betting advice."
    )
