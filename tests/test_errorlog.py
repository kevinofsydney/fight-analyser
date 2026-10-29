import json
import logging

import pytest
from typer.testing import CliRunner

from fightdb import cli, errorlog
from fightdb.config import get_settings


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/123/abcsecret")
    get_settings.cache_clear()
    return tmp_path


def _entries(log_dir):
    return errorlog.read_entries(log_dir)


def _boom():
    raise ValueError("bad row in https://discord.com/api/webhooks/123/abcsecret?token=xyz")


def test_exception_entry_has_origin_context_and_redaction(log_dir):
    errorlog.install("cli")
    with errorlog.context(run_id="r1"):
        try:
            _boom()
        except ValueError:
            logging.getLogger("fightdb.test").exception("import failed")
    (e,) = _entries(log_dir)
    assert e["exc_type"] == "ValueError" and e["context"] == {"run_id": "r1"}
    assert e["level"] == "ERROR" and e["process"] == "cli"
    assert "abcsecret" not in json.dumps(e) and "xyz" not in json.dumps(e)
    assert e["traceback"] and len(e["fingerprint"]) == 10


def test_install_is_idempotent_and_ignores_info(log_dir):
    for _ in range(3):
        errorlog.install("app")
    logging.getLogger("fightdb.test").info("fine")
    logging.getLogger("fightdb.test").warning("source is stale")
    (e,) = _entries(log_dir)
    assert e["level"] == "WARNING" and e["message"] == "source is stale"


def test_summarise_groups_repeats(log_dir):
    errorlog.install("cli")
    for _ in range(3):
        try:
            _boom()
        except ValueError:
            logging.getLogger("fightdb.test").exception("x")
    (g,) = errorlog.summarise(_entries(log_dir))
    assert g["count"] == 3


def test_cli_main_records_uncaught_exception(log_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "missing" / "nope.duckdb"))
    monkeypatch.setattr("sys.argv", ["fightdb", "db", "status"])
    with pytest.raises(Exception):  # noqa: B017 - any failure from a missing read-only DB
        cli.main()
    (e,) = _entries(log_dir)
    assert e["context"] == {"command": "db"} and e["exc_type"]

    out = CliRunner().invoke(cli.app, ["errors"]).output
    assert "x1" in out and e["fingerprint"] in out
