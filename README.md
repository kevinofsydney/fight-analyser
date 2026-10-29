# Fight Analyser

A UFC fight database and analysis tool:

- **Hypothesis Lab:** test ideas like *"in men's lightweight, when one fighter is 8+ years older, how often do they win?"* and get sample sizes, confidence intervals and significance, not just a percentage.
- **Tale of the tape:** for every upcoming bout, see who has the better takedown defence, the longer reach (and by how much), and more.
- **Daily refresh:** the upcoming card re-syncs once a day and logs any card changes.

> Status: planning. See the docs below.

## Docs
- [PRD](docs/PRD.md): what we're building and why
- [Data sources](docs/DATA_SOURCES.md): where the data comes from and how it refreshes
- [Analysis ideas](docs/ANALYSIS_IDEAS.md): xG-style metrics, matchup models, question catalogue
- [Build plan](docs/BUILD_PLAN.md): tasks for coding agents, in dependency order
- [Deployment](docs/DEPLOYMENT.md): homelab and free hosting options
- [Decisions](docs/DECISIONS.md): open choices for the owner
- [AGENTS.md](AGENTS.md): rules for anyone (human or AI) contributing

## Quick start (once built)
```bash
cp .env.example .env
make setup
make backfill
make app
```

## Data credits
- [fight-canon-db](https://github.com/Jadevit/fight-canon-db) (MIT): fight history, round stats, odds
- [Greco1899/scrape_ufc_stats](https://github.com/Greco1899/scrape_ufc_stats) (GPL-3.0 code; we use its data files): fallback history
- ESPN's public scoreboard feed: upcoming cards

Both datasets originate from [UFC Stats](http://ufcstats.com). Used for personal, non-commercial analysis.
