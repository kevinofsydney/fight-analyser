import duckdb
import pytest

from fightdb.config import Settings
from fightdb.pipeline import quality, upsert
from fightdb.pipeline.build import build_all
from fightdb.pipeline.refresh import run_refresh
from tests.conftest import FIXTURES, synthetic_history
from tests.pipeline.test_refresh import TODAY, _no_network, _upcoming

S = Settings(_env_file=None)


@pytest.fixture
def built(con):
    upsert.upsert_history(con, synthetic_history(n_bouts=60))
    build_all(con, train_models=False)
    return con


def test_clean_database_passes(built):
    assert quality.check_quality(built, quality.table_counts(built), S) == []


def test_bout_without_two_fighters_fails(built):
    built.execute("UPDATE bouts SET fighter_b_id = fighter_a_id WHERE bout_id = 'b0'")
    assert any("two different fighters" in f for f in quality.check_quality(built, None, S))


def test_shrinking_counts_fail(built):
    before = quality.table_counts(built)
    built.execute("DELETE FROM fighter_snapshots WHERE bout_id = 'b1'")
    failures = quality.check_quality(built, before, S)
    assert any("completed_snapshots fell" in f for f in failures)


def test_missing_reach_fails(built):
    built.execute("UPDATE events SET date = date + INTERVAL 10 YEAR")
    assert quality.check_quality(built, None, S) == []
    built.execute("UPDATE fighters SET reach_cm = NULL")
    assert any("no reach" in f for f in quality.check_quality(built, None, S))


def test_missing_round_stats_fail(built):
    built.execute("DELETE FROM round_stats WHERE bout_id IN ('b0', 'b1', 'b2', 'b3', 'b4')")
    assert any("round stats" in f for f in quality.check_quality(built, None, S))


def test_quality_failure_keeps_old_database(tmp_path):
    ok = Settings(
        db_path=tmp_path / "fightdb.duckdb",
        download_dir=tmp_path / "dl",
        overrides_dir=tmp_path / "overrides",
        _env_file=None,
    )
    kwargs = dict(
        file=FIXTURES / "canon_mini.db",
        client=_no_network(),
        fetch_upcoming=_upcoming(),
        today=TODAY,
        train_models=False,
    )
    run_refresh(ok, kind="backfill", **kwargs)
    con = duckdb.connect(str(ok.db_path), read_only=True)
    snap = con.execute("SELECT count(*) FROM fighter_snapshots").fetchone()
    con.close()

    strict = ok.model_copy(update={"dq_min_stats_coverage": 1.01})
    with pytest.raises(RuntimeError, match="data quality"):
        run_refresh(strict, **kwargs)

    con = duckdb.connect(str(ok.db_path), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM fighter_snapshots").fetchone() == snap
        last = con.execute(
            "SELECT status, error FROM scrape_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    assert last[0] == "failed" and "data quality" in last[1]
    assert not ok.db_path.with_suffix(".tmp.duckdb").exists()
