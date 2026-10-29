"""`fightdb` command line. Run `fightdb --help`."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from fightdb.config import get_settings

app = typer.Typer(
    help="Fight Analyser: UFC database, hypothesis lab and matchup projections.",
    no_args_is_help=True,
)
db_app = typer.Typer(help="Database utilities.", no_args_is_help=True)
hyp_app = typer.Typer(help="Run saved hypotheses.", no_args_is_help=True)
model_app = typer.Typer(help="Outcome models.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(hyp_app, name="hypothesis")
app.add_typer(model_app, name="model")


@app.callback()
def _main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _print(obj: object) -> None:
    typer.echo(json.dumps(obj, indent=2, default=str))


@app.command()
def backfill(
    source: Annotated[
        str | None, typer.Option(help="fight_canon | greco (default: HISTORY_SOURCE)")
    ] = None,
    file: Annotated[
        Path | None, typer.Option(help="Use an already-downloaded canon.db or Greco CSV folder")
    ] = None,
    no_cards: Annotated[
        bool, typer.Option("--no-cards", help="Skip fetching upcoming cards")
    ] = False,
) -> None:
    """Download and import full fight history, then build all derived tables."""
    from fightdb.pipeline.refresh import run_refresh

    _print(
        run_refresh(get_settings(), kind="backfill", source=source, file=file, cards=not no_cards)
    )


@app.command()
def refresh(
    no_history: Annotated[
        bool, typer.Option("--no-history", help="Only refresh upcoming cards")
    ] = False,
    no_models: Annotated[
        bool, typer.Option("--no-models", help="Skip retraining outcome models")
    ] = False,
) -> None:
    """The daily job: new results, upcoming cards, card changes, projections."""
    from fightdb.pipeline.refresh import run_refresh

    _print(run_refresh(get_settings(), history=not no_history, train_models=not no_models))


@app.command()
def build(no_models: Annotated[bool, typer.Option("--no-models")] = False) -> None:
    """Rebuild derived tables (snapshots, projections, models) from the core tables."""
    from fightdb.db.conn import open_db
    from fightdb.pipeline.build import build_all

    with open_db(get_settings().db_path) as con:
        _print(build_all(con, train_models=not no_models))


@app.command()
def serve(port: int = 8501) -> None:
    """Start the Streamlit app."""
    home = Path(__file__).parent / "app" / "Home.py"
    raise SystemExit(
        subprocess.call(
            [sys.executable, "-m", "streamlit", "run", str(home), "--server.port", str(port)]
        )
    )


@db_app.command("init")
def db_init() -> None:
    """Create an empty database with the core schema."""
    from fightdb.db.conn import open_db

    path = get_settings().db_path
    with open_db(path):
        pass
    typer.echo(f"initialised {path}")


@db_app.command("status")
def db_status() -> None:
    """Row counts and the last few refresh runs."""
    from fightdb.db.conn import open_db

    with open_db(get_settings().db_path, read_only=True) as con:
        tables = [
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables ORDER BY 1"
            ).fetchall()
        ]
        counts = {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}  # type: ignore[index]
        runs = con.execute(
            "SELECT run_id, kind, status, started_at, source, source_version, error FROM scrape_runs ORDER BY started_at DESC LIMIT 5"
        ).fetchall()
    _print({"tables": counts, "recent_runs": runs})


@hyp_app.command("run")
def hypothesis_run(
    path: Path, show_sweep: Annotated[bool, typer.Option("--sweep")] = False
) -> None:
    """Evaluate one hypothesis YAML file."""
    from fightdb.analytics import hypothesis, schema
    from fightdb.db.conn import open_db

    h = schema.load_hypothesis(path)
    with open_db(get_settings().db_path, read_only=True) as con:
        r = hypothesis.evaluate(con, h, keep_bouts=False)
        typer.echo(f"{h.title}\n  {r.verdict}")
        for w in r.warnings:
            typer.echo(f"  ! {w}")
        upcoming = hypothesis.matches_upcoming(con, h)
        if not upcoming.empty:
            typer.echo("  Applies to upcoming:")
            for row in upcoming.itertuples():
                typer.echo(
                    f"    - {row.fighter_name} vs {row.opp_name} ({row.event_name}, {row.event_date:%Y-%m-%d})"
                )
        if show_sweep and h.sweep:
            typer.echo(hypothesis.sweep(con, h).round(3).to_string(index=False))


@hyp_app.command("run-all")
def hypothesis_run_all() -> None:
    """Evaluate every hypothesis in the hypotheses/ folder."""
    from fightdb.analytics import hypothesis, schema
    from fightdb.db.conn import open_db

    s = get_settings()
    with open_db(s.db_path, read_only=True) as con:
        for path, h in schema.load_all(s.hypotheses_dir):
            r = hypothesis.evaluate(con, h, keep_bouts=False)
            typer.echo(f"[{path.name}] {h.title}\n  {r.verdict}")


@model_app.command("eval")
def model_eval() -> None:
    """Show walk-forward calibration results."""
    from fightdb.db.conn import open_db

    with open_db(get_settings().db_path, read_only=True) as con:
        df = con.execute(
            """SELECT test_year, n, round(brier, 4) AS brier, round(brier_model_on_market_rows, 4) AS brier_same_rows,
                      round(brier_market, 4) AS brier_market, n_market, round(accuracy, 3) AS accuracy
               FROM model_evals ORDER BY test_year"""
        ).df()
    typer.echo(df.to_string(index=False))


if __name__ == "__main__":
    app()
