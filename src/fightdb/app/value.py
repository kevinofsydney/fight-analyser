"""Where the model disagrees with the bookmakers, per card, and how those picks turned out."""

from __future__ import annotations

import math

import duckdb
import numpy as np
import pandas as pd

from fightdb.app import components, data, explain

_BOUTS_SQL = """
SELECT a.bout_id, a.event_id, a.event_name, a.event_date, a.card_order, a.status,
       a.fighter_id, a.fighter_name, a.opp_id, a.opp_name, a.self_p_win AS p,
       a.won, a.lost, a.draw, a.no_contest, a.method, a.method_group, a.end_round,
       a.fight_seconds, a.self_implied_prob AS close_p,
       o.close_low, o.close_high, ob.close_low AS opp_close_low, ob.close_high AS opp_close_high
FROM bout_sides a
LEFT JOIN odds o ON o.bout_id = a.bout_id AND o.fighter_id = a.fighter_id
LEFT JOIN odds ob ON ob.bout_id = a.bout_id AND ob.fighter_id = a.opp_id
WHERE a.is_a AND a.status IN ('completed', 'scheduled') AND a.self_p_win IS NOT NULL
"""


def _table_exists(name: str) -> bool:
    return not data.query(
        "SELECT table_name FROM information_schema.tables WHERE table_name = ?", [name]
    ).empty


def _best(low: object, high: object) -> float:
    prices = [float(v) for v in (low, high) if v is not None and not pd.isna(v)]  # type: ignore[arg-type]
    value = components.decimal_odds_from_american(max(prices)) if prices else None
    return math.nan if value is None else float(value)


def _polled_prices(bouts: pd.DataFrame) -> pd.DataFrame:
    """Latest fetched bookmaker prices per bout: the current poll for upcoming bouts, the last
    poll before the event for past ones (history is only kept from when it started)."""
    frames = []
    if _table_exists("upcoming_odds"):
        frames.append(
            data.query(
                "SELECT bout_id, fighter_id, bookmaker, decimal_odds, fetched_at FROM upcoming_odds"
            )
        )
    if _table_exists("upcoming_odds_history"):
        frames.append(
            data.query(
                """SELECT h.bout_id, h.fighter_id, h.bookmaker, h.decimal_odds, h.fetched_at
                   FROM upcoming_odds_history h
                   JOIN bout_sides s ON s.bout_id = h.bout_id AND s.is_a
                   WHERE CAST(h.fetched_at AS DATE) <= s.event_date
                   QUALIFY h.fetched_at = max(h.fetched_at) OVER (PARTITION BY h.bout_id)"""
            )
        )
    if not frames:
        return pd.DataFrame(columns=["bout_id", "market_p", "best_a", "best_b", "fetched_at"])
    prices = pd.concat(frames, ignore_index=True)
    prices = prices[prices["bout_id"].isin(bouts["bout_id"])]
    # The newest poll per bout, whichever table it came from.
    prices = prices[
        prices["fetched_at"] == prices.groupby("bout_id")["fetched_at"].transform("max")
    ]
    sides = bouts.set_index("bout_id")[["fighter_id", "opp_id"]]
    rows = []
    for bout_id, group in prices.groupby("bout_id"):
        a_id, b_id = sides.loc[bout_id, "fighter_id"], sides.loc[bout_id, "opp_id"]
        view = explain.market_view(group.drop_duplicates(["bookmaker", "fighter_id"]), [a_id, b_id])
        if view is None:
            continue
        rows.append(
            {
                "bout_id": bout_id,
                "market_p": float(view.loc[a_id, "market_p"]),
                "best_a": float(view.loc[a_id, "best_odds"]),
                "best_b": float(view.loc[b_id, "best_odds"]),
                "fetched_at": group["fetched_at"].max(),
            }
        )
    return pd.DataFrame(rows, columns=["bout_id", "market_p", "best_a", "best_b", "fetched_at"])


def bouts_with_market(event_id: str | None = None) -> pd.DataFrame:
    """One row per bout (A side) with the model's chance, the bookmakers' margin-free chance and
    best prices: closing line where known, else the latest fetched bookmaker prices."""
    try:
        sql = _BOUTS_SQL + (" AND a.event_id = ?" if event_id else "")
        bouts = data.query(sql, [event_id] if event_id else [])
    except duckdb.Error:
        return pd.DataFrame()
    if bouts.empty:
        return bouts
    bouts["best_a"] = [
        _best(lo, hi) for lo, hi in zip(bouts["close_low"], bouts["close_high"], strict=True)
    ]
    bouts["best_b"] = [
        _best(lo, hi)
        for lo, hi in zip(bouts["opp_close_low"], bouts["opp_close_high"], strict=True)
    ]
    closing = bouts["close_p"].notna() & bouts["best_a"].notna() & bouts["best_b"].notna()
    bouts["market_p"] = bouts["close_p"].where(closing)
    bouts["odds_source"] = pd.Series("Closing line", index=bouts.index).where(closing)

    polled = _polled_prices(bouts[~closing])
    if not polled.empty:
        bouts = bouts.merge(polled, on="bout_id", how="left", suffixes=("", "_polled"))
        use = bouts["market_p"].isna() & bouts["market_p_polled"].notna()
        for col in ("market_p", "best_a", "best_b"):
            bouts.loc[use, col] = bouts.loc[use, f"{col}_polled"]
        bouts.loc[use, "odds_source"] = "Bookmakers, " + bouts.loc[use, "fetched_at"].map(
            components.fmt_date
        )
        bouts = bouts.drop(
            columns=["market_p_polled", "best_a_polled", "best_b_polled", "fetched_at"]
        )
    return bouts[bouts["market_p"].notna()].reset_index(drop=True)


def add_picks(bouts: pd.DataFrame, min_edge: float) -> pd.DataFrame:
    """The model's pick in each bout (the side it rates at least `min_edge` above the market),
    its price, edge and expected return, and, for finished bouts, how a $1 stake settled."""
    b = bouts.copy()
    edge_a = b["p"] - b["market_p"]
    pick_a = edge_a >= min_edge
    pick_b = -edge_a >= min_edge
    b["has_pick"] = pick_a | pick_b
    b["pick"] = np.where(pick_a, b["fighter_name"], np.where(pick_b, b["opp_name"], None))
    b["pick_p"] = np.where(pick_a, b["p"], np.where(pick_b, 1 - b["p"], np.nan))
    b["pick_market_p"] = np.where(pick_a, b["market_p"], 1 - b["market_p"])
    b["price"] = np.where(pick_a, b["best_a"], np.where(pick_b, b["best_b"], np.nan))
    b["edge"] = np.where(b["has_pick"], edge_a.abs(), np.nan)
    b["expected_return"] = b["pick_p"] * b["price"] - 1

    done = b["status"] == "completed"
    pick_won = np.where(pick_a, b["won"] == 1, b["lost"] == 1)
    void = (b["draw"] == 1) | (b["no_contest"] == 1)
    settled = done & b["has_pick"]
    b["outcome"] = np.select(
        [settled & void, settled & pick_won, settled], ["Void", "Won", "Lost"], default=""
    )
    b["profit"] = np.select(
        [settled & void, settled & pick_won, settled], [0.0, b["price"] - 1, -1.0], default=np.nan
    )
    return b


def summarise(picks: pd.DataFrame) -> dict[str, float]:
    settled = picks[picks["outcome"].isin(["Won", "Lost", "Void"])]
    staked = int((settled["outcome"] != "Void").sum())
    profit = float(settled["profit"].sum())
    return {
        "picks": int(picks["has_pick"].sum()),
        "settled": len(settled),
        "won": int((settled["outcome"] == "Won").sum()),
        "profit": profit,
        "return": profit / staked if staked else math.nan,
    }
