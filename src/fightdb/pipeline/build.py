"""Rebuild every derived table from the core tables: snapshots, projections, models, bout_sides."""

from __future__ import annotations

import json
import logging

import duckdb
import pandas as pd

from fightdb.features import bout_sides as bs
from fightdb.features import snapshots
from fightdb.models import matchup, outcome

log = logging.getLogger(__name__)


def _write(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame) -> None:
    con.register("_df", df)
    try:
        con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM _df")
    finally:
        con.unregister("_df")


def build_all(con: duckdb.DuckDBPyConnection, train_models: bool = True) -> dict[str, int]:
    log.info("building snapshots")
    snap, priors = snapshots.build_snapshots(con)
    _write(con, "fighter_snapshots", snap)
    _write(
        con, "shrinkage_priors", pd.DataFrame({"stat": list(priors), "k": list(priors.values())})
    )

    log.info("projecting matchups")
    pairs = bs.build_bout_sides(con, snap)[["bout_id", "fighter_id", "opp_id"]]
    proj = matchup.project(snap, pairs)
    sides = bs.build_bout_sides(con, snap, proj)

    counts = {"fighter_snapshots": len(snap), "bout_sides": len(sides)}
    pred_cols = ["p_win", "p_win_ko", "p_win_sub", "p_win_dec", "p_distance"]
    if train_models and (sides["status"] == "completed").sum() > 500:
        log.info("training outcome models (walk-forward)")
        oos, evals = outcome.walk_forward(sides)
        if not evals.empty:
            con.execute("DELETE FROM model_evals")
            _insert_evals(con, evals)
        final = outcome.fit(
            sides[(sides["status"] == "completed") & (sides["year"] >= outcome.TRAIN_FROM_YEAR)]
        )
        upcoming = (
            final.predict(sides[sides["status"] == "scheduled"])
            if (sides["status"] == "scheduled").any()
            else pd.DataFrame()
        )
        preds = pd.concat([oos, upcoming], ignore_index=True)
        if not preds.empty:
            preds = preds.drop_duplicates(["bout_id", "fighter_id"], keep="last")
            proj = proj.merge(
                preds[["bout_id", "fighter_id", *pred_cols]],
                on=["bout_id", "fighter_id"],
                how="left",
            )
            sides = sides.merge(
                preds[["bout_id", "fighter_id", *pred_cols]].rename(
                    columns={c: f"self_{c}" for c in pred_cols}
                ),
                on=["bout_id", "fighter_id"],
                how="left",
            )
        counts["model_eval_years"] = len(evals)

    proj["computed_at"] = pd.Timestamp.now(tz="UTC").tz_localize(None)
    _write(con, "matchup_projections", proj)
    _write(con, "bout_sides", sides)
    con.execute(
        "CREATE OR REPLACE VIEW upcoming_bout_sides AS SELECT * FROM bout_sides WHERE status = 'scheduled'"
    )
    con.execute(
        "CREATE OR REPLACE VIEW completed_bout_sides AS SELECT * FROM bout_sides WHERE status = 'completed'"
    )
    return counts


def _insert_evals(con: duckdb.DuckDBPyConnection, evals: pd.DataFrame) -> None:
    cols = [r[0] for r in con.execute("DESCRIBE model_evals").fetchall() if r[0] != "created_at"]
    df = evals.reindex(columns=cols)
    con.register("_ev", df)
    try:
        con.execute(
            f"INSERT INTO model_evals ({', '.join(cols)}) SELECT {', '.join(cols)} FROM _ev"
        )
    finally:
        con.unregister("_ev")


def summary(con: duckdb.DuckDBPyConnection) -> str:
    row = con.execute(
        """SELECT count(*), avg(brier), avg(brier_market), avg(brier_model_on_market_rows), avg(accuracy)
           FROM model_evals"""
    ).fetchone()
    return json.dumps(
        {
            "years": row[0],
            "brier": row[1],
            "brier_market": row[2],
            "brier_model_same_rows": row[3],
            "accuracy": row[4],
        }
        if row
        else {}
    )
