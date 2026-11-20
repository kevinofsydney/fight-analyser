"""Finished cards on screen: the scored bouts with prices, and one card's bout table."""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from fightdb.app import components, data, past_cards, value


def load_scored() -> pd.DataFrame:
    """Every scored finished bout, newest first, with the bookmakers' chance and best prices
    (closing line where known, else the last prices fetched before the event). Cached until the
    database file changes."""
    return _load_scored(data.db_mtime())


def with_bets(scored: pd.DataFrame, bankroll: float, kelly: float) -> pd.DataFrame:
    """`past_cards.add_bets` on every scored bout, cached per database and stake setting."""
    return _with_bets(data.db_mtime(), bankroll, kelly)


def load_cards() -> pd.DataFrame:
    """`past_cards.by_card` over every scored bout, cached until the database changes."""
    return _load_cards(data.db_mtime())


@st.cache_data(ttl=data.TTL, show_spinner=False)
def _load_cards(mtime: float) -> pd.DataFrame:
    return past_cards.by_card(_load_scored(mtime))


@st.cache_data(ttl=data.TTL, show_spinner=False)
def _with_bets(mtime: float, bankroll: float, kelly: float) -> pd.DataFrame:
    return past_cards.add_bets(_load_scored(mtime), bankroll=bankroll, kelly_fraction=kelly)


@st.cache_data(ttl=data.TTL, show_spinner=False)
def _load_scored(mtime: float) -> pd.DataFrame:
    bouts = data.query(past_cards.PAST_BOUTS_SQL + " ORDER BY a.event_date DESC")
    if bouts.empty:
        return bouts
    market = value.bouts_with_market()
    if not market.empty:
        bouts = bouts.merge(
            market[["bout_id", "market_p", "best_a", "best_b"]], on="bout_id", how="left"
        )
    return past_cards.score(bouts)


def in_card_order(card: pd.DataFrame) -> pd.DataFrame:
    # Finished bouts carry no card position, so title and five-round fights go first.
    return card.sort_values(
        ["is_title", "scheduled_rounds", "card_order"], ascending=[False, False, True]
    ).reset_index(drop=True)


def _mark(v: object) -> str:
    return "—" if pd.isna(v) else ("✅" if v else "❌")  # type: ignore[arg-type]


def _how(row: pd.Series) -> str:
    if row["draw"] == 1:
        return "Draw"
    if row["no_contest"] == 1:
        return "No contest"
    method = row["method"] if isinstance(row["method"], str) else "—"
    return f"{method}, R{int(row['end_round'])}" if pd.notna(row["end_round"]) else method


def _money(v: object) -> str:
    return "—" if v is None or pd.isna(v) else f"{'+' if v >= 0 else '−'}${abs(v):,.0f}"  # type: ignore[operator, arg-type]


def card_table(card: pd.DataFrame, key: str) -> None:
    """One card's bouts: picks, result and, when `card` has `add_bets` columns, the bets."""
    winner_id = card["fighter_id"].where(card["won"] == 1, card["opp_id"]).where(card["decisive"])
    model_pick_id = card["fighter_id"].where(card["p"] >= 0.5, card["opp_id"])
    market_pick_id = (
        card["fighter_id"]
        .where(card["market_p"] >= 0.5, card["opp_id"])
        .where(card["market_p"].notna())
    )
    shown = pd.DataFrame(
        {
            "Fighter": card["fighter_name"],
            "Opponent": card["opp_name"],
            "Model pick": card["model_pick"],
            "Model %": 100 * card["model_pick_p"],
            "Model": card["model_right"].map(_mark),
            "Bookmakers pick": card["market_pick"].fillna("—"),
            "Bookmakers %": 100 * card["market_pick_p"],
            "Bookmakers": card["market_right"].map(_mark),
            "Winner": card["winner"].fillna("—"),
            "How": card.apply(_how, axis=1),
            "Model's likeliest ending": card["model_ending"].fillna("—"),
        }
    )
    ids = {
        "Fighter": card["fighter_id"],
        "Opponent": card["opp_id"],
        "Model pick": model_pick_id,
        "Bookmakers pick": market_pick_id,
        "Winner": winner_id,
    }
    if "flat_profit" in card:
        shown["$100 bet"] = card["flat_profit"].map(_money)
        shown["Edge bet"] = [
            "No bet"
            if pd.isna(stake) or stake <= 0 or pick is None
            else f"${stake:,.0f} on {pick} @ {price:.2f}"
            for stake, pick, price in zip(
                card["edge_stake"], card["edge_pick"], card["edge_price"], strict=True
            )
        ]
        shown["Edge bet result"] = card["edge_profit"].map(_money)
        ids["Edge bet"] = (
            card["fighter_id"]
            .where(card["edge_pick"] == card["fighter_name"], card["opp_id"])
            .where(card["edge_pick"].notna())
        )
    components.nav_table(
        shown,
        key=key,
        fighter_ids=ids,
        bout_ids=card["bout_id"],
        column_config={
            "Model %": st.column_config.NumberColumn(format="%.0f%%"),
            "Bookmakers %": st.column_config.NumberColumn(format="%.0f%%"),
        },
    )


def money(v: float) -> str:
    return _money(v) if not np.isnan(v) else "—"


KELLY_CHOICES = {"Quarter Kelly": 0.25, "Half Kelly": 0.5, "Full Kelly": 1.0}
EDGE_HELP = (
    "Kelly staking: back whichever fighter the model rates above the best price, staking "
    "(model chance × decimal price − 1) ÷ (price − 1) of the bankroll. A bigger edge means a "
    "bigger stake; no edge, no bet. Full Kelly swings hard when the model is overconfident, so "
    "quarter or half is the usual choice. The bankroll stays fixed (stakes don't compound)."
)


def bet_settings(key: str) -> tuple[float, float]:
    """(bankroll, Kelly fraction) for edge-sized betting."""
    left, right = st.columns(2)
    bankroll = left.number_input(
        "Bankroll for edge-sized bets ($)",
        min_value=100,
        max_value=1_000_000,
        value=1_000,
        step=100,
        key=f"{key}_bankroll",
        help=EDGE_HELP,
    )
    choice = right.radio(
        "Edge sizing", list(KELLY_CHOICES), horizontal=True, key=f"{key}_kelly", help=EDGE_HELP
    )
    return float(bankroll), KELLY_CHOICES[str(choice)]


def pct(right: float, n: float) -> str:
    return f"{right / n:.0%}" if n else "—"


def counts(s: dict[str, float]) -> str:
    text = f"Model {s['model_right']:.0f} of {s['decisive']}"
    if s["with_market"]:
        text += f", bookmakers {s['market_right']:.0f} of {s['with_market']}"
    if s["endings"]:
        text += f", likeliest ending {s['ending_right']:.0f} of {s['endings']}"
    return text + "."


def card_metrics(card: pd.DataFrame) -> None:
    s = past_cards.summary(card)
    c = st.columns(5 if "flat_profit" in card else 4)
    c[0].metric("Model right", pct(s["model_right"], s["decisive"]))
    c[1].metric("Bookmakers right", pct(s["market_right"], s["with_market"]))
    c[2].metric(
        "Likeliest ending",
        pct(s["ending_right"], s["endings"]),
        help="How often the model's likeliest of the six endings (either fighter by KO/TKO, "
        "submission or decision) happened.",
    )
    if "flat_profit" in card:
        flat, edge = card["flat_profit"].sum(min_count=1), card["edge_profit"].sum(min_count=1)
        c[3].metric(
            "$100 a pick",
            money(float(flat)),
            help="$100 on the model's pick in every bout with a price, at the best price.",
        )
        c[4].metric("Edge-sized", money(float(edge)), help=EDGE_HELP)
    else:
        c[3].metric(
            "Biggest upset",
            f"{card['p_winner'].min():.0%}" if card["p_winner"].notna().any() else "—",
            help="The lowest chance the model gave a fighter who went on to win.",
        )
    st.caption(counts(s))
