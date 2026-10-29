from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

import duckdb


def _sql(name: str) -> str:
    return resources.files("fightdb.db").joinpath(name).read_text()


def connect(path: str | Path, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = Path(path)
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


def init_db(con: duckdb.DuckDBPyConnection) -> None:
    """Create core tables if missing. Safe to run repeatedly."""
    con.execute(_sql("schema.sql"))


@contextmanager
def open_db(path: str | Path, read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
    con = connect(path, read_only=read_only)
    try:
        if not read_only:
            init_db(con)
        yield con
    finally:
        con.close()


def table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return bool(row and row[0])
