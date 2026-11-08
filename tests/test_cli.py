import duckdb
from typer.testing import CliRunner

from fightdb.cli import app
from fightdb.config import get_settings
from fightdb.db.conn import init_db
from fightdb.pipeline import upsert
from fightdb.pipeline.build import build_all
from tests.conftest import synthetic_history

GOOD = """id: good
title: Older fighter
condition:
  - feature: diff_age_years
    op: ">"
    value: 0
"""
NEEDS_NEW_COLUMN = """id: newer
title: Short notice
condition:
  - feature: self_days_notice
    op: "<"
    value: 14
"""


def test_run_all_reports_failures_and_keeps_going(tmp_path, monkeypatch):
    db = tmp_path / "f.duckdb"
    con = duckdb.connect(str(db))
    init_db(con)
    upsert.upsert_history(con, synthetic_history(n_bouts=120))
    build_all(con, train_models=False)
    # A database built before `days_notice` existed.
    con.execute("ALTER TABLE bout_sides DROP COLUMN self_days_notice")
    con.close()
    hyp = tmp_path / "hyp"
    hyp.mkdir()
    (hyp / "a_newer.yaml").write_text(NEEDS_NEW_COLUMN, encoding="utf-8")
    (hyp / "b_good.yaml").write_text(GOOD, encoding="utf-8")
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("HYPOTHESES_DIR", str(hyp))
    get_settings.cache_clear()

    result = CliRunner().invoke(app, ["hypothesis", "run-all"])
    assert result.exit_code == 1
    assert "[a_newer.yaml] Short notice\n  ERROR:" in result.output
    assert "[b_good.yaml] Older fighter" in result.output  # evaluated after the failure
    assert "1 hypotheses failed: a_newer.yaml" in result.output
