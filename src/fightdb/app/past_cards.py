"""Scoring finished cards: who the model and the bookmakers picked, and who won."""

from __future__ import annotations

import numpy as np
import pandas as pd

PAST_BOUTS_SQL = """
SELECT a.bout_id, a.event_id, a.event_name, a.event_date, a.card_order, a.weight_class,
       a.is_title, a.scheduled_rounds, a.fighter_id, a.fighter_name, a.opp_id, a.opp_name,
       a.won, a.lost, a.draw, a.no_contest, a.method, a.method_group, a.end_round,
       a.self_p_win AS p,
       a.self_p_win_ko AS a_ko, a.self_p_win_sub AS a_sub, a.self_p_win_dec AS a_dec,
       b.self_p_win_ko AS b_ko, b.self_p_win_sub AS b_sub, b.self_p_win_dec AS b_dec
FROM bout_sides a
JOIN bout_sides b ON b.bout_id = a.bout_id AND b.fighter_id = a.opp_id
WHERE a.is_a AND a.status = 'completed' AND a.self_p_win IS NOT NULL
"""

_ENDINGS = {"a_ko": "KO/TKO", "a_sub": "submission", "a_dec": "decision"}
_ENDINGS |= {k.replace("a_", "b_"): v for k, v in _ENDINGS.items()}
_ACTUAL = {"ko_tko": "ko", "sub": "sub", "dec": "dec"}


def score(bouts: pd.DataFrame) -> pd.DataFrame:
    """Adds, per bout: whether it had a winner, each side's pick and whether it was right, the
    chance the model gave the eventual winner, and the model's likeliest of the six endings.

    Expects the columns of `PAST_BOUTS_SQL` and, optionally, `market_p` (the bookmakers'
    margin-free chance for the A side)."""
    b = bouts.copy()
    b["market_p"] = pd.to_numeric(b.get("market_p"), errors="coerce") if "market_p" in b else np.nan
    b["decisive"] = (b["won"] + b["lost"]) == 1
    a_won = b["won"] == 1
    b["winner"] = np.where(b["decisive"], np.where(a_won, b["fighter_name"], b["opp_name"]), None)
    b["model_pick"] = np.where(b["p"] >= 0.5, b["fighter_name"], b["opp_name"])
    b["model_pick_p"] = np.maximum(b["p"], 1 - b["p"])
    b["model_right"] = np.where(b["decisive"], (b["p"] >= 0.5) == a_won, np.nan)
    has_m = b["market_p"].notna()
    b["market_pick"] = np.where(
        has_m, np.where(b["market_p"] >= 0.5, b["fighter_name"], b["opp_name"]), None
    )
    b["market_pick_p"] = np.maximum(b["market_p"], 1 - b["market_p"])
    b["market_right"] = np.where(b["decisive"] & has_m, (b["market_p"] >= 0.5) == a_won, np.nan)
    b["p_winner"] = np.where(b["decisive"], np.where(a_won, b["p"], 1 - b["p"]), np.nan)
    b["market_p_winner"] = np.where(
        b["decisive"] & has_m, np.where(a_won, b["market_p"], 1 - b["market_p"]), np.nan
    )

    cols = list(_ENDINGS)
    known = b[cols].notna().all(axis=1)
    likeliest = b[cols].astype(float).fillna(0.0).idxmax(axis=1)
    b["model_ending"] = np.where(
        known,
        np.where(likeliest.str.startswith("a_"), b["fighter_name"], b["opp_name"])
        + " by "
        + likeliest.map(_ENDINGS),
        None,
    )
    actual = np.where(a_won, "a_", "b_") + b["method_group"].map(_ACTUAL).fillna("other")
    b["ending_right"] = np.where(
        b["decisive"] & known & ~pd.Series(actual, index=b.index).str.endswith("other"),
        likeliest == actual,
        np.nan,
    )
    return b


def summary(scored: pd.DataFrame) -> dict[str, float]:
    """Totals over a set of scored bouts. Brier scores are on the A side of decisive bouts;
    the bookmakers' numbers only cover bouts with a price."""
    d = scored[scored["decisive"]]
    y = (d["won"] == 1).to_numpy(dtype=float)
    p = d["p"].to_numpy(dtype=float)
    m = d["market_p"].notna().to_numpy()
    mp = d.loc[m, "market_p"].to_numpy(dtype=float)
    return {
        "bouts": len(scored),
        "decisive": len(d),
        "model_right": float(d["model_right"].sum()),
        "with_market": int(m.sum()),
        "market_right": float(d["market_right"].sum()),
        "model_brier": float(np.mean((p - y) ** 2)) if len(d) else np.nan,
        "model_brier_on_market": float(np.mean((p[m] - y[m]) ** 2)) if m.any() else np.nan,
        "market_brier": float(np.mean((mp - y[m]) ** 2)) if m.any() else np.nan,
        "endings": int(d["ending_right"].notna().sum()),
        "ending_right": float(d["ending_right"].sum()),
    }


def by_card(scored: pd.DataFrame) -> pd.DataFrame:
    """One row per event, newest first."""
    rows = []
    for (event_id, name, date), g in scored.groupby(
        ["event_id", "event_name", "event_date"], sort=False
    ):
        s = summary(g)
        rows.append(
            {
                "event_id": event_id,
                "event_name": name,
                "event_date": date,
                "bouts": s["bouts"],
                "model": f"{s['model_right']:.0f} / {s['decisive']}",
                "model_pct": s["model_right"] / s["decisive"] if s["decisive"] else np.nan,
                "market": f"{s['market_right']:.0f} / {s['with_market']}"
                if s["with_market"]
                else "—",
                "market_pct": s["market_right"] / s["with_market"] if s["with_market"] else np.nan,
                "model_brier": s["model_brier"],
                "market_brier": s["market_brier"],
            }
        )
    out = pd.DataFrame(rows)
    return out.sort_values("event_date", ascending=False).reset_index(drop=True) if rows else out


FLAT_STAKE = 100.0


def add_bets(scored: pd.DataFrame, *, bankroll: float, kelly_fraction: float) -> pd.DataFrame:
    """Two ways of betting the model, on bouts with a price for both fighters.

    Flat: `FLAT_STAKE` on the model's pick at the best price. Edge-sized: Kelly staking, i.e.
    back whichever fighter the model thinks is underpriced, with a stake that grows with the
    edge: `kelly_fraction` × (p × price − 1) / (price − 1) of a fixed `bankroll`. The bankroll
    doesn't compound, so one bad year can't wipe out later ones. Draws and no contests refund.
    Expects `best_a` and `best_b` (decimal odds) alongside the columns `score` adds."""
    b = scored.copy()
    for col in ("best_a", "best_b"):
        b[col] = pd.to_numeric(b[col], errors="coerce") if col in b else np.nan
    priced = b["best_a"].notna() & b["best_b"].notna() & b["p"].notna()
    void = (b["draw"] == 1) | (b["no_contest"] == 1)
    settled = priced & (b["decisive"] | void)

    def profit(stake: pd.Series, back_a: pd.Series, price: pd.Series) -> pd.Series:
        won = np.where(back_a, b["won"] == 1, b["lost"] == 1)
        out = np.where(void, 0.0, np.where(won, stake * (price - 1), -stake))
        return pd.Series(np.where(settled & (stake > 0), out, np.nan), index=b.index)

    flat_a = b["p"] >= 0.5
    flat_price = b["best_a"].where(flat_a, b["best_b"])
    b["flat_stake"] = np.where(settled, FLAT_STAKE, 0.0)
    b["flat_profit"] = profit(b["flat_stake"], flat_a, flat_price)

    kelly_a = (b["p"] * b["best_a"] - 1) / (b["best_a"] - 1)
    kelly_b = ((1 - b["p"]) * b["best_b"] - 1) / (b["best_b"] - 1)
    edge_a = kelly_a >= kelly_b
    f = np.maximum(kelly_a.where(edge_a, kelly_b), 0.0).fillna(0.0)
    b["edge_pick"] = np.where(f > 0, np.where(edge_a, b["fighter_name"], b["opp_name"]), None)
    b["edge_price"] = np.where(f > 0, b["best_a"].where(edge_a, b["best_b"]), np.nan)
    b["edge_stake"] = np.where(settled, (bankroll * kelly_fraction * f).round(2), 0.0)
    b["edge_profit"] = profit(b["edge_stake"], edge_a, b["best_a"].where(edge_a, b["best_b"]))
    return b


def betting_summary(bets: pd.DataFrame) -> pd.DataFrame:
    """Bets, amount staked, profit and return per $ staked for both ways of betting."""
    rows = []
    for name, prefix in (("$100 on every pick", "flat"), ("Sized by edge", "edge")):
        placed = bets[bets[f"{prefix}_profit"].notna()]
        staked = float(placed[f"{prefix}_stake"].sum())
        made = float(placed[f"{prefix}_profit"].sum())
        rows.append(
            {
                "Strategy": name,
                "Bets": len(placed),
                "Staked": staked,
                "Profit": made,
                "Return": made / staked if staked else np.nan,
            }
        )
    return pd.DataFrame(rows)
