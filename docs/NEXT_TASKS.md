# Next Tasks

Updated 2026-11-09. This is the handoff file: read it after [AGENTS.md](../AGENTS.md) and [PRD.md](PRD.md). It says what exists, what's verified, and what is left to build, in task-sized pieces.

## Where things stand

- **v1 is built.** Every phase in [BUILD_PLAN.md](BUILD_PLAN.md) (0–9) is implemented, and every row in [DECISIONS.md](DECISIONS.md) is decided.
- **Python 3.14** (`.python-version`, `requires-python >=3.14`, Docker `python:3.14-slim`). The code uses 3.14-only syntax (PEP 758 `except A, B:`), so it won't run on 3.12 or 3.13.
- **Live app:** https://fight-analyser.streamlit.app/ (Streamlit Community Cloud, deployed from `main`, `src/fightdb/app/Home.py`). The password gate (`APP_PASSWORD`) shows on Home, SQL and Status, and nothing loads before the password is entered.
- **Data:** the GitHub Actions "Daily refresh" builds `fightdb.duckdb` and publishes it to the `data-latest` release, and the app downloads it via `DATA_RELEASE_URL`. The DB holds about 9,000 UFC bouts and 71,518 judge round scores.
- **Quality:** `make check` passes, with 146 offline tests and CI green on `main`. Branch protection is on for `main`.
- **Licence:** MIT.

**Windows without `make`:** run `make check` as:
```bash
export PYTHONPATH=src
uv run ruff check src tests scripts && uv run ruff format --check src tests scripts && uv run mypy src && uv run pytest -q
```

Run the app locally with `uv run streamlit run src/fightdb/app/Home.py` (http://localhost:8501; no password unless `APP_PASSWORD` is set in `.env`).

---

## Owner checks (human only, about 5 minutes)

1. **Sign in to the live app** and open **Status**. The last refresh run should be `ok` and recent (within about 36 h), and the table sizes should be non-zero. If the page shows a `SyntaxError`, the Streamlit app was created with an older Python. Streamlit can't change the Python version of an existing app, so delete the app and redeploy it with Python 3.14 (DEPLOYMENT.md §B4).
2. **Keep the secrets out of git.** `APP_PASSWORD` and `DATA_RELEASE_URL` live only in Streamlit → app → Settings → Secrets.

---

## What's left to build

Pick one task, branch `task/<ID>-<short-name>`, one PR per task (AGENTS.md). Difficulty: 🟢 mechanical, 🟡 moderate judgement, 🔴 needs careful reasoning and close review. None of these blocks using the app today.

### Hardening and ops

| ID | Task | Files | Done when | |
|----|------|-------|-----------|--|
| H1 | **Live-app smoke check.** A scheduled workflow (daily, after the refresh) that GETs https://fight-analyser.streamlit.app/ and fails if the status isn't 200. Streamlit apps sleep; a GET wakes them, so allow about 60 s and one retry. Don't send the password. | `.github/workflows/app-smoke.yml` | Workflow runs green on `workflow_dispatch`; README links the badge | 🟢 |
| H2 | **Stale-data alert.** If the newest `scrape_runs` row with `status='ok'` is older than 36 h when the refresh workflow ends, open (or update) a GitHub issue, reusing the canary's issue step. | `.github/workflows/refresh.yml` | Simulated by pointing it at an old DB in a test job, or verified by a dry-run flag | 🟢 |
| H3 | **README screenshots** (T9.5 leftover): Home, Bout, Hypothesis Lab, from a local run. PNGs under `docs/img/`, each under 300 KB. | `README.md`, `docs/img/*` | Images render on GitHub | 🟢 |
| H4 | **Homelab deploy check** (T8.1). The compose files exist but haven't been run on real hardware since the 3.14 upgrade: `docker compose -f deploy/docker-compose.yml up -d --build`, then open :8501 and check Status. | none unless it breaks | App and refresher containers healthy | 🟢 (needs the owner's box) |
| H5 | **Performance check (NFR-4).** Time every saved hypothesis and the six ad-hoc queries on the Bout page against the full DB; anything over 2 s gets an index or a materialised column. Add a slow-marked benchmark test. | `src/fightdb/analytics/*`, `tests/analytics/test_perf.py` | Each hypothesis is under 2 s on a laptop | 🟡 |

### New hypothesis features (from the question catalogue, [ANALYSIS_IDEAS.md](ANALYSIS_IDEAS.md) §4)

These questions can't be asked in the Hypothesis Lab yet because the feature column doesn't exist. Each task adds point-in-time columns to `fighter_snapshots` / `bout_sides`, plus a `stat_meta.py` entry. **`stat_meta.py` is a contract file:** the task owns it, so say so in the PR. Every new feature needs a look-ahead test like `test_future_fights_never_change_past_snapshots`.

| ID | Question | What to add | |
|----|----------|-------------|--|
| F1 | 17, 29: home-country advantage | Fighter nationality isn't imported. Check whether fight-canon-db has it (`fighters` table); if so, import it as `fighters.country`, parse the country from `events.location`, and add `self_is_home`. If the source lacks it, write that up in DATA_SOURCES.md and stop. | 🟡 |
| F2 | 18: altitude | Add `events.altitude_m` from a small committed lookup of venue cities (Mexico City, Denver, Salt Lake City and others above 1,500 m) in `data/overrides/altitude.yaml`, plus a `high_altitude` flag on `bout_sides`. | 🟢 |
| F3 | 12: accumulated damage | `self_career_sig_absorbed` and `self_career_kd_absorbed` (sums over prior fights). | 🟢 |
| F4 | 11, 13: after a KO loss | `self_last_result_ko_loss` (previous fight was a KO/TKO loss) and `self_ko_losses_last_3`. | 🟢 |
| F5 | 22: who fades | `self_fade_ratio` = last-round vs first-round sig strikes per minute over prior fights, shrunk like the other rates. | 🟡 |
| F6 | 21, 23, 24: in-fight dynamics | These condition on what happens *during* the bout (won round 1, early takedown, more control time), so they aren't pre-fight features. Add a `bout_round_sides` view (one row per bout, fighter and round, with round winner on strikes and judges' round score where available), and a SQL-page example for each question. Don't add these to the hypothesis feature list, because they'd leak the result. | 🟡 |
| F7 | 6: wrestler vs striker | `self_style` ∈ {wrestler, striker, balanced}, from point-in-time `idx_wrest_off` vs `idx_str_off` (document the thresholds in ANALYSIS_IDEAS.md). Categorical, so the hypothesis condition builder needs an `in` operator if it doesn't have one. | 🟡 |

### Modelling (careful, needs review)

| ID | Task | |
|----|------|--|
| M1 | **Decision-label watch.** In the method model, big favourites winning on points are under-predicted (0.5–0.6 bin: 53% predicted, 64% observed, n=47). Once that bin has 100+ fighters in the last three test years and is still off by more than 5 pp, the gate hides only the decision label. Try adding `self_p_win` as a feature to the method model and compare walk-forward log loss; keep it only if log loss improves and every method stays calibrated. | 🔴 |
| M2 | **Market comparison for upcoming bouts** (PRD §10, v2). Live odds need `ODDS_API_KEY` (already documented in PRD §7). Show model vs implied probability on the Bout page, labelled "not betting advice". Needs an owner decision on the odds provider first. | 🔴 |
| M3 | **Multi-variable hypotheses** (PRD §10): logistic regression with covariates, so "older by 8+ years" can be tested controlling for Elo gap. Add an optional `controls:` list to the hypothesis YAML schema (a contract change in `analytics/schema.py`); report the adjusted odds ratio and CI. | 🔴 |
| M4 | **Insight finder** (PRD §10): scan every numeric feature × outcome, report effects with Benjamini–Hochberg FDR correction. CLI first (`fightdb hypothesis scan`), page later. | 🔴 |

### Out of scope unless the owner asks

Other promotions (PFL, ONE), gradient-boosted models, live fight-night updates, user accounts (PRD §2 non-goals).

---

## Known quirks

- **Desktop-app sandbox (Windows):** `uv python install` can fail with "Missing expected target directory for Python minor version link", because the packaged app redirects `AppData\Roaming`. Point uv at the downloaded interpreter: `UV_PYTHON=C:/Users/<you>/AppData/Roaming/uv/python/cpython-3.14.6-windows-x86_64-none/python.exe uv sync`. A normal terminal isn't affected.
- **A running local app locks `.venv`:** stop Streamlit before `uv sync`.
- **Five upcoming debutants are unmatched**, which is expected (Status page). Add a line to `data/overrides/fighter_identity.yaml` only for a fighter with UFC history.
- **GitHub cron starts late:** the 11:00 UTC refresh has been starting between 16:30 and 18:30 UTC.

---

## Done on 2026-11-09

| ID | What changed |
|----|--------------|
| N1 | Every text file read/write uses `encoding="utf-8"` (4 tests failed on Windows). |
| N2 | Canary (issue #1): upstream control-time disagreements, not an adapter bug. The cross-source test tolerates ≤5 s drift and `max(2, 1%)` differing rows. |
| N3 | Actions on Node 24: `actions/checkout@v7`, `astral-sh/setup-uv@v10.3.3`, `gitleaks/gitleaks-action@v3`. |
| N4 | New tests: download client, stats edge cases, schema, backfill idempotency and failover, notifications, hypothesis outcomes. |
| N5 | Data-quality gate before the DB swap (`pipeline/quality.py`, `DQ_*` settings). |
| N6 | Method model evaluated walk-forward; labels gated per method on `outcome.is_calibrated`. The gate counts each "wins by X" probability once (an earlier version double-counted it). |
| N7 | `idx_sub_def` (submission defence index). |
| N8 | `self/opp_days_notice` and `_short_notice`; example `short-notice.yaml`. |
| N9 | Identity overrides (Duclos, Osmanli); the card diff no longer reports a newly identified fighter as a change. |
| N10 | Wikipedia fallback for upcoming cards (adds bouts, never cancels). |
| N11 | README walkthrough, docs refresh. |
| S1 | Strength-of-schedule adjustment (Brier 0.2334 → 0.2322); walk-forward shrinkage priors (look-ahead fix). |
| S2 | Judge scores imported (`judge_scores`, `judge_rounds` view). |
| S3 | Method-model features (log loss 1.6334 → 1.6237). |
| R1 | A failing saved hypothesis is reported and skipped instead of breaking `run-all`, the Saved Hypotheses page or the Bout page (PR #3). |
| P1 | Python 3.12 → 3.14 (PR #4). |
| D1 | Public app deployed to Streamlit Community Cloud with `APP_PASSWORD` set. |
