# Build Plan

How to build Fight Analyser in small tasks that different coding agents (Claude, Codex, DS Flash 1017, or a human) can pick up independently.

Read first: [PRD.md](PRD.md) (what), [../AGENTS.md](../AGENTS.md) (rules), [DECISIONS.md](DECISIONS.md) (open choices). This plan assumes the **recommended defaults** in DECISIONS.md. If a decision changes, update the affected tasks before handing them out.

---

## How this plan works

- **Phases run in order.** Tasks *inside* a phase marked **∥** can run in parallel, because they touch different files.
- **Contracts come first.** Phase 1 fixes the database schema, the source-neutral models and the stat metadata. After that, source adapters, features, analytics, models and UI can be built in parallel against those contracts.
- **We don't scrape.** History comes from the fight-canon-db release (fallback: Greco1899 CSVs) and upcoming cards from ESPN JSON. See [DATA_SOURCES.md](DATA_SOURCES.md).
- **Every task has the same shape:** ID, depends-on, files owned, steps, acceptance criteria, verify command. An agent should touch **only** the files its task owns (plus tests for them). If it needs to change a contract file, it stops and raises that in its PR description instead.
- **Difficulty tags** help route work: 🟢 mechanical / well-specified (any agent), 🟡 moderate judgement, 🔴 needs careful reasoning (give to your strongest agent and review closely).
- **Definition of done for every task:** `make check` passes (lint + typecheck + tests), new code has tests, no network access in tests, no secrets, and the PR description lists what changed and how it was verified.

### Handing a task to an agent

Copy this into the agent's prompt:

```
You are working in the fight-analyser repo. Read AGENTS.md, then docs/PRD.md sections relevant to your task.
Your task is <TASK ID> from docs/BUILD_PLAN.md. Do exactly that task.
- Only modify the files listed under "Files owned" (plus their tests).
- Do not change contract files (src/fightdb/db/schema.sql, src/fightdb/sources/models.py,
  src/fightdb/features/stat_meta.py) unless your task owns them.
- Run `make check` before finishing; it must pass.
- Do not add secrets. Do not make network calls in tests.
- Work on a branch named task/<TASK ID>-<short-name> and open a PR to main.
When done, reply with: files changed, how you verified, anything left unresolved.
```

---

## Phase 0: Scaffolding (one agent, sequential)

### T0.1 Project skeleton 🟢
- **Depends on:** nothing
- **Files owned:** `pyproject.toml`, `uv.lock`, `Makefile`, `src/fightdb/__init__.py`, `src/fightdb/config.py`, `src/fightdb/cli.py`, `tests/test_smoke.py`, `.python-version`
- **Steps:**
  1. `uv init --package --name fightdb`, Python 3.12, src layout.
  2. Add deps: `httpx tenacity duckdb pydantic pydantic-settings typer pyyaml scipy statsmodels scikit-learn rapidfuzz streamlit altair pandas`.
  3. Add dev deps: `pytest pytest-cov ruff mypy types-PyYAML pre-commit`.
  4. `config.py`: a `Settings` class (pydantic-settings) reading `.env` with the defaults from `.env.example`.
  5. `cli.py`: a typer app with stub commands `backfill`, `refresh`, `hypothesis`, `db`, `serve` that print "not implemented".
  6. `Makefile` targets (exact names; agents rely on them):
     `setup`, `lint`, `format`, `typecheck`, `test`, `check` (= lint + typecheck + test), `backfill`, `refresh`, `app`, `docker-build`, `docker-up`, `docker-down`, `fixtures`.
- **Acceptance:** `make setup && make check` passes on a clean clone. `uv run fightdb --help` lists the commands.

### T0.2 CI and secret scanning 🟢
- **Depends on:** T0.1
- **Files owned:** `.github/workflows/ci.yml`, `.pre-commit-config.yaml`, `.gitleaks.toml`
- **Steps:** a CI workflow on push/PR that runs `uv sync --frozen`, `make check`, and `gitleaks/gitleaks-action`. Pre-commit runs ruff and gitleaks.
- **Acceptance:** CI is green on the PR. Committing a fake `AKIA...` key in a test branch makes CI fail (verify, then delete the branch).

> `.gitignore`, `.env.example`, `AGENTS.md` and `CLAUDE.md` already exist in the repo. Keep them up to date; don't recreate them.

---

## Phase 1: Contracts (one agent, sequential; review carefully)

These files are the shared language every other task depends on. Get them reviewed before Phase 2 starts.

### T1.1 Database schema and connection 🟡
- **Depends on:** T0.1
- **Files owned:** `src/fightdb/db/schema.sql`, `src/fightdb/db/conn.py`, `src/fightdb/db/__init__.py`, `tests/db/test_schema.py`
- **Steps:** implement the tables in PRD §6.2 exactly (`events`, `fighters`, `bouts`, `bout_fighter_stats`, `round_stats`, `fighter_snapshots`, `fighter_aliases`, `event_aliases`, `bout_aliases`, `odds`, `matchup_projections`, `model_evals`, `card_changes`, `scrape_runs`). Add a `schema_version` table. `conn.py` provides `connect(path, read_only=False)` and `init_db(path)`, which is idempotent.
- **Acceptance:** `uv run fightdb db init` creates `data/fightdb.duckdb`. Running it twice is harmless. Tests create a DB in `tmp_path` and assert every table and column exists.

### T1.2 Source-neutral models 🟢
- **Depends on:** T0.1
- **Files owned:** `src/fightdb/sources/models.py`, `src/fightdb/sources/units.py`, `tests/sources/test_models.py`
- **Steps:** Pydantic models that every adapter returns and the pipeline consumes: `Event`, `Bout` (with result fields optional for upcoming bouts), `RoundStats`, `Fighter`, `OddsLine`, `UpcomingEvent`, plus `SourceRef(source, source_id)` on each so aliases can be recorded. Units are normalised here: heights and reaches in **cm**, times in **seconds**, dates as `date`. `units.py` has helpers: `parse_height("5' 11\"")`, `inches_to_cm(72)`, `parse_x_of_y("24 of 47")`, `parse_mmss("4:54")`, `null_if_dash("--")`. Add `division_from_weight_class(str) -> Division` covering every UFC weight-class string seen in fight-canon, Greco and ESPN ("UFC Featherweight Title Bout", "Women's Strawweight Bout", "W Flyweight", "Catch Weight", "Open Weight", …).
- **Acceptance:** tests cover unit conversion (`5' 11"` → 180.3 cm, `72` in → 182.9 cm, `--` → None, `"24 of 47"` → (24, 47)) and the weight-class mapping, with every distinct weight-class string from the fixtures.

### T1.3 Stat metadata 🟢
- **Depends on:** T0.1
- **Files owned:** `src/fightdb/features/stat_meta.py`, `tests/features/test_stat_meta.py`
- **Steps:** one dict/list describing every comparable stat: `key`, `label`, `group` (Physical/Record/Activity/Striking/Grappling), `unit`, `higher_is_better: bool | None`, `format` (e.g. `"{:.0%}"`), `shrinkable: bool` (rate stats with a numerator/denominator, used by T5.3) and `description`. This drives the tale of the tape (FR-24) **and** the hypothesis form's feature dropdown.
- **Acceptance:** every snapshot feature in PRD FR-13 has an entry. A test checks there are no duplicate keys.

### T1.4 Source fixtures 🟢
- **Depends on:** T0.1
- **Files owned:** `scripts/make_fixtures.py`, `tests/fixtures/**`
- **Steps:** a script (`make fixtures`) that builds small, committed fixtures from the real sources:
  - `tests/fixtures/canon_mini.db`: a SQLite extract of fight-canon-db with the same schema, containing ~3 UFC events (one modern title card, one pre-2005 event with missing stats, one with a draw and a no contest), their fighters, round stats, odds and aliases, plus one non-UFC event (to test filtering). Keep it under 1 MB.
  - `tests/fixtures/greco/*.csv`: the same events' rows from each Greco1899 CSV (keep the header row).
  - `tests/fixtures/espn/scoreboard.json` and `scoreboard_dates_<date>.json`: saved ESPN responses, plus a hand-edited copy with a replaced fighter and a cancelled bout (for card-diff tests).
- **Acceptance:** fixtures are committed (public data, no secrets). The script is the only place in the repo, other than `sources/http.py`, that makes network calls.

---

## Phase 2: Source adapters (∥ parallel, one agent per task)

Adapters take a local file or parsed JSON and **yield models from T1.2**. Downloading is separate (T2.1), so adapters are pure and testable offline.

| Task | Adapter | Files owned | Tag |
|------|---------|-------------|-----|
| T2.1 ∥ | Download client: `download_release_asset(repo, pattern, dest)` (latest GitHub release, via the public `releases/latest/download/` URL, no token), `download_raw(url, dest)`, `get_json(url)`. Retries with backoff (tenacity), descriptive User-Agent, ETag / If-Modified-Since so unchanged files aren't re-downloaded. Returns the source version (release tag / ETag / commit SHA). | `src/fightdb/sources/http.py`, `tests/sources/test_http.py` (use `httpx.MockTransport`) | 🟢 |
| T2.2 ∥ | **fight-canon-db** → models. Open `canon.db` read-only, filter `events.promotion = 'UFC'`, map `fights`/`fight_participants`/`round_stats`/`fighters`/`odds`/`*_aliases`. Inches → cm. Use the UFCStats alias as the primary external ID. Don't use `corner` before 2010-03-21 (it leaks the winner). | `src/fightdb/sources/fight_canon.py` + test | 🟡 |
| T2.3 ∥ | **Greco1899 CSVs** → the same models. Derive IDs from `fighter-details/<id>`, `fight-details/<id>` and `event-details/<id>` URLs. Join `ufc_fight_stats` (fighter names) to fighter IDs via the bout's two fighters. Strip trailing spaces (the EVENT column has them). | `src/fightdb/sources/greco.py` + test | 🟡 |
| T2.4 ∥ | **ESPN scoreboard** → `list[UpcomingEvent]`. Read `leagues[0].calendar` for future events, fetch `?dates=YYYYMMDD` for each, map competitions → bouts (ESPN athlete IDs, names, records, weight class, card order, status). Validate strictly: unknown shapes raise an error. | `src/fightdb/sources/espn.py` + test | 🟢 |
| T2.5 ∥ | **Identity resolution** (ESPN fighter → our `fighter_id`): normalise names (accents, case, punctuation, "Jr."), exact match + division → `rapidfuzz` match ≥ 90 cross-checked against the W-L record → `data/overrides/fighter_identity.yaml`. Returns `(fighter_id or None, method, score)`. Writes `fighter_aliases(source='espn')` rows once matched. | `src/fightdb/sources/identity.py`, `data/overrides/fighter_identity.yaml` + test | 🟡 |
| T2.6 ∥ | **Cross-source consistency check**: given fight-canon and Greco data for a date range, report mismatched results and missing bouts. Used for the failover decision and a weekly report. | `src/fightdb/sources/crosscheck.py` + test | 🟢 |

- **Acceptance (each):** handles every relevant fixture from T1.4. Tests assert specific known values (e.g. a named fighter's sig strikes landed in round 2 of a named bout, the result method, the reach in cm). Missing values become `None`, not errors or 0. **T2.2 and T2.3 must produce identical models for the same fixture events** (a shared test enforces this).

---

## Phase 3: Ingestion pipeline

### T3.1 Upsert layer 🟡
- **Depends on:** T1.1, T1.2
- **Files owned:** `src/fightdb/pipeline/upsert.py`, `tests/pipeline/test_upsert.py`
- **Steps:** functions `upsert_event`, `upsert_fighter`, `upsert_bout`, `upsert_fight_stats`, taking models from T1.2. Use DuckDB `INSERT ... ON CONFLICT DO UPDATE`. Maintain `first_seen_at`, `last_seen_at`, `updated_at`.
- **Acceptance:** inserting the same model twice leaves one row. Changed fields update. Tests use an in-memory or `tmp_path` DB.

### T3.2 Backfill / import command 🟡
- **Depends on:** T2.1–T2.3, T3.1
- **Files owned:** `src/fightdb/pipeline/backfill.py`, CLI wiring in `cli.py` (backfill command only), `tests/pipeline/test_backfill.py`
- **Steps:** download the history source chosen by `HISTORY_SOURCE` (default `fight_canon`), run the adapter, upsert everything in one transaction, and write a `scrape_runs` row with `source` and `source_version`. Flags: `--source fight_canon|greco`, `--since YYYY-MM-DD`, `--file PATH` (use an already-downloaded file).
- **Acceptance:** with fixtures, backfill populates all tables from either source, and re-running inserts nothing new. A real `make backfill` completes in under 10 minutes (manual check, not in CI).

---

## Phase 4: Derived data (can start once T3.1 exists; ∥ between T4.1 and T4.3)

### T4.1 Point-in-time fighter snapshots 🔴
- **Depends on:** T1.1, T1.3, T3.1
- **Files owned:** `src/fightdb/features/snapshots.py`, `tests/features/test_snapshots.py`
- **Steps:** for every bout and each fighter, compute FR-13 features using **only bouts with an earlier date** (same-day bouts excluded). Prefer one SQL pass with window functions over a Python loop. Rate stats are per 15 minutes of UFC cage time; percentages are landed/attempted totals (not averages of per-fight percentages). Also compute snapshots for **scheduled** bouts using all completed history.
- **Acceptance:**
  - Hand-built mini dataset (5 fighters, ~10 bouts) with expected values computed by hand in the test.
  - **Leakage test:** for every snapshot, no contributing bout has `date >= bout.date`.
  - A debut fighter gets `ufc_fights_before = 0` and NULL rates.
  - Full rebuild on the real dataset takes under 60 s.

### T4.2 `bout_sides` and `upcoming_bout_sides` views 🟡
- **Depends on:** T4.1
- **Files owned:** `src/fightdb/db/views.sql`, `tests/db/test_views.py`
- **Steps:** implement FR-15. Columns: `bout_id, event_date, division, sex, is_title, scheduled_rounds, fighter_id, opp_id, self_<feature>, opp_<feature>, diff_<feature>` for every numeric feature, plus `won, lost, draw, no_contest, method_group ('ko_tko'|'sub'|'dec'|'dq'|'other'), end_round, went_distance`.
- **Acceptance:** every completed bout produces exactly 2 rows. `won` is true in exactly one row for decisive bouts. `diff_x` = −(the other row's `diff_x`).

### T4.3 Card diff 🟡 ∥
- **Depends on:** T1.1, T1.2
- **Files owned:** `src/fightdb/pipeline/card_diff.py`, `tests/pipeline/test_card_diff.py`
- **Steps:** `diff_cards(stored: list[Bout], scraped: list[Bout]) -> list[CardChange]` covering every change type in FR-8. Match bouts by source bout ID first, then by unordered fighter pair, then by "same event + one shared fighter" (replacement). Pure function; the caller writes results.
- **Acceptance:** a table-driven test with one case per change type, plus a "no change" case that returns an empty list.

---

## Phase 5: Analytics (∥ T5.1 and T5.2)

### T5.1 Stats helpers 🟢 ∥
- **Depends on:** T0.1
- **Files owned:** `src/fightdb/analytics/stats.py`, `tests/analytics/test_stats.py`
- **Steps:** `wilson_ci(k, n, alpha=0.05)`, `binomial_test(k, n, p0)`, `two_proportion_test(k1, n1, k2, n2)` (fall back to Fisher's exact for small counts), `describe_difference(rate, baseline)` → both percentage points and relative %.
- **Acceptance:** results match `scipy` / `statsmodels` reference values for known inputs. Edge cases: n = 0, k = 0, k = n.

### T5.2 Hypothesis engine 🔴 ∥
- **Depends on:** T4.2 (can start against a fixture DB with the view schema), T5.1
- **Files owned:** `src/fightdb/analytics/hypothesis.py`, `src/fightdb/analytics/schema.py` (pydantic model of the YAML in PRD §6.3), `tests/analytics/test_hypothesis.py`, `hypotheses/examples/*.yaml`
- **Steps:**
  1. Pydantic model for the YAML. Validate that `feature` names exist in `stat_meta` / `bout_sides` columns. Validate `op` ∈ `{>, >=, <, <=, ==, !=, between}`.
  2. Compile to **parameterised** SQL against `bout_sides` (never string-format user values into SQL).
  3. Return a `HypothesisResult`: n, k, rate, CI, baseline, pp diff, relative diff, p-value, verdict string, excluded-for-missing-data count, matching bout IDs.
  4. `compare_to` modes: `baseline_50`, `complement`, `division_rate`.
  5. Sweep mode returns a list of results across thresholds.
  6. `matches_upcoming(hypothesis) -> list[bout_id]` evaluates the condition against `upcoming_bout_sides`.
  7. CLI: `fightdb hypothesis run <file.yaml>` prints the result. `fightdb hypothesis run-all` runs everything in `hypotheses/`.
- **Acceptance:** on a synthetic DB with a planted effect (e.g. the older fighter wins 70% of 500 bouts), the engine reports ~70% with p < 0.001. With no planted effect it reports ~50% and isn't significant. Includes 3 example YAMLs (age gap, reach gap, takedown-defence gap).

---

## Phase 5b: Ratings and matchup models (the "xG" layer)

Spec: [ANALYSIS_IDEAS.md](ANALYSIS_IDEAS.md) §1–3 and PRD §4.6. All of these work on point-in-time data from T4.1.

### T5.3 Shrinkage (empirical Bayes) 🟡 ∥
- **Depends on:** T4.1
- **Files owned:** `src/fightdb/models/shrinkage.py`, `tests/models/test_shrinkage.py`
- **Steps:** for each rate stat in `stat_meta` flagged `shrinkable`, estimate prior strength `k` per division (method of moments on a beta-binomial, or a closed-form fit), then add `<stat>_shrunk` to snapshots. Store the fitted `k` values in a small table for display.
- **Acceptance:** on simulated data with a known true rate, shrunk estimates have lower mean squared error than raw ones. A fighter with 4/4 TDD gets a shrunk value close to the division mean.

### T5.4 Ratings and Elo 🔴 ∥
- **Depends on:** T4.1, T5.3
- **Files owned:** `src/fightdb/models/ratings.py`, `tests/models/test_ratings.py`
- **Steps:** the FR-29 indices (100 = division average, opponent-adjusted), plus Elo (or Glicko-2) with a finish bonus, all computed walking forward in date order and stored per bout in snapshots.
- **Acceptance:** no leakage (a test like T4.1's). Elo alone predicts winners better than a coin flip on held-out years. Indices are around 100 on average per division-year.

### T5.5 Matchup projections 🟡
- **Depends on:** T5.3, T5.4
- **Files owned:** `src/fightdb/models/matchup.py`, `tests/models/test_matchup.py`
- **Steps:** the odds-ratio combination (ANALYSIS_IDEAS §1.1) for xTD, xCTRL, xSS and xKD per round, against division-era baselines. Write to `matchup_projections` for upcoming bouts **and** historical bouts (needed to evaluate them). Each projection keeps its inputs for FR-32 explanations.
- **Acceptance:** the worked example in ANALYSIS_IDEAS §1.1 (45% vs 50% TDD at a 36.3% baseline) gives 58.9% and 3.5 TD/15. Over historical bouts, the projected xTD totals correlate with actual takedowns (report r on held-out years).

### T5.6 Outcome models and calibration 🔴
- **Depends on:** T5.4, T5.5
- **Files owned:** `src/fightdb/models/outcome.py`, `src/fightdb/models/evaluate.py`, `tests/models/test_outcome.py`
- **Steps:** a logistic model for win probability and a multinomial model for method (A-KO, A-Sub, A-Dec, B-KO, B-Sub, B-Dec) on symmetric rating differentials. Train walk-forward by year. Write `model_evals` (Brier, log loss, reliability bins) including the closing-line benchmark, using the vig-free implied probability from `odds`. CLI: `fightdb model train`, `fightdb model eval`.
- **Acceptance:** the model beats the naive baseline on held-out years, and the reliability chart's bins fall within ±5 pp of the diagonal where n ≥ 100. The report includes the closing-line comparison whichever way it goes. Probabilities are symmetric: P(A wins) + P(B wins) = 1, and swapping A and B mirrors the outputs.

---

## Phase 6: UI (Streamlit; ∥ one agent per page after T6.1)

### T6.1 App shell and data access 🟡
- **Depends on:** T4.2, T5.2
- **Files owned:** `src/fightdb/app/Home.py`, `src/fightdb/app/data.py` (cached read-only queries), `src/fightdb/app/components.py`
- **Steps:** multipage app. `data.py` opens DuckDB **read-only** and wraps queries in `st.cache_data` with a TTL. On startup, if `DATA_RELEASE_URL` is set and the local DB is missing or older than `DB_MAX_AGE_HOURS`, download it (free-hosting path). Sidebar: last refreshed time, plus a stale warning if over 36 h. Optional `APP_PASSWORD` gate.
- **Acceptance:** `make app` starts and shows the Upcoming page against a backfilled DB.

| Task | Page | Files owned | Tag |
|------|------|-------------|-----|
| T6.2 ∥ | **Upcoming** (home content): events → bouts list, card changes last 7 days, link to bout page | `app/pages/1_Upcoming.py` | 🟢 |
| T6.3 ∥ | **Bout / tale of the tape**: side-by-side stats from `stat_meta` (raw + shrunk), advantage highlight and margin, reach diff in cm + in, index bars (A's offence vs B's defence), matchup projections (xTD, xSS, xCTRL, win and method probabilities with "low/med/high" vs baseline and an expandable "show the maths"), matching saved hypotheses, low-data badge, common opponents | `app/pages/2_Bout.py` | 🟡 |
| T6.4 ∥ | **Hypothesis Lab**: form (population, condition builder with feature dropdown from `stat_meta`, outcome, compare_to), result card, sweep chart with CI band, drill-down table, "copy as YAML" button, session test counter | `app/pages/3_Hypothesis_Lab.py` | 🟡 |
| T6.5 ∥ | **Saved Hypotheses**: table of `hypotheses/**/*.yaml` with latest results and upcoming matches | `app/pages/4_Saved_Hypotheses.py` | 🟢 |
| T6.6 ∥ | **Fighter**: profile, fight history, snapshot trends chart | `app/pages/5_Fighter.py` | 🟢 |
| T6.7 ∥ | **SQL Console**: read-only connection, row limit 5,000, timeout, schema reference panel | `app/pages/6_SQL.py` | 🟢 |
| T6.8 ∥ | **Status**: `scrape_runs` history (source + version), table row counts, freshness, unmatched fighters, model calibration charts from `model_evals` | `app/pages/7_Status.py` | 🟢 |

- **Acceptance (each page):** a Streamlit `AppTest` smoke test (`streamlit.testing.v1.AppTest`) loads the page against a small fixture DB without exceptions.

---

## Phase 7: Daily refresh

### T7.1 Refresh command 🟡
- **Depends on:** T2.4, T2.5, T3.2, T4.1, T4.3, T5.5 (T5.6 optional: skip projections if no model is trained yet)
- **Files owned:** `src/fightdb/pipeline/refresh.py`, CLI wiring (refresh command), `src/fightdb/notify.py`, `tests/pipeline/test_refresh.py`
- **Steps:** implement FR-7, including failover (DATA_SOURCES.md "Freshness monitoring"): if the fight-canon download fails or is stale, import Greco instead and record that. Copy the DB to `fightdb.tmp.duckdb`, apply everything there, then atomically rename over the live file on success. On failure, keep the old DB, write a failed `scrape_runs` row, and exit non-zero. If `DISCORD_WEBHOOK_URL` is set and there are card changes, post a summary.
- **Acceptance:** with fixture HTTP: (1) a first run inserts the card, (2) a second run with a modified fixture records the expected `card_changes`, (3) a simulated adapter exception leaves the original DB byte-identical.

### T7.2 Scheduled refresh (free path: GitHub Actions) 🟢
- **Depends on:** T7.1
- **Files owned:** `.github/workflows/refresh.yml`
- **Steps:** `schedule: cron: "0 11 * * *"` (an hour after fight-canon-db's 10:00 UTC release) plus `workflow_dispatch`. Steps: checkout, set up uv, download the current `fightdb.duckdb` from release `data-latest` (if any), `uv run fightdb refresh` (or `backfill` if no DB exists yet), upload the DB as the `data-latest` release asset with `gh release upload --clobber` (needs `permissions: contents: write`), and pass `DISCORD_WEBHOOK_URL` from repo secrets if set. Add `concurrency: refresh` so two runs never overlap.
- **Acceptance:** a manual `workflow_dispatch` run succeeds and the release asset updates. See DEPLOYMENT.md §B.

### T7.3 Scheduled refresh (homelab: Docker) 🟢
- **Depends on:** T7.1
- **Files owned:** `deploy/Dockerfile`, `deploy/docker-compose.yml`, `deploy/crontab`, `.dockerignore`
- **Steps:** one image (python:3.12-slim + uv + supercronic). Compose services: `app` (runs `streamlit run`, port 8501, reads the DB volume read-only) and `refresher` (runs supercronic with `deploy/crontab` → `fightdb refresh`). Shared named volume `fightdb-data` mounted at `/data`. `env_file: ../.env`. Healthcheck on the app.
- **Acceptance:** `make docker-up` on a fresh machine brings up both services. `docker compose exec refresher fightdb refresh` works manually.

---

## Phase 8: Deployment

Follow [DEPLOYMENT.md](DEPLOYMENT.md). These are checklist tasks, best done by a human or an agent with shell access to the target.

- **T8.1** Homelab deploy (DEPLOYMENT.md §A) 🟢
- **T8.2** Free deploy: GitHub Actions + Streamlit Community Cloud (DEPLOYMENT.md §B) 🟢
- **T8.3** (Optional) Hugging Face Spaces or Oracle Cloud alternative (DEPLOYMENT.md §C/§D) 🟢

---

## Phase 9: Hardening (any order)

- **T9.1** 🟢 Source-drift canary: a weekly workflow runs `make fixtures` against the live sources, runs the adapter tests and the T2.6 cross-check, and opens a GitHub issue on failure or when an upstream hasn't updated in 7+ days.
- **T9.2** 🟡 Data-quality checks after each refresh: row counts never shrink, no bout without two fighters, `% NULL reach` below a threshold. Fail the refresh if violated.
- **T9.3** 🟢 GitHub Actions keepalive (scheduled workflows are disabled after 60 days of no repo activity).
- **T9.4** 🟡 Performance: index or materialise `bout_sides` as a table if queries exceed 2 s.
- **T9.5** 🟢 README screenshots and a "first hypothesis in 60 seconds" walkthrough.

---

## Dependency graph (summary)

```
T0.1 ─► T0.2
  │
  ├─► T1.1 ─┬─► T3.1 ─► T3.2 ─► T4.1 ─┬─► T4.2 ─► T5.2 ──────────┐
  ├─► T1.2 ─┤                          └─► T5.3 ─► T5.4 ─► T5.5 ─► T5.6
  ├─► T1.3 ─┤                                                    │
  ├─► T1.4 ─┼─► T2.1..T2.6 (∥) ─► T3.2               T6.1 ◄──────┘ ─► T6.2..T6.8 (∥)
  │         └─► T4.3 (∥) ──────────────────────────► T7.1 ─► T7.2, T7.3 ─► T8.*
  └─► T5.1 (∥, any time after T0.1)
```

**Maximum parallelism points:** after Phase 1 (T2.1–T2.6, T4.3, T5.1 → up to 8 agents), after T4.1 (the T4.2/T5.2 track alongside the T5.3–T5.6 track), and after T6.1 (T6.2–T6.8 → up to 7 agents).

**Suggested routing:** 🔴 tasks (T4.1 snapshots, T5.2 hypothesis engine, T5.4 ratings, T5.6 outcome models) go to your strongest agent and get a careful human review, since mistakes there quietly produce wrong "insights". 🟢 tasks are good candidates for cheaper or faster agents.
