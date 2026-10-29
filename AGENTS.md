# AGENTS.md

Rules for every coding agent (Claude, Codex, DS Flash 1017, others) and human working in this repo.

## Before you start
1. Read [docs/PRD.md](docs/PRD.md) for what we're building.
2. Find your task ID in [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md). Do **only** that task.
3. Check [docs/DECISIONS.md](docs/DECISIONS.md) for anything that affects your task.

## Hard rules
- **No secrets in git. Ever.** This is a public repo. Secrets go in `.env` (gitignored) or the hosting platform's secret store. If you add a new config variable, add it to `.env.example` with a dummy value and to `src/fightdb/config.py`.
- **Never commit** `.env`, `*.duckdb`, downloaded source files, or anything in `data/` except `data/overrides/`.
- **Don't scrape UFCStats.com.** Its pages sit behind a bot challenge, and we don't bypass bot detection. Data comes from the sources in [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).
- **Stay in your lane.** Modify only the files your task lists under "Files owned", plus their tests. Don't edit contract files (`src/fightdb/db/schema.sql`, `src/fightdb/sources/models.py`, `src/fightdb/features/stat_meta.py`) unless your task owns them. If you need a contract change, say so in your PR description and stop.
- **No network in tests.** Adapters are tested against `tests/fixtures/`. HTTP is mocked with `httpx.MockTransport`.
- **No look-ahead.** Any feature for a historical bout uses only bouts dated strictly before it.
- **Parameterised SQL only.** Never format user input into SQL strings.
- **`make check` must pass** before you finish.

## Commands (use these, not ad-hoc ones)
| Command | What it does |
|---------|--------------|
| `make setup` | Install dependencies (uv) |
| `make check` | Lint + typecheck + tests. **Must pass.** |
| `make test` | Tests only |
| `make format` | Auto-format (ruff) |
| `make backfill` | Download and import full history + upcoming cards (not in CI) |
| `make hypotheses` | Run every saved hypothesis |
| `make refresh` | Run the daily refresh once |
| `make app` | Run the Streamlit app at localhost:8501 |
| `make fixtures` | Rebuild small test fixtures from the live sources |
| `make docker-up` / `make docker-down` | Homelab stack |

## macOS note
Run Python through `make` targets, or prefix commands with `PYTHONPATH=src` (e.g. `PYTHONPATH=src uv run fightdb --help`). macOS can mark uv's editable-install `.pth` file as hidden, and Python then skips it.

## Conventions
- Python 3.12, `src/` layout, package name `fightdb`.
- Type hints everywhere. Pydantic models at module boundaries.
- Units inside the codebase: **cm, seconds, `datetime.date`**. Convert only for display.
- Source adapters take local files or parsed JSON and return models from `sources/models.py`. Downloading is done only in `sources/http.py`.
- Missing source values (`--`, blank) become `None`, never `0`.
- Tests go in `tests/` and mirror the `src/fightdb/` layout.
- Keep comments sparse. Explain *why*, not *what*.

## Git
- Branch: `task/<TASK-ID>-<short-name>`, e.g. `task/T2.3-greco-adapter`.
- One task per PR. Commit messages: `T2.3: add Greco1899 CSV adapter`.
- PR description: task ID, files changed, how you verified, open questions.
