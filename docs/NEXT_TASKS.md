# Next Tasks

Updated 2026-11-09. This is the handoff file: read it after [AGENTS.md](../AGENTS.md) and [PRD.md](PRD.md). It says what exists, what's verified, and what is left to build, in task-sized pieces.

## Where things stand

- **v1 is built.** Every phase in [BUILD_PLAN.md](BUILD_PLAN.md) (0–9) is implemented, and every row in [DECISIONS.md](DECISIONS.md) is decided.
- **Python 3.14** (`.python-version`, `requires-python >=3.14`, Docker `python:3.14-slim`). The code uses 3.14-only syntax (PEP 758 `except A, B:`), so it won't run on 3.12 or 3.13.
- **Live app:** https://fight-analyser.streamlit.app/ (Streamlit Community Cloud, deployed from `main`, `src/fightdb/app/Home.py`). The password gate (`APP_PASSWORD`) shows on Home, SQL and Status, and nothing loads before the password is entered.
- **Data:** the GitHub Actions "Daily refresh" builds `fightdb.duckdb` and publishes it to the `data-latest` release, and the app downloads it via `DATA_RELEASE_URL`. The DB holds about 9,000 UFC bouts and 71,518 judge round scores.
- **Quality:** CI on `main` runs lint, formatting checks, type checking, offline tests, secret scanning and a Docker build. Branch protection is on for `main`.
- **Owner workflow:** agents may merge their changes after required checks pass; the owner will not review code.
- **Upcoming odds:** The Odds API integration supplies upcoming bookmaker prices when `ODDS_API_KEY` is configured in GitHub Actions. Historical closing odds need no key.
- **Fight wear:** recorded prior UFC strikes and knockdowns absorbed, high-absorption fight counts, recent exposure, last KO loss and five-round-war features are implemented. Incomplete round-stat histories remain unknown. Per the owner, do not add these and related November 2026 hypothesis features to prediction models or evaluate them for model improvement.
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
2. **Keep secrets out of git.** `APP_PASSWORD` and `DATA_RELEASE_URL` live in Streamlit → app → Settings → Secrets. For optional upcoming odds, add `ODDS_API_KEY` to GitHub Actions repository secrets; do not paste it into chat or commit it.

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
| F1 | 17, 29: home-country advantage | `fighters.nationality` exists; assess source coverage and historical provenance, parse event country, and add `self_is_home` only where the comparison is reliable. | 🟡 |
| F2 | 18: altitude | Add `events.altitude_m` from a small committed lookup of venue cities (Mexico City, Denver, Salt Lake City and others above 1,500 m) in `data/overrides/altitude.yaml`, plus a `high_altitude` flag on `bout_sides`. | 🟢 |
| F4 | 11, 13: after a KO loss | The previous-fight KO/TKO loss flag and quick-turnaround hypothesis exist as `self_last_ko_loss`; the count of KO losses over the last three fights remains to be added. | 🟢 |
| F5 | 22: who fades | `self_fade_ratio` = last-round vs first-round sig strikes per minute over prior fights, shrunk like the other rates. | 🟡 |
| F6 | 21, 23, 24: in-fight dynamics | These condition on what happens *during* the bout (won round 1, early takedown, more control time), so they aren't pre-fight features. Add a `bout_round_sides` view (one row per bout, fighter and round, with round winner on strikes and judges' round score where available), and a SQL-page example for each question. Don't add these to the hypothesis feature list, because they'd leak the result. | 🟡 |
| F7 | 6: wrestler vs striker | `self_style` ∈ {wrestler, striker, balanced}, from point-in-time `idx_wrest_off` vs `idx_str_off` (document the thresholds in ANALYSIS_IDEAS.md). Categorical, so the hypothesis condition builder needs an `in` operator if it doesn't have one. | 🟡 |

### Modelling (careful, needs review)

| ID | Task | |
|----|------|--|
| M1 | **Decision-label watch.** In the method model, big favourites winning on points are under-predicted (0.5–0.6 bin: 53% predicted, 64% observed, n=47). Once that bin has 100+ fighters in the last three test years and is still off by more than 5 pp, the gate hides only the decision label. Try adding `self_p_win` as a feature to the method model and compare walk-forward log loss; keep it only if log loss improves and every method stays calibrated. | 🔴 |
| M2 | **Market comparison for upcoming bouts.** The Odds API feed and bookmaker prices are implemented. A calculated model-versus-market implied-probability comparison is not implemented. | 🔴 |
| M3 | **Multi-variable hypotheses** (PRD §10): logistic regression with covariates, so "older by 8+ years" can be tested controlling for Elo gap. Add an optional `controls:` list to the hypothesis YAML schema (a contract change in `analytics/schema.py`); report the adjusted odds ratio and CI. | 🔴 |
| M4 | **Insight finder** (PRD §10): scan every numeric feature × outcome, report effects with Benjamini–Hochberg FDR correction. CLI first (`fightdb hypothesis scan`), page later. | 🔴 |

### Out of scope unless the owner asks

Other promotions (PFL, ONE), gradient-boosted models, live fight-night updates, user accounts (PRD §2 non-goals). Do not add the November 2026 fight-wear and related hypothesis features to prediction models or evaluate them for model improvement unless the owner changes this instruction (AGENTS.md).

---

## Known quirks

- **Desktop-app sandbox (Windows):** `uv python install` can fail with "Missing expected target directory for Python minor version link", because the packaged app redirects `AppData\Roaming`. Point uv at the downloaded interpreter: `UV_PYTHON=C:/Users/<you>/AppData/Roaming/uv/python/cpython-3.14.6-windows-x86_64-none/python.exe uv sync`. A normal terminal isn't affected.
- **A running local app locks `.venv`:** stop Streamlit before `uv sync`.
- **Some upcoming debutants are unmatched**, which is expected (Status page). Add a line to `data/overrides/fighter_identity.yaml` only for a fighter with UFC history.
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

## Later changes on 2026-11-09

| PR | What changed |
|----|--------------|
| #6 | Human-readable dates, Fighter history navigation, per-round fight statistics and pre-fight Elo on Bout. |
| #7 | Fight-wear features and profile, division move direction and stance flags, and saved age, size, debut and fight-wear hypotheses. |
| #8 | Hypothesis start-date picker through today, Bout reset and model-input explanation. |
| #9 | Paginated matching historical fighter-sides in Saved Hypotheses, with links to bouts. |
| #10 | Hoverable outcome plots and descriptive one-variable logistic fits for saved hypotheses; matching or all eligible population. |
| #11 | Historical closing odds display and optional daily Australian bookmaker quotes from The Odds API. |
| #12 | Selecting a Home card row opens the corresponding Bout page. |
| #13 | Side-by-side Bout cards with ESPN portraits when available, current records, odds and physical details. Rank, weigh-in weight and gym remain unavailable. |

## Win-model experiments on 2026-11-10

Walk-forward win model, A side of each decided bout. Log loss 2012+ / 2020+ (baseline 0.6556 / 0.6452). The closing line's Brier score is 0.2096 on the same rows.

| Tried | Result |
|-------|--------|
| Opponent adjustment squared (`ratings.SOS_POWER = 2`) | **Shipped.** 0.6548 / 0.6437, accuracy 60.9% → 61.2% / 62.0% → 62.4%; every calibration gate still passes. 1.5 and 3 were close; 0 (no adjustment) was worst (0.6583). |
| Stronger or weaker shrinkage (k × 0.5, 2, 4) | No change (±0.0007). The estimated priors are about right. |
| Recency weighting (4-year half-life) on top of the above | 0.6540 / 0.6440, better, but the KO and decision gates then fail. Their top bins (KO 0.3–0.4, decision 0.4–0.5) were already under-confident and slipped past the 5-point limit, so those labels would disappear. Not shipped. Worth retrying once the method split's under-confidence is fixed (stronger method-model C did not help). |
| Age hinges (over 30 / 32 / 34), logistic C 0.05–5 | No change. |
| Gradient boosting (HistGradientBoosting, mirrored for symmetry) | Worse on 2020+ (0.6490+). Its apparent gain on 2012+ came from **missing reach**: fighters with no recorded reach win 4–6% of bouts before 2020 because short careers never got measured. That is hindsight, not a usable signal. Don't add missing-value flags for profile fields. |
