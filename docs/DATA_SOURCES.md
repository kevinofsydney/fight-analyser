# Data Sources

Research done 2026-10-29: where Fight Analyser gets its data, and why.

## TL;DR: recommended strategy

| Need | Primary source | Fallback | How we pull it |
|------|----------------|----------|----------------|
| Completed fights, round-by-round stats, fighter bios, results | **[fight-canon-db](https://github.com/Jadevit/fight-canon-db)** SQLite release (`canon.db`) | **[Greco1899/scrape_ufc_stats](https://github.com/Greco1899/scrape_ufc_stats)** CSVs | Download one file per day; no scraping |
| Historical betting odds (for calibration, not betting) | fight-canon-db `odds` table | none needed (nice-to-have) | Same file |
| **Upcoming cards** | **ESPN's public JSON** (`site.api.espn.com/.../mma/ufc/scoreboard`) | Wikipedia (MediaWiki API) event pages | One small JSON request per event |

**Don't scrape UFCStats.com ourselves.** As of 2026-10-29, every UFCStats page (events, fights, upcoming) returns a JavaScript "Checking your browser…" challenge to plain HTTP clients. Getting past it would mean evading bot detection, which we won't do. Two GitHub projects already publish that data every day, so we consume theirs.

Daily pipeline:

```
10:00 UTC  fight-canon-db's own GitHub Action publishes a new canon.db release
11:00 UTC  our refresh job:
           1. download canon.db (latest release; ~150 MB)       → import completed history
           2. if that fails or is stale > 7 days → Greco1899 CSVs (raw.githubusercontent.com)
           3. fetch ESPN scoreboard for every future event in its calendar → upcoming cards
           4. match ESPN fighters → our fighter IDs (identity table)
           5. card diff, snapshots, features, matchup projections
```

## Source evaluations

### 1. fight-canon-db (primary for history) ✅
- **What:** a SQLite DB of MMA events, fights and round-by-round stats. UFC data comes from UFC Stats, plus Sherdog pro records, PFL/Oktagon/Sofascore stats, BestFightOdds odds lines and official judges' scorecards.
- **Freshness (verified):** a GitHub Action runs daily at 10:00 UTC and reloads the last 21 days to catch late corrections. Latest release `db-2026-10-28-1829`. UFC data runs through **UFC 333 (2026-10-24)**.
- **UFC coverage (verified by querying the release):** 8,971 UFC fights (1993-11-12 → 2026-10-24), 42,074 UFC round-stat rows, and **odds for 8,021 UFC fights** (opening and closing lines, 2007+).
- **How to pull:** `curl -L -o canon.db https://github.com/jadevit/fight-canon-db/releases/latest/download/canon.db`. Every update is a release tagged `db-YYYY-MM-DD-HHMM`, so a specific version can be pinned.
- **Pluses:** MIT licence, **stable numeric IDs** with per-source alias tables (`fighter_aliases.source = 'ufcstats'` and others), NULL means "not recorded" (not 0), a guard against publishing updates that lose rows, documented schema, and it explicitly warns that `corner` before 2010-03-21 leaks the winner.
- **Risks:** a **young repo** (created 2026-09-27) with a single maintainer and no forks yet. It's also a big file (~150 MB) because it covers ~60k events across all promotions. We filter to `promotion = 'UFC'` when importing.
- **Units:** height/reach in **inches** (integers); dates are `YYYY-MM-DD` strings.

### 2. Greco1899/scrape_ufc_stats (fallback for history) ✅
- **What:** six CSVs scraped from UFC Stats: `ufc_event_details`, `ufc_fight_details`, `ufc_fight_results`, `ufc_fight_stats` (per round), `ufc_fighter_details`, `ufc_fighter_tott` (height, reach, stance, DOB).
- **Freshness (verified):** the README says a Cloud Run job on GCP checks daily and pushes when something changed. Commit history shows "data refresh" commits several times a week, latest **2026-10-25** (includes UFC 333). The last full re-scrape was 2025-11-22.
- **Track record:** created 2022-01, 171 stars, 51 forks, 0 open issues. This is the most established public UFC dataset.
- **How to pull:** `https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main/<file>.csv` (about 12 MB total), or a shallow `git clone --depth 1`.
- **Minuses:** joins on **names and URLs, not clean IDs** (`ufc_fight_stats` has a fighter *name* per row), so we derive IDs from the `fighter-details/<id>` URLs. Values are strings like `"24 of 47"` and `"5' 11\""`, so they need parsing. Licence is **GPL-3.0**: fine for reading the data, but don't copy their scraper code into this repo unless we accept GPL. No odds, no upcoming cards.

### 3. ESPN public JSON API (primary for upcoming cards) ✅
- **Endpoint:** `https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard`, optionally with `?dates=YYYYMMDD`.
- **Verified on 2026-10-29:** returns the next event (UFC Fight Night: Moicano vs. Nolan, 12 bouts) with each bout's weight class, status (`STATUS_SCHEDULED`), both fighters with ESPN athlete IDs and records, and card order. `leagues[0].calendar` lists the upcoming events (5 future events through UFC 335 on 2026-12-13). `?dates=20261107` returned the following card (14 bouts).
- **Pluses:** no key, JSON, includes card order and status, updates quickly when bouts change.
- **Risks:** **undocumented and unofficial**, so it can change or vanish without notice. We wrap it in an adapter with strict validation and fail loudly. We also need an **identity-matching step**, because ESPN IDs aren't in fight-canon-db's aliases.

### 4. Other options considered

| Source | Verdict | Notes |
|--------|---------|-------|
| Scraping UFCStats.com directly | ❌ | JS browser challenge on every page (verified 2026-10-29). We won't bypass it. |
| `shortlikeafox/ultimate_ufc_dataset` (Apache-2.0) | ⚠️ Reference only | Pre-aggregated features plus odds and an `upcoming.csv`, but updated irregularly (last push 2026-04-01). Useful for checking our feature engineering. |
| `jansen88/ufc-data` | ❌ | Stale (2023). |
| `balaustrada/ufcscraper` | ❌ | A scraper package (last release 2025-08). It would hit the same UFCStats challenge. |
| Kaggle UFC datasets | ❌ | Mostly stale snapshots; Kaggle downloads need an API key. |
| UFCalendar Fight API | 🤔 v2 option | Commercial API with Python SDK (events, cards, per-round stats, judges' scores, rankings history). Needs an API key (a secret in `.env`); check pricing. A good paid fallback if the free sources die. |
| Apify UFC scraper actors | ❌ | Pay-per-run hosted scrapers. Unnecessary. |
| Wikipedia (MediaWiki API) | ✅ Fallback for upcoming cards | Stable, documented API, CC BY-SA content; "UFC on ESPN X" pages list scheduled bouts. Parsing wikitext tables is fiddly but robust. |
| UFC.com | 🤔 | Official card order, but HTML scraping. Consider only if ESPN and Wikipedia both fail. |

## Design consequences

1. **Ingestion becomes adapters, not scrapers.** Each source has an adapter that converts its data into our own schema (PRD §6.2). The rest of the app never sees source formats, so swapping the primary source is a config change: `HISTORY_SOURCE=fight_canon|greco`.
2. **Snapshot what we import.** Our refresh publishes our own DuckDB file (free path: our `data-latest` release; homelab: a volume). If an upstream repo disappears, we keep everything up to the last good import.
3. **Identity resolution is a first-class task.** Upcoming bouts (ESPN) must map to fighter IDs (fight-canon / UFCStats). Order of matching: exact normalised name + weight class → fuzzy name (accents, nicknames, "Jr.") with record cross-check → manual `data/overrides/fighter_identity.yaml` (committed; no secrets). Unmatched fighters (typically UFC debutants) appear with "no UFC history".
4. **Cross-source sanity check.** Weekly, compare fight-canon and Greco counts and results for the last 90 days. Disagreements open a GitHub issue. This also tells us early if the primary has stopped updating.
5. **Respect upstreams.** One download per day, cached, with a descriptive User-Agent. Credit both repos in the README. Consider starring them, and opening issues upstream rather than working around bad data.

## Freshness monitoring (built into the refresh job)

- Fail over to Greco if `canon.db`'s newest UFC event is older than Greco's newest event, or if the canon release is more than 7 days old.
- Warn in the UI if the newest completed UFC event in our DB is older than the most recent completed event on ESPN's calendar plus 3 days.
- Record `source`, `source_version` (release tag or commit SHA) and `max_event_date` in every `scrape_runs` row.
