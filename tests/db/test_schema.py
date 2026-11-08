import duckdb

from fightdb.db.conn import init_db, open_db, table_exists

CORE_TABLES = [
    "schema_version",
    "events",
    "fighters",
    "fighter_aliases",
    "bouts",
    "round_stats",
    "odds",
    "card_changes",
    "scrape_runs",
    "model_evals",
]


def test_init_creates_core_tables(tmp_path):
    with open_db(tmp_path / "x.duckdb") as con:
        for t in CORE_TABLES:
            assert table_exists(con, t), t


def test_init_is_idempotent(tmp_path):
    path = tmp_path / "x.duckdb"
    with open_db(path) as con:
        con.execute(
            "INSERT INTO events (event_id, name, date, status, source) VALUES ('e1', 'E', '2020-01-01', 'completed', 'test')"
        )
    with open_db(path) as con:
        init_db(con)
        assert con.execute("SELECT count(*) FROM events").fetchone() == (1,)
        assert con.execute("SELECT count(*) FROM schema_version").fetchone() == (1,)


def test_read_only_connection_refuses_writes(tmp_path):
    path = tmp_path / "x.duckdb"
    with open_db(path):
        pass
    with open_db(path, read_only=True) as con:
        try:
            con.execute("CREATE TABLE t (x INT)")
        except duckdb.Error:
            return
    raise AssertionError("write succeeded on a read-only connection")
