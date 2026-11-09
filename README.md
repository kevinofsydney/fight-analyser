# Fight Analyser

A UFC fight database and analysis tool:

- **Hypothesis Lab:** test ideas like *"in men's lightweight, when one fighter is 8+ years older, how often do they win?"* and get sample sizes, 95% confidence intervals, p-values and a threshold sweep, not just a percentage.
- **Tale of the tape:** for every upcoming bout, see who has the better takedown defence, the longer reach (and by how much), and 40+ other point-in-time stats with the edge highlighted.
- **xG-style projections:** expected takedowns, strikes, control time and knockdowns per round; win and method-of-victory probabilities from a calibrated model.
- **Daily refresh:** new results and upcoming cards re-sync once a day, and card changes (cancellations, replacements, moved bouts) are logged.

**Live app:** https://fight-analyser.streamlit.app/ (password-protected). **Status and what's left to build:** [docs/NEXT_TASKS.md](docs/NEXT_TASKS.md).

## Quick start

Needs [uv](https://docs.astral.sh/uv/) (it installs Python 3.14 for you) (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`).

```bash
cp .env.example .env   # optional; defaults work. .env is gitignored.
make setup             # install dependencies
make backfill          # download ~150 MB of history + upcoming cards, build everything (~30 s)
make app               # http://localhost:8501
```

Other commands:

| Command | What it does |
|---------|--------------|
| `make refresh` | The daily job: new results, upcoming cards, card changes, retrain models |
| `make hypotheses` | Run every saved hypothesis in `hypotheses/` |
| `uv run fightdb hypothesis run hypotheses/examples/lw-older-by-8.yaml --sweep` | Run one hypothesis with a sweep |
| `uv run fightdb model eval` | Walk-forward calibration of the win and method models, year by year |
| `uv run fightdb db status` | Row counts and recent refresh runs |
| `make check` | Lint, typecheck, tests |
| `make errors` | Every logged error, grouped by cause (see [AGENTS.md](AGENTS.md#fixing-errors)) |

On Windows without `make`, run the same steps through `uv run` (for `make check`: `uv run ruff check src tests scripts`, `uv run ruff format --check src tests scripts`, `uv run mypy src`, `uv run pytest`).

On macOS, run Python commands through `make` or prefix them with `PYTHONPATH=src` (macOS can hide uv's editable-install file, so Python skips it).

## What's in the app

| Page | |
|------|--|
| **Home** | Upcoming events and bouts with model odds, age and reach gaps; card changes in the last 7 days |
| **Bout** | Tale of the tape, model outlook (win / KO / sub / decision vs division average; "low / medium / high" labels for each method whose probabilities pass the calibration check), per-round projections with "show the maths", matching saved hypotheses, common opponents |
| **Hypothesis Lab** | Form builder, result with confidence interval, sweep chart, matching bouts, YAML to save |
| **Saved Hypotheses** | Every YAML in `hypotheses/`, its latest result and which upcoming bouts it applies to |
| **Fighter** | Profile, UFC history, stat trends as of each fight |
| **SQL** | Read-only console (no file or network access) over the DuckDB database, including `judge_rounds` (every judge's round score next to that round's stats) |
| **Status** | Refresh runs (including data-quality failures and Wikipedia fallback days), unmatched fighters, model calibration vs the closing betting line |

The Fighter page includes a fight-wear profile based on recorded UFC round statistics. It counts significant strikes and knockdowns absorbed and fights with 50+, 75+, or 100+ significant strikes absorbed. Incomplete round statistics make totals unavailable; these numbers describe recorded exposure, not a medical diagnosis. The Hypothesis Lab and saved examples can explore these measures, prior KO/TKO losses, five-round wars, division moves, and stance matchups. Division direction is unknown for debutants and catchweights. Historical stance uses the currently recorded stance and may not reflect changes over time.

Saved hypotheses are plain YAML. Examples are in [`hypotheses/examples/`](hypotheses/examples/).

### Your first hypothesis in 60 seconds

1. Open **Hypothesis Lab**.
2. Population: pick **lightweight**, sex **M**, leave the date from 2010.
3. Condition: **Of** = "difference (this fighter − opponent)", **Stat** = Age, `>=` **8**. That reads "this fighter is at least 8 years older than the opponent".
4. Outcome **won**, compare to **50% (coin flip)**, then run it.
5. Read the verdict: the win rate, its 95% confidence interval, the gap from 50% in both percentage points and relative %, and the p-value. Below 30 matching bouts it warns you it's an anecdote.
6. Look at the **sweep** chart (on by default; it varies the first condition's threshold). If the line wobbles around 50% with wide bands, "8 years" was a cherry-picked cutoff.
7. Like it? Copy the YAML into `hypotheses/` and commit it. It then shows up on every upcoming bout it applies to.

Every number is point-in-time: a 2015 bout only sees fights before it. Each hypothesis you run in a session counts toward a multiple-comparisons reminder.

## Fight odds

The Bout page shows historical closing price ranges where fight-canon-db has them. To show upcoming Australian bookmaker prices, obtain an API key from [The Odds API](https://the-odds-api.com/) and set `ODDS_API_KEY` in your local `.env` or as a GitHub Actions repository secret. The daily refresh makes one request for Australian head-to-head odds, matches both fighters and event date, and records when the quotes were fetched. Without a key, upcoming odds are unavailable and the rest of the app continues to work. Prices can change between refreshes.

## Hosting

Runs on a homelab (Docker Compose with a cron refresher) or for free (GitHub Actions builds the database daily; Streamlit Community Cloud or Hugging Face serves the app). GitHub starts scheduled workflows late: the 11:00 UTC refresh has been starting between 16:30 and 18:30 UTC, so on the free path cards can be up to about 8 hours older than the cron suggests. The homelab cron runs on time. Step-by-step instructions are in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). No secrets are needed; optional ones (`APP_PASSWORD`, `DISCORD_WEBHOOK_URL`) go in `.env` or the host's secret store.

## Docs
- [PRD](docs/PRD.md): what we're building and why
- [Data sources](docs/DATA_SOURCES.md): where the data comes from and how it refreshes
- [Analysis ideas](docs/ANALYSIS_IDEAS.md): xG-style metrics, matchup models, question catalogue
- [Build plan](docs/BUILD_PLAN.md): tasks for coding agents, and what is built so far
- [Deployment](docs/DEPLOYMENT.md): homelab and free hosting options
- [Decisions](docs/DECISIONS.md): open choices for the owner
- [AGENTS.md](AGENTS.md): rules for anyone (human or AI) contributing

## License
Code: [MIT](LICENSE). The license covers this repository's code, not the upstream data (see below).

## Data credits
- [fight-canon-db](https://github.com/Jadevit/fight-canon-db) (MIT): fight history, round stats, odds
- [Greco1899/scrape_ufc_stats](https://github.com/Greco1899/scrape_ufc_stats) (GPL-3.0 code; we use its data files): fallback history
- ESPN's public scoreboard feed: upcoming cards
- [Wikipedia](https://en.wikipedia.org/wiki/List_of_UFC_events) (CC BY-SA), via the MediaWiki API: upcoming cards when ESPN is down
- Judges' scorecards via fight-canon-db (from mmadecisions.com and ufc.com)

Both datasets originate from [UFC Stats](http://ufcstats.com). Used for personal, non-commercial analysis. Model probabilities are not betting advice.
