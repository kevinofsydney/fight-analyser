"""Data-quality gate run at the end of every refresh, before the new database is swapped in."""

from __future__ import annotations

import duckdb

from fightdb.config import Settings

# Counts that must never go down between runs. Upserts never delete bouts, so a drop means a
# broken build (e.g. snapshots computed from an empty or truncated table).
GUARDED_COUNTS = {
    "completed_bouts": "SELECT count(*) FROM bouts WHERE status = 'completed'",
    # Scheduled bouts come and go with card changes, so only completed bouts are guarded.
    "completed_snapshots": """SELECT count(*) FROM fighter_snapshots s
                              JOIN bouts b ON b.bout_id = s.bout_id
                              WHERE b.status = 'completed'""",
}


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> float | None:
    row = con.execute(sql).fetchone()
    return None if row is None else row[0]


def table_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    out = {}
    for name, sql in GUARDED_COUNTS.items():
        try:
            out[name] = int(_scalar(con, sql) or 0)
        except duckdb.CatalogException:
            out[name] = 0
    return out


def check_quality(
    con: duckdb.DuckDBPyConnection, previous_counts: dict[str, int] | None, settings: Settings
) -> list[str]:
    """Return a list of failure messages; empty means the database is fit to publish."""
    failures: list[str] = []

    bad_pairs = _scalar(
        con,
        """SELECT count(*) FROM bouts
           WHERE fighter_a_id IS NULL OR fighter_b_id IS NULL OR fighter_a_id = fighter_b_id""",
    )
    if bad_pairs:
        failures.append(f"{bad_pairs} bouts without two different fighters")

    now = table_counts(con)
    for name, before in (previous_counts or {}).items():
        if now.get(name, 0) < before:
            failures.append(f"{name} fell from {before} to {now.get(name, 0)}")

    null_reach = _scalar(
        con,
        """WITH recent AS (
             SELECT fighter_a_id AS f FROM bouts JOIN events USING (event_id)
              WHERE bouts.status = 'completed' AND events.date >= DATE '2015-01-01'
             UNION
             SELECT fighter_b_id FROM bouts JOIN events USING (event_id)
              WHERE bouts.status = 'completed' AND events.date >= DATE '2015-01-01')
           SELECT avg(CASE WHEN reach_cm IS NULL THEN 1.0 ELSE 0.0 END)
           FROM fighters WHERE fighter_id IN (SELECT f FROM recent)""",
    )
    if null_reach is not None and null_reach > settings.dq_max_null_reach:
        failures.append(
            f"{null_reach:.0%} of fighters since 2015 have no reach (limit {settings.dq_max_null_reach:.0%})"
        )

    coverage = _scalar(
        con,
        """SELECT avg(CASE WHEN n = 2 THEN 1.0 ELSE 0.0 END) FROM (
             SELECT b.bout_id, count(DISTINCT r.fighter_id) AS n
             FROM bouts b JOIN events e USING (event_id)
             LEFT JOIN round_stats r ON r.bout_id = b.bout_id
             WHERE b.status = 'completed' AND e.date >= DATE '2010-01-01'
             GROUP BY b.bout_id)""",
    )
    if coverage is not None and coverage < settings.dq_min_stats_coverage:
        failures.append(
            f"only {coverage:.0%} of completed bouts since 2010 have round stats for both fighters "
            f"(limit {settings.dq_min_stats_coverage:.0%})"
        )
    return failures
