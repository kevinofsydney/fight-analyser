"""Momentum: does a fighter's last 1, 2 or 3 results predict their next fight?

    python scripts/momentum_experiment.py [--db path/to/fightdb.duckdb]

Prints Markdown tables. Part 1 is descriptive: how often fighters with each recent-results
pattern won, next to what the model (out of sample) and the closing line expected for them.
Part 2 asks whether momentum adds anything once quality is known: the walk-forward win model
with and without momentum inputs, with a paired bootstrap interval on the log-loss change.
Every input uses only bouts dated before the one being predicted.
"""

from __future__ import annotations

import argparse
import math

import duckdb
import numpy as np
import pandas as pd

from fightdb.config import get_settings
from fightdb.models import outcome

PATTERNS = {
    1: ["W", "L"],
    2: ["WW", "WL", "LW", "LL"],
    3: ["WWW", "WWL", "WLW", "LWW", "WLL", "LWL", "LLW", "LLL"],
}
SCORE = {"W": 1, "L": -1}


def _history(results: pd.DataFrame, key: str) -> pd.DataFrame:
    """Per (fight, fighter): the fighter's previous three results, newest first, from fights
    dated strictly before this one. `results` has fighter_id, date, `key`, result."""
    r = results.sort_values(["fighter_id", "date", key]).reset_index(drop=True)
    g = r.groupby("fighter_id")["result"]
    for k in (1, 2, 3):
        r[f"prev{k}"] = g.shift(k)
    # Same-day fights (old tournaments) must not see each other.
    for k in (1, 2, 3):
        r.loc[r.groupby("fighter_id")["date"].shift(k) == r["date"], f"prev{k}"] = None
    for k in (1, 2, 3):
        cols = [f"prev{i}" for i in range(1, k + 1)]
        ok = r[cols].isin(["W", "L"]).all(axis=1)
        r[f"last{k}"] = np.where(ok, r[cols].fillna("").agg("".join, axis=1), None)
    net = sum(r[f"prev{i}"].map(SCORE).fillna(0) for i in (1, 2, 3))
    r["net3"] = net
    r["n3"] = r[["prev1", "prev2", "prev3"]].notna().sum(axis=1)
    return r


def _wilson(k: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return math.nan, math.nan
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def _load(db: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = duckdb.connect(db, read_only=True)
    try:
        sides = con.execute("SELECT * FROM bout_sides WHERE status = 'completed'").df()
        pro = con.execute(
            """SELECT fight_id, date, fighter_a_id AS fighter_id, result_a AS result
               FROM pro_fights
               UNION ALL
               SELECT fight_id, date, fighter_b_id,
                      CASE result_a WHEN 'W' THEN 'L' WHEN 'L' THEN 'W' ELSE result_a END
               FROM pro_fights"""
        ).df()
    finally:
        con.close()
    return sides, pro


def add_momentum(sides: pd.DataFrame, pro: pd.DataFrame) -> pd.DataFrame:
    s = sides.copy()
    s["date"] = pd.to_datetime(s["event_date"])
    ufc = _history(s[["bout_id", "fighter_id", "date", "result"]], "bout_id")
    s = s.merge(
        ufc[["bout_id", "fighter_id", "last1", "last2", "last3", "net3", "n3"]],
        on=["bout_id", "fighter_id"],
    )
    opp = ufc[["bout_id", "fighter_id", "net3", "prev1"]].rename(
        columns={"fighter_id": "opp_id", "net3": "opp_net3", "prev1": "opp_prev1"}
    )
    s = s.merge(opp, on=["bout_id", "opp_id"], how="left")
    s["self_last1_score"] = s["last1"].map(SCORE).fillna(0)
    s["opp_last1_score"] = s["opp_prev1"].map(SCORE).fillna(0)
    s["diff_last1"] = s["self_last1_score"] - s["opp_last1_score"]
    s["diff_net3"] = s["net3"] - s["opp_net3"].fillna(0)

    # All-promotion version: net of the last three pro fights before the bout day, so
    # debutants have a history too. pro_fights includes the UFC bouts.
    p = pro.dropna(subset=["fighter_id", "date"]).copy()
    p["date"] = pd.to_datetime(p["date"])
    p = p.sort_values(["fighter_id", "date", "fight_id"])
    p["score"] = p["result"].map(SCORE).fillna(0)
    p["net_incl"] = p.groupby("fighter_id")["score"].transform(
        lambda x: x.rolling(3, min_periods=1).sum()
    )
    p = p.groupby(["fighter_id", "date"], as_index=False)["net_incl"].last()
    look = s[["bout_id", "fighter_id", "opp_id", "date"]].copy()
    look["asof"] = look["date"] - pd.Timedelta(days=1)
    look = pd.merge_asof(
        look.sort_values("asof"),
        p.rename(columns={"date": "asof"}).sort_values("asof"),
        on="asof",
        by="fighter_id",
        direction="backward",
    )
    pro_net = look[["bout_id", "fighter_id", "net_incl"]]
    s = s.merge(pro_net, on=["bout_id", "fighter_id"], how="left").merge(
        pro_net.rename(columns={"fighter_id": "opp_id", "net_incl": "opp_net_incl"}),
        on=["bout_id", "opp_id"],
        how="left",
    )
    s["diff_pro_net3"] = s["net_incl"].fillna(0) - s["opp_net_incl"].fillna(0)
    return s


def descriptive(s: pd.DataFrame) -> str:
    d = s[(s["year"] >= outcome.FIRST_TEST_YEAR) & ((s["won"] + s["lost"]) == 1)]
    lines = [
        "| Last results (newest first) | Fighters | Won | 95% interval | Model expected "
        "| Bookmakers expected | Won, priced bouts |",
        "|---|---|---|---|---|---|---|",
    ]
    for k, pats in PATTERNS.items():
        for pat in pats:
            g = d[d[f"last{k}"] == pat]
            n = len(g)
            if n < 30:
                continue
            won = g["won"].sum()
            lo, hi = _wilson(won, n)
            m = g[g["self_implied_prob"].notna()]
            lines.append(
                f"| {pat} | {n:,} | {won / n:.1%} | {lo:.1%}–{hi:.1%} | "
                f"{g['self_p_win'].mean():.1%} | "
                f"{m['self_implied_prob'].mean():.1%} | {m['won'].mean():.1%} |"
            )
    return "\n".join(lines)


def _walk_forward_losses(decided: pd.DataFrame, feats: list[str]) -> pd.Series:
    """Per-bout out-of-sample log loss (A side), indexed by bout_id."""
    out = []
    for y in sorted(int(v) for v in decided["year"].unique()):
        if y < outcome.FIRST_TEST_YEAR:
            continue
        train = decided[decided["year"] < y]
        test = decided[(decided["year"] == y) & decided["is_a"]]
        if train.empty or test.empty:
            continue
        ws, win = outcome._fit_win(train, feats)
        p = win.predict_proba(ws.transform(outcome._matrix(test, feats)))[:, 1]
        p = np.clip(p, 1e-6, 1 - 1e-6)
        yt = test["won"].to_numpy(dtype=float)
        loss = -(yt * np.log(p) + (1 - yt) * np.log(1 - p))
        hit = (p > 0.5) == (yt == 1)
        out.append(pd.DataFrame({"bout_id": test["bout_id"], "year": y, "loss": loss, "hit": hit}))
    return pd.concat(out).set_index("bout_id")


def _boot(diff: np.ndarray, reps: int = 2000, seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    means = [diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(reps)]
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def model_test(s: pd.DataFrame) -> str:
    done = s[s["year"] >= outcome.TRAIN_FROM_YEAR]
    decided = outcome._decided(outcome.add_matchup_diffs(done))
    current = [f for f in outcome.DIFF_FEATURES if f in decided.columns]
    no_streak = [f for f in current if f != "diff_streak"]
    variants = {
        "Current model (has `diff_streak`)": current,
        "Without any momentum input": no_streak,
        "Streak replaced by last result": [*no_streak, "diff_last1"],
        "Streak replaced by net of last 3": [*no_streak, "diff_net3"],
        "Current + net of last 3": [*current, "diff_net3"],
        "Current + net of last 3 pro fights (all promotions)": [*current, "diff_pro_net3"],
        "Streak replaced by net of last 3 pro fights": [*no_streak, "diff_pro_net3"],
    }
    base = _walk_forward_losses(decided, current)
    lines = [
        "| Model | Log loss 2012+ | Log loss 2020+ | Accuracy 2012+ | Change vs current, 2012+ "
        "(95% interval) |",
        "|---|---|---|---|---|",
    ]
    for name, feats in variants.items():
        r = base if feats is current else _walk_forward_losses(decided, feats)
        recent = r["year"] >= 2020
        diff = (r["loss"] - base["loss"]).to_numpy()
        lo, hi = _boot(diff)
        change = "—" if feats is current else f"{diff.mean():+.4f} [{lo:+.4f}, {hi:+.4f}]"
        lines.append(
            f"| {name} | {r['loss'].mean():.4f} | {r.loc[recent, 'loss'].mean():.4f} | "
            f"{r['hit'].mean():.1%} | {change} |"
        )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(get_settings().db_path))
    args = ap.parse_args()
    sides, pro = _load(args.db)
    s = add_momentum(sides, pro)
    print("## Win rate by recent results (UFC bouts only, 2012+)\n")
    print(descriptive(s))
    print("\n## Does momentum add to the win model? (walk-forward)\n")
    print(model_test(s))


if __name__ == "__main__":
    main()
