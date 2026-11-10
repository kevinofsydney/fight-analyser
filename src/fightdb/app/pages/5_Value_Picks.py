"""Value picks: per card, where the model rates a fighter above the bookmakers, and how it went."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fightdb.app.freshness import reload_changed_modules

reload_changed_modules()


import altair as alt
import pandas as pd
import streamlit as st

from fightdb.app import components, explain, value

components.page("Value picks", "🎯")
st.title("Value picks")
st.caption(
    "For each card: the fighters the model rates at least the chosen margin above the "
    "bookmakers' margin-free chance, the best price, and for past cards how they turned out "
    "(a $1 stake per pick). Not betting advice."
)

all_bouts = value.bouts_with_market()
if all_bouts.empty:
    st.info("No bouts have both a model prediction and bookmaker odds yet.")
    st.stop()

min_edge_pts = st.slider(
    "Minimum edge (percentage points)",
    min_value=0.0,
    max_value=25.0,
    value=5.0,
    step=0.5,
    help="How far the model's chance must exceed the bookmakers' margin-free chance.",
)
picks = value.add_picks(all_bouts, min_edge_pts / 100)

past = picks[picks["status"] == "completed"]
overall = value.summarise(past)
if overall["settled"]:
    st.warning(
        f"Track record at a {min_edge_pts:g}-point edge: {overall['settled']:,} past picks won "
        f"{overall['won'] / overall['settled']:.0%} of the time and returned "
        f"{overall['return']:+.1%} per $1 staked ({overall['profit']:+.1f} units). The "
        "bookmakers' closing lines have been more accurate than this model (see Model "
        "performance), so treat a big edge as the model possibly missing something."
    )

events = (
    picks.groupby(["event_id", "event_name", "event_date", "status"], as_index=False)
    .agg(bouts=("bout_id", "size"))
    .sort_values("event_date", ascending=False)
)
upcoming = events[events["status"] == "scheduled"].sort_values("event_date")
completed = events[events["status"] == "completed"]
order = pd.concat([upcoming, completed]).drop_duplicates("event_id")
labels = dict(
    zip(
        order["event_id"],
        order["event_name"] + " · " + order["event_date"].map(components.fmt_date),
        strict=True,
    )
)
pending = st.session_state.pop("vp_open_event", None)
if pending in labels:
    st.session_state["vp_event"] = pending
event_id = st.selectbox(
    "Card", list(labels), format_func=labels.get, key="vp_event", help="Upcoming cards first."
)

card = picks[picks["event_id"] == event_id].sort_values("card_order")
is_past = bool((card["status"] == "completed").all())
summary = value.summarise(card)
cols = st.columns(4)
cols[0].metric("Bouts with odds", len(card))
cols[1].metric("Model picks", summary["picks"])
if is_past:
    cols[2].metric("Picks won", f"{summary['won']} of {summary['settled']}")
    cols[3].metric(
        "Profit / loss",
        f"{summary['profit']:+.2f} units",
        help="$1 on each pick at the best price; draws and no contests are void.",
    )


def result_line(r: pd.Series) -> str:
    if r["status"] != "completed":
        return "Upcoming"
    a = pd.Series({"fighter_name": r["fighter_name"], **r.to_dict()})
    b = pd.Series({"fighter_name": r["opp_name"], "won": r["lost"], "lost": r["won"]})
    return explain.result_text(a, b).replace("**", "")


def text(values: pd.Series, fmt: str) -> list[str]:
    """Numbers as text so bouts without a pick show a dash rather than 'None'."""
    return ["—" if pd.isna(v) else fmt.format(v) for v in values]


table = pd.DataFrame(
    {
        "Bout": card["fighter_name"] + " vs " + card["opp_name"],
        "Model pick": card["pick"].fillna("—"),
        "Price": text(card["price"], "{:.2f}"),
        "Model chance": text(card["pick_p"], "{:.0%}"),
        "Bookmakers' chance": text(card["pick_market_p"].where(card["has_pick"]), "{:.0%}"),
        "Edge": text(100 * card["edge"], "{:+.1f} pts"),
        "Expected return per $1": text(card["expected_return"], "{:+.2f}"),
        "Result": [result_line(r) for _, r in card.iterrows()],
        "Pick": card["outcome"].replace({"Won": "✓ Won", "Lost": "✗ Lost", "": "—"}),
        "Profit": text(card["profit"], "{:+.2f}"),
        "Odds": card["odds_source"],
    }
)
if not is_past:
    table = table.drop(columns=["Result", "Pick", "Profit"])
help_text = {k: v.get("help") for k, v in explain.EDGE_COLUMNS.items()}
st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    column_config={
        **{k: st.column_config.TextColumn(help=h) for k, h in help_text.items() if k in table},
        "Price": st.column_config.TextColumn(help="Best decimal price."),
    },
)
st.caption(
    "Bouts without a pick are those where the model and the bookmakers are within the minimum "
    "edge. Past cards use the closing line where available, otherwise the last bookmaker prices "
    "fetched before the event."
)

st.subheader("Every past card")
by_card = (
    past.groupby(["event_id", "event_name", "event_date"], as_index=False)
    .apply(lambda g: pd.Series(value.summarise(g)), include_groups=False)
    .sort_values("event_date", ascending=False)
)
by_card = by_card[by_card["picks"] > 0]
if by_card.empty:
    st.caption("No past picks at this edge.")
else:
    running = by_card.sort_values("event_date").assign(cumulative=lambda d: d["profit"].cumsum())
    st.altair_chart(
        alt.Chart(running, title="Cumulative profit / loss ($1 per pick)")
        .mark_line(strokeWidth=2, color=explain.SIDE_COLORS[0])
        .encode(
            x=alt.X("event_date:T", title=None),
            y=alt.Y("cumulative:Q", title="Units"),
            tooltip=[
                alt.Tooltip("event_name:N", title="Card"),
                alt.Tooltip("event_date:T", title="Date", format="%-d %b %Y"),
                alt.Tooltip("profit:Q", title="Card profit", format="+.2f"),
                alt.Tooltip("cumulative:Q", title="Running total", format="+.2f"),
            ],
        )
        .properties(height=260),
        width="stretch",
    )
    shown = pd.DataFrame(
        {
            "Date": by_card["event_date"].map(components.fmt_date),
            "Card": by_card["event_name"],
            "Picks": by_card["picks"].astype(int),
            "Won": by_card["won"].astype(int),
            "Profit": by_card["profit"],
            "Return per $1": by_card["return"],
        }
    )
    selection = st.dataframe(
        shown,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key="vp_cards",
        column_config={
            "Profit": st.column_config.NumberColumn(format="%+.2f"),
            "Return per $1": st.column_config.NumberColumn(format="%+.3f"),
        },
    )
    st.caption("Select a card to show it above.")
    if selection.selection.rows:
        chosen = by_card.iloc[selection.selection.rows[0]]["event_id"]
        if chosen != event_id:
            st.session_state["vp_open_event"] = chosen
            st.session_state.pop("vp_cards", None)
            st.rerun()
