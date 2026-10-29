# Fight Analyser: Product Requirements Document

**Status:** Draft v0.1 · **Owner:** @kevinofsydney · **Last updated:** 2026-10-29

Related docs: [DATA_SOURCES.md](DATA_SOURCES.md) · [ANALYSIS_IDEAS.md](ANALYSIS_IDEAS.md) · [BUILD_PLAN.md](BUILD_PLAN.md) · [DEPLOYMENT.md](DEPLOYMENT.md) · [DECISIONS.md](DECISIONS.md) · [../AGENTS.md](../AGENTS.md)

---

## 1. Summary

Fight Analyser is a personal UFC database and analysis tool. It keeps a complete history of UFC bouts and fighters, refreshes the upcoming fight card every day, and lets the user:

1. **Test hypotheses** against history, for example: *"In men's lightweight, when one fighter is 8+ years older than the other, how often does the older fighter win, and is that different from chance?"*
2. **See a tale of the tape** for any upcoming bout: who has the better takedown defence, who has the longer reach and by how much, and similar comparisons, with the advantage highlighted.
3. **See which saved hypotheses apply** to each upcoming bout ("this bout has a 9-year age gap; historically the older fighter wins 41% of the time in this division, n=87").
4. **See xG-style matchup projections**: expected takedowns, expected strikes, and win and method-of-victory probabilities that combine one fighter's offence with the other's defence (see [ANALYSIS_IDEAS.md](ANALYSIS_IDEAS.md)).

## 2. Goals and non-goals

### Goals
- G1. A complete, queryable history of UFC events, bouts, fighters and per-fight stats.
- G2. The upcoming card(s) refresh automatically once per day and survive card changes (bouts added, cancelled, replaced, re-ordered, moved between events).
- G3. A hypothesis engine that answers "when X, how often does Y happen?" with honest statistics (sample size, confidence interval, significance), not just a raw percentage.
- G4. A per-bout comparison view readable at a glance.
- G5. Runs on the user's homelab **or** on free hosting from the same codebase.
- G6. Can be built in small, well-specified tasks by several coding agents (Claude, Codex, DS Flash 1017) working in parallel.
- G7. Public repo with **zero secrets committed**.

### Non-goals (v1)
- Betting advice or automated picks. (Historical closing odds *are* imported, but only as a calibration benchmark; see DECISIONS.md D7.)
- Complex ML prediction models beyond the calibrated logistic models in §4.6. (v2 candidate; the v1 data model must not block it.)
- Multi-user accounts, social features, comments.
- Promotions other than the UFC.
- Real-time / live fight-night updates.

## 3. Users and core use cases

Single primary user (the owner), possibly a few friends with read-only access.

| ID | As a user I want to… | So that… |
|----|----------------------|----------|
| U1 | Open the app and see the next UFC card(s) with every bout listed | I know what's coming up |
| U2 | Click a bout and see a side-by-side tale of the tape with the advantage highlighted per stat | I can size up a matchup in seconds |
| U3 | Build a hypothesis from a form (division, condition, outcome) and get a result | I can test ideas without writing SQL |
| U4 | Save a hypothesis and have it automatically checked against every upcoming bout | Relevant historical patterns surface on their own |
| U5 | See what changed on the card since yesterday | I notice late replacements and cancellations |
| U6 | Run ad-hoc SQL against the database (power-user mode) | I'm not limited by the form builder |
| U7 | Trust that the data is current | I'm not analysing a stale card |

## 4. Functional requirements

### 4.1 Data ingestion

We **consume existing, regularly updated datasets** instead of scraping. Full research is in [DATA_SOURCES.md](DATA_SOURCES.md). UFCStats.com now serves a JavaScript bot challenge to plain HTTP clients, and we won't bypass it.

| Data | Primary | Fallback |
|------|---------|----------|
| Completed fights, round stats, fighters, odds | [fight-canon-db](https://github.com/Jadevit/fight-canon-db) `canon.db` release (daily) | [Greco1899/scrape_ufc_stats](https://github.com/Greco1899/scrape_ufc_stats) CSVs (daily) |
| Upcoming cards | ESPN public JSON scoreboard | Wikipedia (MediaWiki API) |

- **FR-1 Historical import.** One command (`fightdb backfill`) downloads the history source and imports every completed UFC event, bout, fighter, per-round stat and odds line into our database. It takes minutes, not hours.
- **FR-2 Upcoming cards.** Ingest every upcoming event in ESPN's calendar and its bouts (fighters, weight class, card order, title-fight flag, scheduled rounds, status).
- **FR-3 Idempotent upserts.** Every import is safe to run repeatedly. We keep our own stable IDs, and record each source's IDs in `*_aliases` tables (UFCStats ID, fight-canon ID, ESPN ID).
- **FR-4 Source adapters.** Each source has an adapter that outputs our own Pydantic models. No other module reads source formats. The history source is chosen by config (`HISTORY_SOURCE=fight_canon|greco`) with automatic fallback (DATA_SOURCES.md, "Freshness monitoring").
- **FR-5 Offline-testable adapters.** Every adapter has tests against small committed fixtures (a tiny SQLite extract, a few CSV rows, saved ESPN JSON). No network in tests.
- **FR-5a Identity resolution.** Map upcoming-card fighters (ESPN) to our fighter IDs: normalised exact name + division, then fuzzy match checked against record, then a committed manual override file. Unmatched fighters are shown as "no UFC history", never silently dropped.
- **FR-5b Polite downloading.** At most one download per source per refresh, a descriptive User-Agent, retries with backoff. Record the source version (release tag / commit SHA) in `scrape_runs`.

### 4.2 Daily refresh and card-change resilience

- **FR-6** A scheduled job runs once per day (default 11:00 UTC, an hour after fight-canon-db's daily release; configurable).
- **FR-7** The job: (a) imports the latest history release (new results and late stat corrections), (b) fetches all upcoming events from ESPN, (c) resolves fighter identities, (d) diffs the cards against the stored ones and applies changes, (e) recomputes snapshots, ratings and matchup projections for affected fighters, (f) records a `scrape_runs` row with status, counts, source versions and errors.
- **FR-8 Change detection.** Detect and log to `card_changes`:
  - bout added
  - bout removed / cancelled (marked `status = 'cancelled'`, **never deleted**)
  - fighter replaced (old bout cancelled, new bout added, linked via `replaces_bout_id` when the opponent is the same)
  - weight class changed (e.g. moved to catchweight)
  - event date or venue changed
  - bout moved to a different event
  - card order changed
- **FR-9** The UI shows a "Card changes (last 7 days)" panel and a "Last refreshed: <time>" indicator. If the last successful refresh is older than 36 hours, show a stale-data warning.
- **FR-10** Optional notification (e.g. Discord webhook) when the card changes. The webhook URL is a secret (see §7).
- **FR-11** A failed refresh must never corrupt the database: writes happen in a transaction, or to a temp DB file that is atomically swapped in.

### 4.3 Derived features (point-in-time)

- **FR-12 No look-ahead.** For every historical bout, each fighter's stats must be computed **as they were before that bout**, from prior fights only. Career averages are *current* numbers; using them for a 2015 bout leaks the future and makes every hypothesis look better than it is. We compute all rates ourselves from round stats. Current numbers are fine for upcoming bouts.
- **FR-13** Per bout, per fighter, store a snapshot (`fighter_snapshots`) including at least:
  - Age at fight date (from DOB), height, reach, stance
  - UFC record before the bout (W-L-D), win/loss streak, finishes, KO/TKO losses, submission losses
  - Number of UFC fights, total UFC fight time, days since last fight
  - Per-15-minute rates: significant strikes landed (SLpM) and absorbed (SApM), takedowns landed, submission attempts
  - Percentages: striking accuracy, striking defence, takedown accuracy, takedown defence
  - Whether the fighter is moving up/down a division for this bout
- **FR-14** Fighters with too little history (e.g. a UFC debut) get `NULL` for rate stats plus a `ufc_fights_before` count, so the hypothesis engine can filter on minimum experience.

### 4.4 Hypothesis engine

- **FR-15 Symmetric "bout sides" view.** Each completed bout appears as two rows, one from each fighter's perspective, with `self_*`, `opp_*` and `diff_*` columns and a `won` boolean. This avoids red/blue corner bias (the red corner is usually the favourite or champion) and makes every "fighter with more X" question a simple filter.
- **FR-16 Hypothesis definition.** A hypothesis has:
  - **Population filters:** division, sex, date range, scheduled rounds, title fight, min UFC fights per fighter, outcome exclusions (draws, no contests and DQs excluded by default).
  - **Condition:** a feature, an operator and a threshold. Example: `diff_age_years >= 8` ("this fighter is at least 8 years older than the opponent"). Compound conditions (AND) are supported.
  - **Outcome:** what counts as success. Default is "won". Others: won by KO/TKO, won by submission, won by decision, fight went the distance, finished in round 1.
  - **Comparison:** against a 50% baseline (default for symmetric win questions), or against the complement group (fights where the condition is false), or against the division's overall rate (for outcomes like "went the distance").
- **FR-17 Result output.** Every result shows:
  - n (bouts matching), successes, success rate
  - 95% confidence interval (Wilson score interval)
  - Baseline rate and the difference, shown **both** as percentage points and relative %, with the two clearly labelled (see DECISIONS.md D10)
  - p-value (exact binomial test vs a fixed baseline; two-proportion z-test or Fisher's exact vs the complement group)
  - A plain-English verdict, e.g. *"Older fighters (8+ yrs) won 41% of 87 bouts (95% CI 31–52%). That is 9 pp below a 50% baseline. Not statistically significant (p = 0.11)."*
  - A warning when n < 30 (configurable), and a reminder about multiple comparisons when the user has run many variants in a session
  - The list of matching bouts (drill-down), so the user can sanity-check
- **FR-18 Sweep mode.** Optionally vary the threshold (e.g. age gap 0–15 years) and plot success rate with CI bands against the threshold. This is the most useful way to see whether "8 years" is a real cutoff or a cherry-picked one.
- **FR-19 Saved hypotheses as files.** Saved hypotheses are YAML files in `hypotheses/` (committed to git). They're versioned, reviewable and work on read-only free hosting. See the schema in §6.3.
- **FR-20 Applied to upcoming bouts.** For each upcoming bout, evaluate each saved hypothesis's condition using current stats. If it matches, show the hypothesis result next to the bout.
- **FR-21 Ad-hoc SQL.** A read-only SQL console against the database, with the `bout_sides` view documented inline. Queries run with a row limit and a timeout.
- **FR-22 CLI parity.** Everything in the hypothesis UI can also be run from the CLI (`fightdb hypothesis run hypotheses/lw-age-gap.yaml`), which lets coding agents and tests use it without a browser.

### 4.5 Bout comparison ("tale of the tape")

- **FR-23** For any bout (upcoming or past), show both fighters side by side with:

| Group | Stats |
|-------|-------|
| Physical | Age, height, reach (and **reach difference**, e.g. "+3 in / +7.6 cm"), stance, weight class |
| Record | UFC record, overall pro record (if available), current streak, finish rate, times finished (KO / sub) |
| Activity | UFC fights, days since last fight, total cage time |
| Striking | SLpM, SApM, striking accuracy, striking defence, striking differential |
| Grappling | Takedowns per 15 min, takedown accuracy, **takedown defence**, submission attempts per 15 min, control time per 15 min |
| Common opponents | Shared opponents and each fighter's result against them |

- **FR-24** For each stat, highlight the fighter with the advantage and show the margin. "Higher is better" or "lower is better" is defined per stat in one config file (for example, SApM: lower is better).
- **FR-25** Show which saved hypotheses match this bout (FR-20), and the bout's matchup projections (§4.6).
- **FR-26** Show a "data confidence" badge when a fighter has fewer than 3 UFC fights (rates are noisy).
- **FR-27** Units: metric and imperial toggle. Default to be decided (DECISIONS.md D13).

### 4.6 Ratings and matchup projections (the "xG" layer)

Details and formulas: [ANALYSIS_IDEAS.md](ANALYSIS_IDEAS.md) §1–3.

- **FR-28 Shrunk rates.** Every per-fighter rate (TD accuracy, TDD, strike accuracy, KD rate and so on) also has an empirical-Bayes shrunk version that pulls small samples towards the division average. `k` is estimated per stat from the data.
- **FR-29 Fighter indices.** Point-in-time, opponent-adjusted indices with 100 = division average: Striking Offence/Defence, Wrestling Offence/Defence, Submission Threat/Defence, Durability, Pace/Cardio, plus an Elo or Glicko-2 rating.
- **FR-30 Matchup projections.** For each upcoming bout: xTD, xCTRL, xSS and xKD per round (odds-ratio combination of A's offence and B's defence against the division baseline), grappling share, win probability, and method-of-victory probabilities (KO / Sub / Dec for each fighter).
- **FR-31 Calibration gate.** Probability models are trained on earlier years and evaluated on later ones (walk-forward), with a reliability chart, Brier score and log loss on the Status page, compared against a naive baseline and the closing betting line. The UI shows "low / medium / high" labels only for calibrated models, defined relative to the division baseline (e.g. high ≥ 1.5× baseline).
- **FR-32 Explainability.** Every projection can be expanded to show the inputs and the arithmetic, e.g. "6.0 attempts/15 × 58.9% matchup accuracy = 3.5 TD/15".

### 4.7 UI pages (v1)

1. **Upcoming** (home): next events, their bouts, last-refreshed time, recent card changes.
2. **Bout**: tale of the tape (§4.5).
3. **Hypothesis Lab**: form builder, results, sweep chart, drill-down, "save as YAML" (downloads or copies the YAML for committing).
4. **Saved Hypotheses**: every YAML hypothesis with its latest result.
5. **Fighter**: profile, fight history, stat trends over time.
6. **SQL Console** (power user).
7. **Status**: recent `scrape_runs`, row counts, data freshness.

## 5. Non-functional requirements

| ID | Requirement |
|----|-------------|
| NFR-1 | **No secrets in git.** All secrets live in `.env` (gitignored) locally, or in the host's secret store (GitHub Actions secrets, HF Space secrets, etc.). `.env.example` documents every variable with dummy values. CI runs a secret scanner (gitleaks). |
| NFR-2 | **Portable.** One Docker image runs the app and the refresher. The same image works on the homelab and on any free host that accepts containers. |
| NFR-3 | **Cheap.** $0/month on the free path. Homelab: one small container plus a ~100–300 MB database file. |
| NFR-4 | **Fast.** Hypothesis queries return in under 2 s on the full dataset (around 8–9k bouts, around 17k bout-side rows). |
| NFR-5 | **Reproducible.** `make setup && make backfill && make app` from a clean clone produces a working local instance. Python dependencies are pinned with a lockfile. |
| NFR-6 | **Agent-friendly.** Every module has a clear contract, tests and a `make` target. CI must pass before merge. See AGENTS.md. |
| NFR-7 | **Observable.** Each refresh writes a `scrape_runs` row. Failures are visible on the Status page and (optionally) via a notification. |
| NFR-8 | **Resilient to source changes.** If an upstream dataset changes its schema or stops updating, adapter validation fails loudly, the refresh fails over to the fallback source, and the job records a failure. It must never write partial or garbage data. |

## 6. Technical design (proposed; confirm in DECISIONS.md)

### 6.1 Stack (recommended defaults)

| Concern | Choice | Why |
|---------|--------|-----|
| Language | Python 3.12 | Best scraping and stats ecosystem, and every coding agent knows it well |
| Package manager | `uv` with `uv.lock` | Fast, reproducible, one tool |
| Data access | `httpx` + `tenacity` for downloads; DuckDB's `sqlite` extension to read `canon.db` directly; `rapidfuzz` for name matching | No HTML scraping needed |
| Database | **DuckDB** (single file) | Analytical SQL, very fast on this data size, no server, easy to ship as one file to free hosts |
| Stats / models | `scipy.stats`, `statsmodels` (Wilson CI, binomial test, proportions z-test, logistic and multinomial regression), `scikit-learn` (calibration, walk-forward CV) | Standard, well-tested |
| UI | **Streamlit** | Python-only, fast to build forms and charts, free hosting available. Alternatives in DECISIONS.md D1 |
| Charts | Altair (ships with Streamlit) or Plotly | |
| CLI | `typer` | |
| Validation | `pydantic` v2 (hypothesis YAML schema, parsed records) | |
| Lint / format / types | `ruff`, `mypy` (or `pyright`) | |
| Tests | `pytest` | |
| Scheduling | GitHub Actions cron (free path) / `supercronic` in Docker (homelab) | |
| Container | Docker + docker compose | |

### 6.2 Data model (DuckDB)

```
events(event_id PK, name, date, location, status['upcoming'|'completed'|'cancelled'], source_url, updated_at)
fighters(fighter_id PK, name, nickname, dob, height_cm, reach_cm, stance, sex, source_url, updated_at)
bouts(bout_id PK, event_id FK, weight_class, division, sex, is_title, scheduled_rounds,
      card_position, fighter_a_id FK, fighter_b_id FK,
      status['scheduled'|'completed'|'cancelled'],
      winner_id NULL, method, method_detail, end_round, end_time_s, referee,
      replaces_bout_id NULL, first_seen_at, last_seen_at, updated_at)
bout_fighter_stats(bout_id, fighter_id, kd, sig_str_landed, sig_str_attempted, total_str_landed,
      total_str_attempted, td_landed, td_attempted, sub_att, reversals, ctrl_time_s,
      head_l, head_a, body_l, body_a, leg_l, leg_a, distance_l, distance_a, clinch_l, clinch_a,
      ground_l, ground_a,  PK(bout_id, fighter_id))
round_stats(bout_id, fighter_id, round, ...same columns...,  PK(bout_id, fighter_id, round))
fighter_snapshots(bout_id, fighter_id, <all FR-13 point-in-time features, raw + shrunk, FR-29 indices, elo>,  PK(bout_id, fighter_id))
fighter_aliases(fighter_id, source['ufcstats'|'fight_canon'|'espn'|'sherdog'], source_id, name,  PK(source, source_id))
event_aliases / bout_aliases  -- same shape
odds(bout_id, fighter_id, open_american, close_american, close_low, close_high, implied_prob_close)
matchup_projections(bout_id, fighter_id, model_version, x_td_r, x_ctrl_s_r, x_ss_r, x_kd_r,
      p_win, p_ko, p_sub, p_dec, computed_at,  PK(bout_id, fighter_id, model_version))
model_evals(model_version, trained_through, test_years, brier, log_loss, brier_closing_line, reliability JSON)
card_changes(id PK, detected_at, run_id FK, event_id, bout_id, change_type, old_value JSON, new_value JSON)
scrape_runs(run_id PK, started_at, finished_at, status, kind['backfill'|'refresh'], counts JSON, error TEXT)

VIEW bout_sides  -- 2 rows per completed bout: self_*, opp_*, diff_* (= self - opp), won, outcome flags
VIEW upcoming_bout_sides -- same shape for scheduled bouts, using current stats
```

`division` is a normalised enum: `flyweight, bantamweight, featherweight, lightweight, welterweight, middleweight, light_heavyweight, heavyweight, w_strawweight, w_flyweight, w_bantamweight, w_featherweight, catchweight, openweight`.

### 6.3 Hypothesis YAML schema

```yaml
# hypotheses/lw-older-by-8.yaml
id: lw-older-by-8
title: "Men's LW: fighter 8+ years older"
description: Does a big age advantage help or hurt at lightweight?
population:
  divisions: [lightweight]
  sex: M
  date_from: 2010-01-01      # optional
  date_to: null              # optional
  min_ufc_fights_each: 0
  exclude_outcomes: [draw, no_contest, dq]
condition:                   # all clauses ANDed; evaluated on bout_sides
  - feature: diff_age_years
    op: ">="
    value: 8
outcome: won                 # won | won_ko | won_sub | won_dec | went_distance | finished_r1
compare_to: baseline_50      # baseline_50 | complement | division_rate
sweep:                       # optional
  feature: diff_age_years
  from: 0
  to: 15
  step: 1
```

### 6.4 Repository layout

```
fight-analyser/
├── AGENTS.md                 # rules for all coding agents (Claude reads CLAUDE.md → AGENTS.md)
├── CLAUDE.md
├── README.md
├── Makefile                  # the only commands agents need
├── pyproject.toml / uv.lock
├── .env.example              # committed; dummy values only
├── .gitignore                # ignores .env, data/, caches
├── src/fightdb/
│   ├── config.py             # reads env vars (pydantic-settings)
│   ├── cli.py                # typer: backfill, refresh, hypothesis, serve, db
│   ├── sources/
│   │   ├── models.py         # source-neutral Pydantic models (the contract)
│   │   ├── http.py           # download client: retries, User-Agent, cache
│   │   ├── fight_canon.py    # canon.db → models (primary history)
│   │   ├── greco.py          # Greco1899 CSVs → models (fallback history)
│   │   ├── espn.py           # ESPN scoreboard JSON → upcoming models
│   │   └── identity.py       # map ESPN fighters → our fighter IDs
│   ├── db/
│   │   ├── schema.sql
│   │   ├── views.sql
│   │   └── conn.py
│   ├── pipeline/
│   │   ├── backfill.py
│   │   ├── refresh.py
│   │   └── card_diff.py
│   ├── features/
│   │   ├── snapshots.py      # point-in-time features
│   │   └── stat_meta.py      # stat labels, units, higher_is_better
│   ├── analytics/
│   │   ├── hypothesis.py     # load YAML → SQL → result
│   │   └── stats.py          # wilson_ci, binomial_test, two_prop_test
│   ├── models/
│   │   ├── shrinkage.py      # empirical-Bayes rates
│   │   ├── ratings.py        # indices + Elo/Glicko
│   │   ├── matchup.py        # xTD / xSS / xCTRL / xKD
│   │   └── outcome.py        # win + method-of-victory models, calibration
│   └── app/                  # Streamlit
│       ├── Home.py
│       └── pages/
├── hypotheses/               # saved hypotheses (YAML)
├── tests/
│   ├── fixtures/             # tiny canon.db extract, sample CSVs, saved ESPN JSON
│   └── ...
├── deploy/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── crontab
│   └── huggingface/README.md
├── .github/workflows/
│   ├── ci.yml                # lint, typecheck, test, gitleaks
│   └── refresh.yml           # daily cron for the free-hosting path
├── data/                     # gitignored: fightdb.duckdb, downloaded sources
│   └── overrides/            # EXCEPTION, committed: fighter_identity.yaml (manual name matches)
└── docs/
```

### 6.5 Data flow

```
                         ┌──────────────── daily (cron, 11:00 UTC) ────────────────┐
fight-canon-db release ─┐
  (fallback: Greco CSVs)├─► adapters ──► upsert (temp DB) ──► identity ──► card_diff ──► card_changes
ESPN scoreboard JSON ───┘                       │
                                                ▼
                         fighter_snapshots (raw + shrunk) ──► ratings / Elo ──► matchup_projections
                                                │
                                                ▼
                                        bout_sides view
                                                │
                ┌───────────────────────────────┼─────────────────────────────┐
                ▼                               ▼                             ▼
        Hypothesis engine              Tale of the tape + xG            SQL console
        (YAML / form / CLI)            (per bout)
```

**Free-hosting path:** the GitHub Actions cron builds the DB and publishes `fightdb.duckdb` as a GitHub Release asset (tag `data-latest`). The app downloads it on startup and when it's older than N hours.
**Homelab path:** a refresher container writes the DB to a shared Docker volume, and the app container reads it.

## 7. Secrets and configuration

v1 is designed to need **no secrets at all**. GitHub release downloads, raw CSVs and the ESPN JSON need no key. Optional secrets:

| Variable | Needed for | Where it lives |
|----------|-----------|----------------|
| `DISCORD_WEBHOOK_URL` | Card-change notifications (optional) | `.env` / GitHub Actions secret |
| `ODDS_API_KEY` | v2 odds feature (optional) | `.env` / GitHub Actions secret |
| `CLOUDFLARE_TUNNEL_TOKEN` | Exposing the homelab instance (optional) | `.env` on the homelab only |
| `APP_PASSWORD` | Simple shared password on a public deployment (optional) | `.env` / host secret store |
| `GITHUB_TOKEN` | Publishing the data release from Actions | Provided automatically by Actions; never stored |

Non-secret configuration (`DB_PATH`, `REFRESH_CRON`, `HISTORY_SOURCE`, `DATA_RELEASE_URL`, `UNITS`) also goes in `.env`, with defaults in `config.py`.

Rules: `.env` and `data/` are gitignored. `.env.example` holds placeholders only. gitleaks runs in CI and (optionally) as a pre-commit hook.

## 8. Success metrics

- Backfill (download + import + features) completes from a clean clone in under 15 minutes, with adapter test coverage for every source.
- At least 98% of upcoming-card fighters with prior UFC fights are matched to an ID automatically.
- The win-probability model is better calibrated than the naive baseline on held-out years (Brier score), and we report honestly how it compares with the closing line.
- Daily refresh success rate ≥ 95% over 30 days; staleness never exceeds 36 h without a visible warning.
- Card changes appear in the app within 24 h of being published on the source.
- Hypothesis queries return in under 2 s.
- The user can build and evaluate a new hypothesis in under 60 seconds from the form.

## 9. Risks and mitigations

| Risk | Mitigation |
|------|-----------|
| Upstream dataset stops updating (fight-canon-db is young and single-maintainer) | Automatic failover to Greco1899; weekly cross-source check; we keep our own snapshot of everything imported; paid API (UFCalendar) as last resort (DATA_SOURCES.md) |
| ESPN's undocumented API changes | Strict validation, loud failure, Wikipedia fallback adapter; the history import keeps working regardless |
| Fighter name mismatches between sources | Identity resolution with record cross-check plus a committed override file; unmatched fighters are listed on the Status page |
| Look-ahead bias produces false "insights" | FR-12 point-in-time snapshots; a test asserts no snapshot uses fights dated on or after the bout |
| Multiple comparisons / p-hacking | CIs everywhere, sweep charts, small-n warnings, a running "tests this session" counter |
| Corner bias (red = favourite) | Symmetric `bout_sides` view (FR-15) |
| Missing data (reach/DOB missing for older fighters) | Explicit NULL handling; results report how many bouts were excluded for missing data |
| GitHub Actions scheduled workflows pause after 60 days without repo activity | The refresh job commits nothing; add a monthly keepalive, or use the homelab cron as primary (see DEPLOYMENT.md) |
| Licensing / redistribution | fight-canon-db is MIT and Greco1899 is GPL-3.0 (we read their data, not their code). Credit both. Decide whether our derived data release is public (D15) |
| Several agents editing the same files | Contract-first build plan with file ownership per task (BUILD_PLAN.md) |

## 10. Future (v2+)

- Live/upcoming odds and "model vs market" views for upcoming bouts (historical odds are already in v1)
- Gradient-boosted prediction models, if they beat the v1 logistic models out of sample
- Multi-variable hypotheses with controls (logistic regression with covariates)
- An "insight finder" that scans many features and reports patterns with false-discovery-rate correction
- Other promotions (PFL, ONE, Bellator archives)
