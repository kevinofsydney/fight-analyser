# Decisions Needed

Choices for the owner (@kevinofsydney) to make. Each has a **recommended default**, which is what [BUILD_PLAN.md](BUILD_PLAN.md) assumes. Mark your choice in the **Decision** column. If you pick something other than the default, the "Affects" column shows which tasks to update before handing them out.

**Status key:** ⬜ open · ✅ decided

## Blocking: decide before Phase 0/1

| # | Decision | Options | Recommended | Affects | Decision |
|---|----------|---------|-------------|---------|----------|
| D1 | **UI framework** | (a) Streamlit (b) FastAPI + HTMX/Jinja (c) FastAPI + React/Next.js (d) static site + DuckDB-WASM in the browser | **(a) Streamlit**: Python only, fastest to build, free hosting, and every agent knows it. (d) is attractive later (fully static, free, never sleeps) but harder to build. | Phase 6, DEPLOYMENT B/C | ⬜ |
| D2 | **Database** | (a) DuckDB file (b) SQLite file (c) Postgres | **(a) DuckDB**: built for analytics queries and ships as one file to free hosts. Postgres only if you later want concurrent writers. | T1.1, T3.1, T4.* | ⬜ |
| D3 | **Primary hosting to build for first** | (a) Free (Actions + Streamlit Cloud) (b) Homelab (c) Both from day one | **(c) Both**, but deploy **(a)** first. The same Docker image and refresh command cover both, so the extra cost is small. | T7.2, T7.3, Phase 8 | ⬜ |
| D4 | **Data source strategy** (researched; see [DATA_SOURCES.md](DATA_SOURCES.md)) | (a) Scrape UFCStats ourselves (b) Consume GitHub datasets: fight-canon-db (primary) + Greco1899 (fallback) for history, ESPN JSON for upcoming cards (c) Paid API (UFCalendar) | **(b)**. UFCStats now serves a JS bot challenge (verified 2026-10-29), so (a) is out. Both datasets update daily and are verified current through UFC 333. (c) is the last-resort fallback. | Phase 2, T3.2 | ⬜ |
| D5 | **Python tooling** | (a) uv (b) Poetry (c) pip + requirements.txt | **(a) uv** | T0.1, Dockerfile | ⬜ |

## Data and scope: decide before Phase 3/4

| # | Decision | Options | Recommended | Affects | Decision |
|---|----------|---------|-------------|---------|----------|
| D6 | **Historical range** | (a) All UFC history (1993→) (b) Since 2005 (c) Since 2010 | **Ingest all** (a), but **default hypothesis filters to `date_from: 2010-01-01`**. Early UFC had no weight classes or modern rules, and its stats are sparse. | T3.2, T5.2 defaults | ⬜ |
| D7 | **Betting odds** | (a) Ignore (b) Import historical closing lines from fight-canon-db as a **calibration benchmark** only (c) Also show upcoming lines and "model vs market" | **(b)**. It's free, already in the file, and it's the honest yardstick for our models. (c) is a v2 question and may need a keyed API. | T2.2, T5.6 | ⬜ |
| D8 | **Women's divisions** | (a) Include (b) Men's only | **(a) Include**. It costs nothing, and `sex` is a filter. | T1.2 | ⬜ |
| D9 | **Default exclusions in hypotheses** | Which outcomes to drop by default: draws, no contests, DQs, overturned results | **Exclude draws, NCs and DQs** by default (each overridable per hypothesis) | T5.2 | ⬜ |
| D10 | **How to phrase "X% higher chance"** | (a) Absolute percentage points (41% vs 50% = −9 pp) (b) Relative (41/50 − 1 = −18%) (c) Show both | **(c) Show both, clearly labelled.** Your example "20% higher chance" is ambiguous: 50%→60% is +10 pp but +20% relative. | T5.1, T5.2, T6.4 | ⬜ |
| D11 | **Division assignment** | For catchweights and fighters changing divisions: (a) use the bout's weight class (b) use the fighter's "primary" division | **(a)**, with catchweights as their own bucket, plus a flag for "fighter moving up/down" | T1.2, T4.1 | ⬜ |
| D12 | **Statistical defaults** | Min sample size warning; CI level; significance threshold | **n < 30 warning, 95% CI, flag p < 0.05**, with an always-visible note that running many variants inflates false positives | T5.1, T5.2 | ⬜ |
| D13 | **Units** | (a) Metric default (b) Imperial default (c) Show both | **(c) Show both** for reach and height (e.g. "+3.0 in / +7.6 cm"). Toggle for primary. | T6.3 | ⬜ |
| D14 | **Saved hypotheses storage** | (a) YAML files in git (b) Rows in the DB (c) Both | **(a) YAML in `hypotheses/`**. Versioned and reviewable, and it works on read-only free hosts. The UI's "save" produces YAML for you to commit. | T5.2, T6.4, T6.5 | ⬜ |

## Access, legal and ops: decide before Phase 8

| # | Decision | Options | Recommended | Affects | Decision |
|---|----------|---------|-------------|---------|----------|
| D15 | **Is our built database public?** | (a) Public GitHub Release asset (free path) (b) Private: homelab-only, or a private bucket/repo (needs a token secret) | Our DB is derived from fight-canon-db (MIT, already public) and ESPN JSON. **(a) is low-risk**, but credit the upstreams in the README and release notes. Choose (b) if you'd rather not republish ESPN-derived card data. | T7.2, DEPLOYMENT B/C | ⬜ |
| D16 | **Who can view the app** | (a) Public, anyone with the URL (b) Shared password (`APP_PASSWORD`) (c) Private network only (Tailscale) (d) Cloudflare Access login | **(b)** on free hosts, **(c)** on the homelab | T6.1, DEPLOYMENT | ⬜ |
| D17 | **Refresh time** | Cron time, in UTC | **11:00 UTC daily** (~10 pm Sydney during daylight saving / ~7 am US Eastern), an hour after fight-canon-db publishes at 10:00 UTC. Optionally add an ESPN-only card check on fight days. | T7.2, T7.3 | ⬜ |
| D18 | **Card-change notifications** | (a) None (b) Discord webhook (c) Email (d) ntfy / Gotify (homelab-friendly) | **(b) Discord**, or **(d) ntfy** if you already run it. Either way the URL or token is a secret in `.env`. | T7.1 | ⬜ |
| D19 | **Repo license** | MIT / Apache-2.0 / none (all rights reserved) | **MIT** for the code. The license doesn't cover scraped data (see D15). | README | ⬜ |
| D20 | **Domain name** | Use a `*.streamlit.app` / `*.hf.space` subdomain, or your own domain | Free subdomain to start | DEPLOYMENT | ⬜ |

## Analytics and models: decide before Phase 5b

| # | Decision | Options | Recommended | Affects | Decision |
|---|----------|---------|-------------|---------|----------|
| D24 | **How far to take the xG layer in v1** | (a) Hypotheses + tale of the tape only (b) + shrinkage and indices (c) + matchup projections (xTD/xSS/xCTRL/xKD) (d) + calibrated win/method probabilities | **(d)**, built in that order, so each step ships on its own. Labels like "sub chance: medium-high" only appear once (d) passes its calibration check. | Phase 5b, T6.3 | ⬜ |
| D25 | **Rating system** | (a) Elo (b) Glicko-2 (tracks uncertainty, better for inactive fighters) (c) Both | **(a) Elo first**, Glicko-2 later if inactivity turns out to matter | T5.4 | ⬜ |
| D26 | **"Low / medium / high" thresholds** | Relative to division baseline: e.g. low ≤ 0.67×, high ≥ 1.5× | **Recommended as written**; tune after seeing the distributions | T6.3 | ⬜ |
| D27 | **Which questions to save first** | Pick 5–10 from [ANALYSIS_IDEAS.md](ANALYSIS_IDEAS.md) §4 | Start with the ★ items (age gap, reach, TD-vs-TDD, sub specialist, short-notice replacements) | `hypotheses/` | ⬜ |

## Multi-agent workflow

| # | Decision | Options | Recommended | Decision |
|---|----------|---------|-------------|----------|
| D21 | **Branch / merge policy** | (a) Every task = branch + PR, CI must pass, you merge (b) Agents push to main | **(a)**. Turn on branch protection for `main` (require CI to pass) in GitHub settings. | ⬜ |
| D22 | **Who reviews contract and 🔴 tasks** | You, or a second agent cross-reviewing (e.g. Codex reviews Claude's PR) | **Cross-agent review, then you approve**, for T1.*, T4.1 and T5.2 | ⬜ |
| D23 | **Task routing** | Which agent gets which tasks | 🔴 tasks to your strongest agent; 🟢 adapter/page tasks spread across the others. Track assignments in GitHub Issues (one issue per task ID). | ⬜ |

---

### Questions to answer during the build (not blocking now)

- After a few hypotheses, does the form builder cover what you want, or do you mostly use the SQL console? (This decides whether to invest in v2 multi-variable regression.)
- Does ESPN pick up late replacements quickly enough? If not, add a Wikipedia or UFC.com card check (DATA_SOURCES.md).
- Is fight-canon-db still updating reliably after a couple of months? (It's young. The failover to Greco covers us, but it's worth watching.)
- Do you want past bouts to show *what the tale of the tape looked like before the fight* (point-in-time) or current career numbers? (The PRD assumes point-in-time for past bouts.)
