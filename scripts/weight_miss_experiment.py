"""Weight misses: do fighters who miss weight lose more, and does a history of missing predict it?

    python scripts/weight_miss_experiment.py [--db path/to/fightdb.duckdb]

Prints Markdown. Needs the `weight_misses` table (loaded from data/overrides by every refresh).
Coverage starts in 2018, so only bouts from 2018 on are used, and a fighter's earlier misses are
those on cards dated strictly before the bout. Part 1: results for fighters who missed weight
for that fight, against the model (which doesn't know) and the bookmakers (whose closing price
does). Part 2: the same for fighters with one or more earlier misses. Part 3: flat bets on their
opponents at the best closing price. Part 4: both as inputs to the walk-forward win model (live
recency weights), scored on 2019+ bouts.
"""

from __future__ import annotations

import argparse

import duckdb
import numpy as np
import pandas as pd
from activity_experiment import _walk_forward
from momentum_experiment import _boot

from fightdb.config import get_settings
from fightdb.models import outcome

FROM = pd.Timestamp("2018-01-01")


def _load(db: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = duckdb.connect(db, read_only=True)
    try:
        sides = con.execute("SELECT * FROM bout_sides WHERE status = 'completed'").df()
        misses = con.execute(
            "SELECT card_date, fighter_id, bout_id, pounds_over FROM weight_misses "
            "WHERE record_type = 'official_miss' AND fighter_id IS NOT NULL"
        ).df()
    finally:
        con.close()
    return sides, misses


def add_features(sides: pd.DataFrame, misses: pd.DataFrame) -> pd.DataFrame:
    s = sides.copy()
    s["event_date"] = pd.to_datetime(s["event_date"])
    linked = misses[misses["bout_id"].notna()]
    missed = set(zip(linked["bout_id"], linked["fighter_id"], strict=True))
    s["self_missed_this"] = [
        float((b, f) in missed) for b, f in zip(s["bout_id"], s["fighter_id"], strict=True)
    ]
    m = misses.assign(card_date=pd.to_datetime(misses["card_date"]))
    by_f = {f: np.sort(g["card_date"].to_numpy()) for f, g in m.groupby("fighter_id")}
    s["self_prior_misses"] = [
        float(np.searchsorted(by_f[f], d, side="left")) if f in by_f else 0.0
        for f, d in zip(s["fighter_id"], s["event_date"].to_numpy(), strict=True)
    ]
    s["self_any_prior_miss"] = (s["self_prior_misses"] > 0).astype(float)
    cols = ["self_missed_this", "self_prior_misses", "self_any_prior_miss"]
    opp = s[["bout_id", "fighter_id", *cols]].rename(
        columns={"fighter_id": "opp_key", **{c: c.replace("self_", "opp_") for c in cols}}
    )
    s["opp_key"] = s.groupby("bout_id")["fighter_id"].transform(lambda x: x.iloc[::-1].to_numpy())
    s = s.merge(opp, on=["bout_id", "opp_key"], how="left").drop(columns="opp_key")
    for c in cols:
        base = c.removeprefix("self_")
        s[f"diff_{base}"] = s[c] - s[f"opp_{base}"]
    return s


def _row(label: str, g: pd.DataFrame) -> str:
    m = g[g["self_implied_prob"].notna()]
    gap = g["won"].mean() - g["self_p_win"].mean()
    se = np.sqrt((g["self_p_win"] * (1 - g["self_p_win"])).sum()) / len(g)
    mgap = m["won"].mean() - m["self_implied_prob"].mean()
    mse = np.sqrt((m["self_implied_prob"] * (1 - m["self_implied_prob"])).sum()) / max(len(m), 1)
    return (
        f"| {label} | {len(g):,} | {g['won'].mean():.1%} | {g['self_p_win'].mean():.1%} | "
        f"{100 * gap:+.1f} ± {196 * se:.1f} | {len(m):,} | {m['self_implied_prob'].mean():.1%} | "
        f"{m['won'].mean():.1%} | {100 * mgap:+.1f} ± {196 * mse:.1f} |"
    )


def tables(s: pd.DataFrame) -> str:
    d = s[(s["event_date"] >= FROM) & ((s["won"] + s["lost"]) == 1) & s["self_p_win"].notna()]
    head = (
        "| Fighters | n | Won | Model expected | Gap (pts) | Priced | Bookmakers expected | "
        "Won, priced | Gap vs bookmakers (pts) |\n|---|---|---|---|---|---|---|---|---|"
    )
    this = d["self_missed_this"] == 1
    opp_this = d["opp_missed_this"] == 1
    prior = d["self_prior_misses"]
    out = [
        "### Missed weight for this fight\n",
        head,
        _row("Missed weight", d[this]),
        _row("Opponent missed weight", d[opp_this]),
        _row("Neither missed", d[~this & ~opp_this]),
        "\n### Earlier misses (cards before this one)\n",
        head,
        _row("No earlier miss", d[prior == 0]),
        _row("1 earlier miss", d[prior == 1]),
        _row("2+ earlier misses", d[prior >= 2]),
        _row("Earlier miss, made weight this time", d[(prior >= 1) & ~this]),
        _row("Earlier miss and missed again", d[(prior >= 1) & this]),
    ]
    return "\n".join(out)


def betting(s: pd.DataFrame, db: str) -> str:
    """Flat stake on the opponent of a weight misser at the best closing price (optimistic: the
    best price across books), with a bootstrap interval on the return."""
    con = duckdb.connect(db, read_only=True)
    try:
        o = con.execute(
            "SELECT bout_id, fighter_id, close_high FROM odds WHERE close_high IS NOT NULL"
        ).df()
    finally:
        con.close()
    am = o["close_high"]
    o["dec"] = np.where(am > 0, 1 + am / 100, 1 + 100 / am.abs())
    d = s[(s["event_date"] >= FROM) & ((s["won"] + s["lost"]) == 1)].merge(
        o[["bout_id", "fighter_id", "dec"]], on=["bout_id", "fighter_id"]
    )
    angles = {
        "Every fighter (baseline)": d,
        "Opponent of a fighter who missed weight": d[d["opp_missed_this"] == 1],
        "Opponent of a past misser who missed again": d[
            (d["opp_missed_this"] == 1) & (d["opp_prior_misses"] >= 1)
        ],
        "Opponent of a past misser who made weight": d[
            (d["opp_prior_misses"] >= 1) & (d["opp_missed_this"] == 0)
        ],
        "Opponent of a 2+ time misser": d[d["opp_prior_misses"] >= 2],
    }
    lines = [
        "| Back | Bets | Won | Average price | Return (95% interval) |",
        "|---|---|---|---|---|",
    ]
    rng = np.random.default_rng(0)
    for name, g in angles.items():
        pnl = np.where(g["won"] == 1, g["dec"] - 1, -1.0)
        boot = [pnl[rng.integers(0, len(pnl), len(pnl))].mean() for _ in range(4000)]
        lines.append(
            f"| {name} | {len(g):,} | {g['won'].mean():.1%} | {g['dec'].mean():.2f} | "
            f"{pnl.mean():+.1%} [{np.percentile(boot, 2.5):+.1%}, "
            f"{np.percentile(boot, 97.5):+.1%}] |"
        )
    return "\n".join(lines)


def model_test(s: pd.DataFrame) -> str:
    done = s[s["year"] >= outcome.TRAIN_FROM_YEAR]
    decided = outcome._decided(outcome.add_matchup_diffs(done))
    current = [f for f in outcome.DIFF_FEATURES if f in decided.columns]
    variants = {
        "Current model": current,
        "+ missed weight for this fight": [*current, "diff_missed_this"],
        "+ number of earlier misses": [*current, "diff_prior_misses"],
        "+ any earlier miss": [*current, "diff_any_prior_miss"],
        "+ this fight and earlier misses": [*current, "diff_missed_this", "diff_prior_misses"],
    }
    base = _walk_forward(decided, current)
    base = base[base["year"] >= 2019]
    lines = [
        "| Model | Log loss 2019+ | Accuracy 2019+ | Change vs current (95% interval) |",
        "|---|---|---|---|",
    ]
    for name, feats in variants.items():
        r = base if feats is current else _walk_forward(decided, feats).loc[base.index]
        diff = (r["loss"] - base["loss"]).to_numpy()
        lo, hi = _boot(diff)
        change = "—" if feats is current else f"{diff.mean():+.4f} [{lo:+.4f}, {hi:+.4f}]"
        lines.append(f"| {name} | {r['loss'].mean():.4f} | {r['hit'].mean():.1%} | {change} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(get_settings().db_path))
    args = ap.parse_args()
    sides, misses = _load(args.db)
    s = add_features(sides, misses)
    print("## Results against the model and the bookmakers (2018+)\n")
    print(tables(s))
    print("\n## Betting against weight missers at the best closing price (2018+)\n")
    print(betting(s, args.db))
    print("\n## Walk-forward win model with weight-miss inputs (recency-weighted, as live)\n")
    print(model_test(s))


if __name__ == "__main__":
    main()
