import duckdb
import pytest

from fightdb.config import Settings
from fightdb.db.conn import init_db
from fightdb.pipeline import backfill
from tests.conftest import FIXTURES

TABLES = ["events", "fighters", "bouts", "round_stats", "odds"]


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=tmp_path / "f.duckdb", download_dir=tmp_path / "dl", _env_file=None)


def _counts(con):
    return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}


@pytest.mark.parametrize(("source", "path"), [("fight_canon", "canon_mini.db"), ("greco", "greco")])
def test_import_from_file_is_idempotent(settings, source, path):
    con = duckdb.connect(":memory:")
    init_db(con)
    bundle, _, warnings = backfill.import_history(
        con, settings, source=source, file=FIXTURES / path
    )
    assert bundle.bouts and not warnings
    first = _counts(con)
    assert first["bouts"] > 0 and first["round_stats"] > 0
    backfill.import_history(con, settings, source=source, file=FIXTURES / path)
    assert _counts(con) == first


def test_failover_to_second_source(settings, monkeypatch):
    def broken(client, s):
        raise RuntimeError("release missing")

    def greco_from_fixture(client, s):
        return backfill.Fetched("greco", FIXTURES / "greco", "abc", None)

    monkeypatch.setattr(backfill, "fetch_fight_canon", broken)
    monkeypatch.setattr(backfill, "fetch_greco", greco_from_fixture)
    con = duckdb.connect(":memory:")
    init_db(con)
    bundle, _, warnings = backfill.import_history(con, settings, client=object())  # type: ignore[arg-type]
    assert bundle.source == "greco"
    assert any("fight_canon failed" in w for w in warnings)
    assert any("fallback" in w for w in warnings)
