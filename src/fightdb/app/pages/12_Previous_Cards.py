"""Previous cards: how the model and the bookmakers did on each finished card."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fightdb.app.freshness import reload_changed_modules

reload_changed_modules()

import pandas as pd
import streamlit as st

from fightdb.app import components, data, past_cards, value

components.page("Previous cards", "🗓️")
st.title("Previous cards")
st.caption(
    "How the model's picks turned out, card by card. Each bout is scored by the model trained "
    "on every fight before that year, so it never saw the result. Upcoming bouts use a model "
    "that also knows this year's fights, so the number shown before a fight can differ from "
    "the one here by a point or two."
)

bouts = data.query(past_cards.PAST_BOUTS_SQL + " ORDER BY a.event_date DESC")
if bouts.empty:
    st.info("No scored past bouts yet. The model needs a daily refresh with enough history.")
    st.stop()
market = value.bouts_with_market()
if not market.empty:
    bouts = bouts.merge(market[["bout_id", "market_p"]], on="bout_id", how="left")
scored = past_cards.score(bouts)
cards = past_cards.by_card(scored)


def _pct(right: float, n: int) -> str:
    return f"{right / n:.0%}" if n else "—"


def _counts(s: dict[str, float]) -> str:
    text = f"Model {s['model_right']:.0f} of {s['decisive']}"
    if s["with_market"]:
        text += f", bookmakers {s['market_right']:.0f} of {s['with_market']}"
    if s["endings"]:
        text += f", likeliest ending {s['ending_right']:.0f} of {s['endings']}"
    return text + "."


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

# Finished bouts carry no card position, so title and five-round fights go first.
card = (
    scored[scored["event_id"] == event_id]
    .sort_values(["is_title", "scheduled_rounds", "card_order"], ascending=[False, False, True])
    .reset_index(drop=True)
)
st.subheader(labels[event_id])
s = past_cards.summary(card)
c = st.columns(4)
c[0].metric("Model picked the winner", _pct(s["model_right"], s["decisive"]))
c[1].metric("Bookmakers picked the winner", _pct(s["market_right"], s["with_market"]))
c[2].metric(
    "Model's likeliest ending happened",
    _pct(s["ending_right"], s["endings"]),
    help="Of the six endings (either fighter by KO/TKO, submission or decision).",
)
c[3].metric(
    "Biggest upset (model)",
    f"{card['p_winner'].min():.0%}" if card["p_winner"].notna().any() else "—",
    help="The lowest chance the model gave a fighter who went on to win.",
)
st.caption(_counts(s))


def _mark(v: object) -> str:
    return "—" if pd.isna(v) else ("✅" if v else "❌")  # type: ignore[arg-type]


def _how(row: pd.Series) -> str:
    if row["draw"] == 1:
        return "Draw"
    if row["no_contest"] == 1:
        return "No contest"
    method = row["method"] if isinstance(row["method"], str) else "—"
    return f"{method}, R{int(row['end_round'])}" if pd.notna(row["end_round"]) else method


show = pd.DataFrame(
    {
        "Bout": card["fighter_name"] + "  vs  " + card["opp_name"],
        "Model pick": card["model_pick"] + " " + card["model_pick_p"].map("{:.0%}".format),
        "Model": card["model_right"].map(_mark),
        "Bookmakers pick": [
            "—" if pd.isna(p) else f"{n} {p:.0%}"
            for n, p in zip(card["market_pick"], card["market_pick_p"], strict=True)
        ],
        "Bookmakers": card["market_right"].map(_mark),
        "Winner": card["winner"].fillna("—"),
        "How": card.apply(_how, axis=1),
        "Model's chance for the winner": card["p_winner"],
        "Model's likeliest ending": card["model_ending"].fillna("—"),
    }
)
selection = st.dataframe(
    show,
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
    key=f"previous_bouts_{event_id}",
    column_config={
        "Model's chance for the winner": st.column_config.NumberColumn(format="percent"),
    },
)
st.caption("Select a bout to open its Bout page. Title and five-round bouts first.")
if selection.selection.rows:
    st.session_state["bout_id"] = card.iloc[selection.selection.rows[0]]["bout_id"]
    for key in ("bout_event_selector", "bout_selector"):
        st.session_state.pop(key, None)
    st.switch_page("pages/1_Bout.py")

st.caption("Model probabilities are for analysis, not betting advice.")
