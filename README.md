# Fight Analyser

A UFC fight database and analysis tool:

- **Hypothesis Lab:** test ideas like *"in men's lightweight, when one fighter is 8+ years older, how often do they win?"* and get sample sizes, 95% confidence intervals, p-values and a threshold sweep, not just a percentage.
- **Tale of the tape:** for every upcoming bout, see who has the better takedown defence, the longer reach (and by how much), and 40+ other point-in-time stats with the edge highlighted.
- **xG-style projections:** expected takedowns, strikes, control time and knockdowns per round; win and method-of-victory probabilities from a calibrated model.
- **Daily refresh:** new results and upcoming cards re-sync once a day, and card changes (cancellations, replacements, moved bouts) are logged.

## Quick start

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`).

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
| `uv run fightdb model eval` | Walk-forward calibration of the win model, year by year |
| `uv run fightdb db status` | Row counts and recent refresh runs |
| `make check` | Lint, typecheck, tests |

On macOS, run Python commands through `make` or prefix them with `PYTHONPATH=src` (macOS can hide uv's editable-install file, so Python skips it).

## What's in the app

| Page | |
|------|--|
| **Home** | Upcoming events and bouts with model odds, age and reach gaps; card changes in the last 7 days |
| **Bout** | Tale of the tape, model outlook (win / KO / sub / decision vs division average), per-round projections with "show the maths", matching saved hypotheses, common opponents |
| **Hypothesis Lab** | Form builder, result with confidence interval, sweep chart, matching bouts, YAML to save |
| **Saved Hypotheses** | Every YAML in `hypotheses/`, its latest result and which upcoming bouts it applies to |
| **Fighter** | Profile, UFC history, stat trends as of each fight |
| **SQL** | Read-only console (no file or network access) over the DuckDB database |
| **Status** | Refresh runs, unmatched fighters, model calibration vs the closing betting line |

Saved hypotheses are plain YAML. Five examples are in [`hypotheses/examples/`](hypotheses/examples/).

## Hosting

Runs on a homelab (Docker Compose with a cron refresher) or for free (GitHub Actions builds the database daily; Streamlit Community Cloud or Hugging Face serves the app). Step-by-step instructions are in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). No secrets are needed; optional ones (`APP_PASSWORD`, `DISCORD_WEBHOOK_URL`) go in `.env` or the host's secret store.

## Docs
- [PRD](docs/PRD.md): what we're building and why
- [Data sources](docs/DATA_SOURCES.md): where the data comes from and how it refreshes
- [Analysis ideas](docs/ANALYSIS_IDEAS.md): xG-style metrics, matchup models, question catalogue
- [Build plan](docs/BUILD_PLAN.md): tasks for coding agents, and what is built so far
- [Deployment](docs/DEPLOYMENT.md): homelab and free hosting options
- [Decisions](docs/DECISIONS.md): open choices for the owner
- [AGENTS.md](AGENTS.md): rules for anyone (human or AI) contributing

## Data credits
- [fight-canon-db](https://github.com/Jadevit/fight-canon-db) (MIT): fight history, round stats, odds
- [Greco1899/scrape_ufc_stats](https://github.com/Greco1899/scrape_ufc_stats) (GPL-3.0 code; we use its data files): fallback history
- ESPN's public scoreboard feed: upcoming cards

Both datasets originate from [UFC Stats](http://ufcstats.com). Used for personal, non-commercial analysis. Model probabilities are not betting advice.
