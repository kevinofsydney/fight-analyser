"""Store safely matched upcoming bookmaker quotes: the latest poll plus every poll's history."""

from datetime import UTC, datetime

import duckdb

from fightdb.sources.identity import name_key
from fightdb.sources.odds_api import OddsEvent


def init_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS upcoming_odds (
        bout_id VARCHAR NOT NULL, fighter_id VARCHAR NOT NULL,
        bookmaker_key VARCHAR NOT NULL, bookmaker VARCHAR NOT NULL,
        decimal_odds DOUBLE NOT NULL, updated_at TIMESTAMP, fetched_at TIMESTAMP NOT NULL,
        PRIMARY KEY (bout_id, fighter_id, bookmaker_key))""")
    # upcoming_odds keeps only the latest poll; this keeps every poll so line movement can be shown.
    con.execute("""CREATE TABLE IF NOT EXISTS upcoming_odds_history (
        bout_id VARCHAR NOT NULL, fighter_id VARCHAR NOT NULL,
        bookmaker_key VARCHAR NOT NULL, bookmaker VARCHAR NOT NULL,
        decimal_odds DOUBLE NOT NULL, updated_at TIMESTAMP, fetched_at TIMESTAMP NOT NULL,
        PRIMARY KEY (bout_id, fighter_id, bookmaker_key, fetched_at))""")
    con.execute("""INSERT INTO upcoming_odds_history
        SELECT * FROM upcoming_odds ON CONFLICT DO NOTHING""")


def reserve_daily_poll(con: duckdb.DuckDBPyConnection, now: datetime) -> bool:
    """Reserve this UTC day before making a paid request, even if it fails or returns no data."""
    con.execute("""CREATE TABLE IF NOT EXISTS upcoming_odds_polls (
        poll_date DATE PRIMARY KEY, attempted_at TIMESTAMP NOT NULL)""")
    if con.execute(
        "SELECT 1 FROM upcoming_odds_polls WHERE poll_date = ?", [now.date()]
    ).fetchone():
        return False
    # Existing databases may contain today's prices from before the daily guard existed.
    if con.execute(
        "SELECT 1 FROM upcoming_odds WHERE CAST(fetched_at AS DATE) = ? LIMIT 1", [now.date()]
    ).fetchone():
        return False
    con.execute("INSERT INTO upcoming_odds_polls VALUES (?, ?)", [now.date(), now])
    return True


def store(con: duckdb.DuckDBPyConnection, events: list[OddsEvent], fetched_at: datetime) -> int:
    """Match both names and date exactly; ambiguous and unmatched listings are ignored."""
    init_table(con)
    bouts = con.execute("""SELECT b.bout_id, b.fighter_a_id, b.fighter_b_id, e.date
        FROM bouts b JOIN events e USING (event_id)
        WHERE b.status = 'scheduled' AND e.status = 'upcoming'""").fetchall()
    names: dict[str, set[str]] = {}
    for fighter_id, name in (
        con.execute("""SELECT fighter_id, name FROM fighters
            UNION SELECT fighter_id, name FROM fighter_aliases WHERE name IS NOT NULL""").fetchall()
    ):
        if name:
            names.setdefault(fighter_id, set()).add(name_key(name))
    rows = []
    for event in events:
        if event.commence_time.tzinfo is None:
            continue
        if event.commence_time.astimezone(UTC).replace(tzinfo=None) <= fetched_at:
            continue
        home, away = name_key(event.home_team), name_key(event.away_team)
        if not home or not away or home == away:
            continue
        candidates = []
        for bout_id, a, b, day in bouts:
            if day is None or abs((day - event.commence_time.date()).days) > 1:
                continue
            if home in names.get(a, set()) and away in names.get(b, set()):
                candidates.append((bout_id, {home: a, away: b}))
            elif home in names.get(b, set()) and away in names.get(a, set()):
                candidates.append((bout_id, {home: b, away: a}))
        if len(candidates) != 1:
            continue
        bout_id, fighter_ids = candidates[0]
        for book in event.bookmakers:
            for market in book.markets:
                if market.key != "h2h":
                    continue
                quotes = {name_key(q.name): q.price for q in market.outcomes}
                if set(quotes) != {home, away} or len(market.outcomes) != 2:
                    continue
                updated = market.last_update or book.last_update
                updated = (
                    updated.astimezone(UTC).replace(tzinfo=None)
                    if updated and updated.tzinfo
                    else None
                )
                for name, fighter_id in fighter_ids.items():
                    rows.append(
                        (
                            bout_id,
                            fighter_id,
                            book.key,
                            book.title,
                            quotes[name],
                            updated,
                            fetched_at,
                        )
                    )
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("DELETE FROM upcoming_odds")
        if rows:
            con.executemany(
                "INSERT OR REPLACE INTO upcoming_odds VALUES (?, ?, ?, ?, ?, ?, ?)", rows
            )
            con.executemany(
                "INSERT OR REPLACE INTO upcoming_odds_history VALUES (?, ?, ?, ?, ?, ?, ?)", rows
            )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return len(rows)
