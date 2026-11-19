"""Previous cards: how the model and the bookmakers did on each finished card."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fightdb.app.freshness import reload_changed_modules

reload_changed_modules()

import streamlit as st

from fightdb.app import card_view, components, past_cards

components.page("Previous cards", "🗓️")
st.title("Previous cards")
st.caption(
    "How the model's picks turned out, card by card. Each bout is scored by the model trained "
    "on every fight before that year, so it never saw the result. Upcoming bouts use a model "
    "that also knows this year's fights, so the number shown before a fight can differ from "
    "the one here by a point or two."
)

scored = card_view.load_scored()
if scored.empty:
    st.info("No scored past bouts yet. The model needs a daily refresh with enough history.")
    st.stop()
cards = past_cards.by_card(scored)
_pct, _counts = card_view.pct, card_view.counts


st.subheader("Recent cards")
n_cards = (
    st.slider(
        "Cards to include", min_value=4, max_value=min(52, len(cards)), value=min(12, len(cards))
    )
    if len(cards) > 4
    else len(cards)
)
recent = cards.head(n_cards)
recent_scored = scored[scored["event_id"].isin(recent["event_id"])]
total = past_cards.summary(recent_scored)
BRIER_HELP = (
    "Average squared gap between the predicted chance and what happened, on bouts with a "
    "price. Lower is better; always saying 50% scores 0.250."
)
c = st.columns(4)
c[0].metric("Model picked the winner", _pct(total["model_right"], total["decisive"]))
c[1].metric(
    "Bookmakers picked the winner",
    _pct(total["market_right"], total["with_market"]),
    help="Closing-line favourite, or the last bookmaker prices fetched before the event.",
)
both = total["with_market"] > 0
c[2].metric(
    "Model Brier",
    f"{total['model_brier_on_market' if both else 'model_brier']:.3f}",
    help=BRIER_HELP,
)
c[3].metric("Bookmakers Brier", f"{total['market_brier']:.3f}" if both else "—", help=BRIER_HELP)
st.caption(_counts(total))
picked = st.dataframe(
    recent.assign(event_date=recent["event_date"].map(components.fmt_date)),
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
    key="previous_cards_table",
    column_order=[
        "event_name",
        "event_date",
        "model",
        "model_pct",
        "market",
        "market_pct",
        "model_brier",
        "market_brier",
    ],
    column_config={
        "event_name": "Event",
        "event_date": "Date",
        "model": "Model right",
        "model_pct": st.column_config.ProgressColumn(
            "Model %", format="percent", min_value=0.0, max_value=1.0
        ),
        "market": "Bookmakers right",
        "market_pct": st.column_config.ProgressColumn(
            "Bookmakers %", format="percent", min_value=0.0, max_value=1.0
        ),
        "model_brier": st.column_config.NumberColumn("Model Brier", format="%.3f"),
        "market_brier": st.column_config.NumberColumn("Bookmakers Brier", format="%.3f"),
    },
)
st.caption("Select a card to show its bouts below. Draws and no contests are left out.")

labels = {
    r.event_id: f"{r.event_name} · {components.fmt_date(r.event_date)}" for r in cards.itertuples()
}
# Apply a table selection once, so the selectbox can still be changed afterwards.
chosen = recent.iloc[picked.selection.rows[0]]["event_id"] if picked.selection.rows else None
if chosen != st.session_state.get("previous_card_chosen"):
    st.session_state["previous_card_chosen"] = chosen
    if chosen is not None:
        st.session_state["previous_card"] = chosen
event_id = st.selectbox("Card", list(labels), format_func=labels.get, key="previous_card")

card = card_view.in_card_order(scored[scored["event_id"] == event_id])
st.subheader(labels[event_id])
bankroll, kelly = card_view.bet_settings("previous")
card = past_cards.add_bets(card, bankroll=bankroll, kelly_fraction=kelly)
card_view.card_metrics(card)
card_view.card_table(card, key=f"previous_bouts_{event_id}")
st.caption(f"{components.NAV_HINT} Title and five-round bouts first.")
st.caption("Model probabilities are for analysis, not betting advice.")
