"""Idempotent bulk upserts from source-neutral records into DuckDB."""

from __future__ import annotations

from collections.abc import Sequence

import duckdb
import pandas as pd
from pydantic import BaseModel

from fightdb.sources.models import HistoryBundle

_KEYS = {
    "events": ["event_id"],
    "fighters": ["fighter_id"],
    "fighter_aliases": ["source", "source_id"],
    "bouts": ["bout_id"],
    "round_stats": ["bout_id", "fighter_id", "round"],
    "odds": ["bout_id", "fighter_id"],
}
# Columns that keep their original value when a row is updated.
_PRESERVE = {"bouts": {"first_seen_at", "replaces_bout_id"}}


def records_to_df(records: Sequence[BaseModel]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in records])


def upsert_df(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    keys = _KEYS[table]
    df = df.drop_duplicates(subset=keys, keep="last")
    table_cols = [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
    cols = [c for c in df.columns if c in table_cols]
    now_cols = [c for c in ("updated_at", "last_seen_at") if c in table_cols and c not in cols]
    updates = [
        f"{c} = excluded.{c}"
        for c in cols
        if c not in keys and c not in _PRESERVE.get(table, set())
    ]
    updates += [f"{c} = now()::TIMESTAMP" for c in now_cols]
    con.register("_upsert_df", df[cols])
    try:
        con.execute(
            f"""INSERT INTO {table} ({", ".join(cols)})
                SELECT {", ".join(cols)} FROM _upsert_df
                ON CONFLICT ({", ".join(keys)}) DO UPDATE SET {", ".join(updates)}"""
        )
    finally:
        con.unregister("_upsert_df")
    return len(df)


def upsert_records(con: duckdb.DuckDBPyConnection, table: str, records: Sequence[BaseModel]) -> int:
    return upsert_df(con, table, records_to_df(records))


def upsert_history(con: duckdb.DuckDBPyConnection, bundle: HistoryBundle) -> dict[str, int]:
    """Write a whole history import. Fighters already known keep values the new source lacks."""
    counts = {
        "events": upsert_records(con, "events", bundle.events),
        "fighters": _upsert_fighters(con, bundle),
        "fighter_aliases": upsert_records(con, "fighter_aliases", bundle.aliases),
        "bouts": upsert_records(con, "bouts", bundle.bouts),
        "round_stats": upsert_records(con, "round_stats", bundle.rounds),
        "odds": upsert_records(con, "odds", bundle.odds),
    }
    return counts


def _upsert_fighters(con: duckdb.DuckDBPyConnection, bundle: HistoryBundle) -> int:
    df = records_to_df(bundle.fighters)
    if df.empty:
        return 0
    existing = con.execute("SELECT * FROM fighters").df()
    if not existing.empty:
        merged = df.merge(existing, on="fighter_id", how="left", suffixes=("", "_old"))
        for col in df.columns:
            old = f"{col}_old"
            if old in merged.columns:
                merged[col] = merged[col].where(merged[col].notna(), merged[old])
        df = merged[df.columns]
    return upsert_df(con, "fighters", df)
