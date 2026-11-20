"""Does the method model improve with how a fighter wins and loses across every pro promotion?

    python scripts/method_finish_experiment.py [--db path/to/fightdb.duckdb]

The method model sees UFC-only finish and distance rates. A fighter like Terrance McKinney
(19-9 pro, no decisions ever, 14 UFC fights) has a long record outside the UFC that says more.
This adds, per side and as of the day before each bout, the share of pro wins that were
finishes and the share of pro losses that were finishes (shrunk towards the overall share),
then compares the walk-forward method model: six-way log loss and the calibration gates.
"""

from __future__ import annotations

import argparse

import duckdb
import numpy as np
import pandas as pd

from fightdb.config import get_settings
from fightdb.models import outcome

K = 5.0  # pseudo-fights of shrinkage towards the overall share


def pro_finish_shares(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Per fighter and fight date: shrunk finish share of wins and of losses over earlier pro
    fights (every promotion), as known after that date."""
    p = con.execute(
        """SELECT date, fighter_a_id AS fighter_id, result_a AS result, is_finish FROM pro_fights
           UNION ALL
           SELECT date, fighter_b_id,
                  CASE result_a WHEN 'W' THEN 'L' WHEN 'L' THEN 'W' ELSE result_a END, is_finish
           FROM pro_fights"""
    ).df()
    p = p.dropna(subset=["fighter_id", "date"])
    p["date"] = pd.to_datetime(p["date"])
    p["win"] = (p["result"] == "W").astype(float)
    p["loss"] = (p["result"] == "L").astype(float)
    p["win_fin"] = p["win"] * p["is_finish"].fillna(False).astype(float)
    p["loss_fin"] = p["loss"] * p["is_finish"].fillna(False).astype(float)
    day = p.groupby(["fighter_id", "date"], as_index=False)[
        ["win", "loss", "win_fin", "loss_fin"]
    ].sum()
    day = day.sort_values(["fighter_id", "date"])
    cum = day.groupby("fighter_id")[["win", "loss", "win_fin", "loss_fin"]].cumsum()
    day[["wins", "losses", "wins_fin", "losses_fin"]] = cum.to_numpy()
    prior_w = p["win_fin"].sum() / p["win"].sum()
    prior_l = p["loss_fin"].sum() / p["loss"].sum()
    day["pro_win_finish"] = (day["wins_fin"] + K * prior_w) / (day["wins"] + K)
    day["pro_loss_finished"] = (day["losses_fin"] + K * prior_l) / (day["losses"] + K)
    return day[["fighter_id", "date", "pro_win_finish", "pro_loss_finished"]]


def add_features(sides: pd.DataFrame, shares: pd.DataFrame) -> pd.DataFrame:
    s = sides.copy()
    look = s[["bout_id", "fighter_id", "event_date"]].copy()
    look["asof"] = pd.to_datetime(look["event_date"]) - pd.Timedelta(days=1)
    merged = pd.merge_asof(
        look.sort_values("asof"),
        shares.rename(columns={"date": "asof"}).sort_values("asof"),
        on="asof",
        by="fighter_id",
        direction="backward",
    )[["bout_id", "fighter_id", "pro_win_finish", "pro_loss_finished"]]
    s = s.merge(
        merged.rename(columns=lambda c: f"self_{c}" if c.startswith("pro_") else c),
        on=["bout_id", "fighter_id"],
        how="left",
    )
    s = s.merge(
        merged.rename(
            columns=lambda c: (
                f"opp_{c}" if c.startswith("pro_") else ("opp_id" if c == "fighter_id" else c)
            )
        ),
        on=["bout_id", "opp_id"],
        how="left",
    )
    for side in ("self", "opp"):
        for c in ("pro_win_finish", "pro_loss_finished"):
            v = s[f"{side}_{c}"].clip(0.02, 0.98)
            s[f"{side}_{c}_logit"] = np.log(v / (1 - v))
    return s


def evaluate(sides: pd.DataFrame, extra: list[str]) -> dict[str, object]:
    base = list(outcome.LEVEL_FEATURES)
    outcome.LEVEL_FEATURES[:] = base + extra
    try:
        preds, evals = outcome.walk_forward(sides)
    finally:
        outcome.LEVEL_FEATURES[:] = base
    m = evals[evals["model_name"] == outcome.METHOD_MODEL]
    recent = m[m["test_year"] >= 2020]
    return {
        "log_loss": float((m["log_loss"] * m["n"]).sum() / m["n"].sum()),
        "log_loss_2020": float((recent["log_loss"] * recent["n"]).sum() / recent["n"].sum()),
        "gates": {
            k: outcome.is_calibrated(evals, outcome.method_model_name(k)) for k in outcome.METHODS
        },
        "preds": preds,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(get_settings().db_path))
    args = ap.parse_args()
    con = duckdb.connect(args.db, read_only=True)
    try:
        sides = con.execute("SELECT * FROM bout_sides WHERE status = 'completed'").df()
        shares = pro_finish_shares(con)
    finally:
        con.close()
    sides = add_features(sides, shares)
    shares_cols = [
        f"{side}_{c}" for side in ("self", "opp") for c in ("pro_win_finish", "pro_loss_finished")
    ]
    variants = {
        "Current method model": [],
        "+ pro finish share of wins and of losses": shares_cols,
        "+ the same, as log-odds": [f"{c}_logit" for c in shares_cols],
    }
    print("| Method model | Log loss 2012+ | Log loss 2020+ | KO gate | Sub gate | Dec gate |")
    print("|---|---|---|---|---|---|")
    results = {}
    for name, extra in variants.items():
        r = evaluate(sides, extra)
        results[name] = r
        g = r["gates"]
        print(
            f"| {name} | {r['log_loss']:.4f} | {r['log_loss_2020']:.4f} | "
            + " | ".join("pass" if g[k] else "fail" for k in outcome.METHODS)
            + " |"
        )
    who = sides[sides["fighter_name"].isin(["Terrance McKinney", "Jim Miller"])]
    who = who[who["event_name"].str.startswith("UFC 334")][
        ["bout_id", "fighter_id", "fighter_name"]
    ]
    print("\nUFC 334, Miller vs McKinney (walk-forward 2026 model):\n")
    for name, r in results.items():
        p = who.merge(r["preds"], on=["bout_id", "fighter_id"])
        for row in p.itertuples():
            print(
                f"- {name}: {row.fighter_name} KO {row.p_win_ko:.0%}, sub {row.p_win_sub:.0%}, "
                f"dec {row.p_win_dec:.0%}"
            )


if __name__ == "__main__":
    main()
