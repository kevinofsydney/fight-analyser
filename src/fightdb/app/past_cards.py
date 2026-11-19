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
