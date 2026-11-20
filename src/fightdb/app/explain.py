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
            .mark_bar(cornerRadiusEnd=4, height={"band": 0.7})
            .encode(
                x=alt.X("Push:Q", title=f"Push (← {opp_name} · {self_name} →)"),
                y=alt.Y(
                    "Factor:N",
                    sort=list(chart_rows["Factor"]),
                    title=None,
                    axis=alt.Axis(labelLimit=320, labelOverlap=False),
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
            .properties(height=alt.Step(28)),
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
    )


# A two-way price whose implied chances add up to less than 100% or more than 115% is stale or
# broken (e.g. an exchange quoting 1.02 on both fighters), so it is left out of the market view.
MAX_OVERROUND = 1.15


def market_view(prices: pd.DataFrame, fighter_ids: list[str]) -> pd.DataFrame | None:
    """Per fighter: the bookmakers' vig-free chance (median across usable books), the best price
    and who offers it. `prices` has bookmaker, fighter_id, decimal_odds."""
    wide = prices.pivot_table(
        index="bookmaker", columns="fighter_id", values="decimal_odds", aggfunc="last"
    ).reindex(columns=fighter_ids)
    wide = wide.dropna()
    if wide.empty:
        return None
    inverse = 1 / wide
    overround = inverse.sum(axis=1)
    usable = (overround >= 1.0) & (overround <= MAX_OVERROUND)
    if not usable.any():
        return None
    fair = inverse[usable].div(overround[usable], axis=0)
    best = wide[usable]
    return pd.DataFrame(
        {
            "market_p": fair.median(),
            "best_odds": best.max(),
            "best_book": best.idxmax(),
            "books": int(usable.sum()),
        }
    )


def edge_table(
    names: list[str], model_p: list[float], market_p: list[float], best_odds: list[float]
) -> pd.DataFrame:
    """Model chance vs the bookmakers' vig-free chance, per fighter."""
    rows = []
    for name, p, m, odds in zip(names, model_p, market_p, best_odds, strict=True):
        rows.append(
            {
                "Fighter": name,
                "Model chance": p,
                "Model fair odds": 1 / p if p > 0 else math.nan,
                "Bookmakers' chance": m,
                "Best price": odds,
                "Edge": 100 * (p - m),
                "Expected return per $1": p * odds - 1 if not math.isnan(odds) else math.nan,
            }
        )
    return pd.DataFrame(rows)


def edge_display(table: pd.DataFrame) -> pd.DataFrame:
    """`edge_table` with chances as whole percentages, for `EDGE_COLUMNS`."""
    return table.assign(**{c: 100 * table[c] for c in ("Model chance", "Bookmakers' chance")})


EDGE_COLUMNS = {
    "Model chance": st.column_config.NumberColumn(format="%.0f%%"),
    "Model fair odds": st.column_config.NumberColumn(
        format="%.2f", help="1 ÷ model chance: the decimal price at which a bet breaks even."
    ),
    "Bookmakers' chance": st.column_config.NumberColumn(
        format="%.0f%%",
        help="The chance implied by the bookmakers' prices with their margin removed.",
    ),
    "Best price": st.column_config.NumberColumn(format="%.2f"),
    "Edge": st.column_config.NumberColumn(
        format="%+.1f pts",
        help="Model chance minus bookmakers' chance, in percentage points. Positive means the "
        "model rates the fighter higher than the market does.",
    ),
    "Expected return per $1": st.column_config.NumberColumn(
        format="%+.2f",
        help="Model chance × best price − 1: what a $1 bet would return on average if the model's "
        "chance were right.",
    ),
}


EDGE_NOTE = (
    "In the backtest the bookmakers' closing lines have been more accurate than this model (see "
    "Model performance), so a large edge more often means the model is missing something than "
    "that the price is wrong."
)
METHOD_NAMES = {"ko": "KO/TKO", "sub": "submission", "dec": "decision"}
_METHOD_GROUP_KEY = {"ko_tko": "ko", "sub": "sub", "dec": "dec"}


def _num(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return math.nan


def likely_outcomes(a: pd.Series, b: pd.Series) -> pd.DataFrame | None:
    """The six possible endings (who wins, how) with the model's chances, most likely first."""
    rows = []
    for side, tag in ((a, "a"), (b, "b")):
        for method in ("ko", "sub", "dec"):
            p = _num(side.get(f"self_p_win_{method}"))
            if math.isnan(p):
                return None
            rows.append(
                {
                    "key": f"{tag}_{method}",
                    "label": f"{side.fighter_name} by {METHOD_NAMES[method]}",
                    "p": p,
                }
            )
    return pd.DataFrame(rows).sort_values("p", ascending=False).reset_index(drop=True)


def actual_outcome(a: pd.Series, b: pd.Series) -> str | None:
    method = _METHOD_GROUP_KEY.get(str(a.get("method_group")))
    if method is None:
        return None
    if _num(a.get("won")) == 1:
        return f"a_{method}"
    if _num(b.get("won")) == 1:
        return f"b_{method}"
    return None


def result_text(a: pd.Series, b: pd.Series) -> str:
    """e.g. 'Jon Jones won by Submission in round 1 at 2:04.'"""
    method = str(a.get("method") or "").strip()
    end_round = _num(a.get("end_round"))
    seconds = _num(a.get("fight_seconds"))
    when = ""
    if not math.isnan(end_round):
        when = f" in round {end_round:.0f}"
        in_round = seconds - (end_round - 1) * 300
        if not math.isnan(in_round) and 0 < in_round <= 300:
            when += f" at {int(in_round) // 60}:{int(in_round) % 60:02d}"
    how = f" by {method}" if method else ""
    if _num(a.get("won")) == 1 or _num(b.get("won")) == 1:
        winner = a if _num(a.get("won")) == 1 else b
        return f"**{winner.fighter_name}** won{how}{when}."
    if _num(a.get("draw")) == 1:
        return f"**Draw**{how}{when}."
    if _num(a.get("no_contest")) == 1:
        return f"**No contest**{when}."
    return "Result not recorded."


def _method_input_label(feature: str, self_name: str, opp_name: str) -> str:
    who = ""
    key = feature
    if feature.startswith("diff_"):
        key, who = feature.removeprefix("diff_"), "Gap in "
    elif feature.startswith("self_"):
        key, who = feature.removeprefix("self_"), f"{self_name}: "
    elif feature.startswith("opp_"):
        key, who = feature.removeprefix("opp_"), f"{opp_name}: "
    raw = key.endswith("_raw")
    base = key.removesuffix("_raw")
    label = BY_KEY[base].label if base in BY_KEY else PROJECTION_LABELS.get(base, base)
    label = label if who == "Gap in " or not label else label[0].lower() + label[1:]
    return f"{who}{label}" + (" (unadjusted)" if raw else "")


def _method_input_value(feature: str, value: float) -> str:
    key = feature.removeprefix("diff_").removeprefix("self_").removeprefix("opp_")
    return _fmt(key.removesuffix("_raw"), value, signed=feature.startswith("diff_"))


def render_ending_breakdown(
    a: pd.Series, b: pd.Series, model: outcome.FittedModels | None, top_n: int = 6
) -> None:
    """Why the model's likeliest ending is the likeliest: win chance × how that fighter's wins
    usually come, then which inputs tilt the method split."""
    outcomes = likely_outcomes(a, b)
    if outcomes is None:
        st.caption("The model has no method prediction for this bout.")
        return
    sides = {"a": a, "b": b}
    names = {"a": str(a.fighter_name), "b": str(b.fighter_name)}
    rows = []
    for tag, side in sides.items():
        pw = _num(side.get("self_p_win"))
        for m in ("ko", "sub", "dec"):
            p = _num(side.get(f"self_p_win_{m}"))
            rows.append(
                {
                    "Fighter": names[tag],
                    "Ending": f"{names[tag]} by {METHOD_NAMES[m]}",
                    "Win chance": pw,
                    "Share of their wins": p / pw if pw > 0 else math.nan,
                    "Chance of this ending": p,
                }
            )
    table = pd.DataFrame(rows).sort_values("Chance of this ending", ascending=False)
    top = outcomes.iloc[0]
    tag, m = str(top["key"]).split("_")
    win_side = sides[tag]
    pw = _num(win_side.get("self_p_win"))
    st.markdown(
        "The model works out each of the six endings in two steps: **who wins** (the win model, "
        "explained above) × **how that fighter's wins split** between KO/TKO, submission and "
        "decision (a second model trained on how fights ended). "
        f"The likeliest ending is **{top['label']}**: {names[tag]} wins {pw:.0%} of the time × "
        f"{top['p'] / pw:.0%} of those wins by {METHOD_NAMES[m]} = **{top['p']:.0%}**."
    )
    st.altair_chart(
        alt.Chart(table)
        .mark_bar(cornerRadiusEnd=4, height={"band": 0.7})
        .encode(
            x=alt.X("Chance of this ending:Q", title=None, axis=alt.Axis(format="%")),
            y=alt.Y(
                "Ending:N",
                sort=list(table["Ending"]),
                title=None,
                axis=alt.Axis(labelLimit=320),
            ),
            color=alt.Color(
                "Fighter:N",
                scale=alt.Scale(domain=list(names.values()), range=SIDE_COLORS),
                legend=None,
            ),
            tooltip=[
                "Ending:N",
                alt.Tooltip("Win chance:Q", format=".0%"),
                alt.Tooltip("Share of their wins:Q", format=".0%"),
                alt.Tooltip("Chance of this ending:Q", format=".0%"),
            ],
        )
        .properties(height=alt.Step(30)),
        width="stretch",
    )
    pct_cols = ["Win chance", "Share of their wins", "Chance of this ending"]
    st.dataframe(
        table.drop(columns=["Fighter"]).assign(**{c: 100 * table[c] for c in pct_cols}),
        hide_index=True,
        width="stretch",
        column_config={c: st.column_config.NumberColumn(format="%.0f%%") for c in pct_cols},
    )
    second = outcomes.iloc[1]
    if str(second["key"]).split("_")[0] != tag:
        st.caption(
            f"Runner-up: {second['label']} ({second['p']:.0%}). The two differ mainly in who "
            "wins, which the win-model breakdown explains."
        )
    if model is None:
        return
    other = [x for x in ("ko", "sub", "dec") if x != m]
    side_df = pd.DataFrame([win_side])
    opp_name = names["b" if tag == "a" else "a"]
    st.markdown(
        f"**What tilts {names[tag]}'s wins towards {METHOD_NAMES[m]}**: the inputs that moved the "
        "method split most, for the two alternatives."
    )
    cols = st.columns(2)
    for col, alt_m in zip(cols, other, strict=True):
        c = outcome.method_contributions(model, side_df, f"win_{m}", f"win_{alt_m}")
        c = c.reindex(c["push"].abs().sort_values(ascending=False).index).head(top_n)
        with col:
            st.caption(f"{METHOD_NAMES[m]} rather than {METHOD_NAMES[alt_m]}")
            st.dataframe(
                pd.DataFrame(
                    {
                        "Input": [
                            _method_input_label(f, names[tag], opp_name) for f in c["feature"]
                        ],
                        "Value": [
                            _method_input_value(f, v)
                            for f, v in zip(c["feature"], c["value"], strict=True)
                        ],
                        "Pushes towards": [
                            METHOD_NAMES[m] if p > 0 else METHOD_NAMES[alt_m] for p in c["push"]
                        ],
                        "Push": c["push"],
                    }
                ),
                hide_index=True,
                width="stretch",
                column_config={"Push": st.column_config.NumberColumn(format="%+.2f")},
            )
    st.caption(
        "Push is the change in log-odds between the two endings. Gaps are this fighter minus "
        "the opponent; index values are relative to the division average (100). Pushes are from "
        f"{names[tag]}'s point of view; the chances above average both fighters' points of view."
    )
