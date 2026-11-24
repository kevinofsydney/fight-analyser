# Fight Analyser

A personal UFC analysis app. It keeps every UFC fight since the 1990s in a local database, refreshes it daily, and uses it to:

- **Predict upcoming fights:** who wins, how (KO/TKO, submission, decision) and whether it goes the distance, and explain why.
- **Compare against the bookmakers:** where the model and the odds disagree, and suggested bets when the gap is big enough.
- **Test your own ideas:** "do fighters 8+ years older lose more often at lightweight?" with proper sample sizes and confidence intervals.
- **Track how it's doing:** card-by-card results, betting results and calibration, all out of sample.

**Live app:** https://fight-analyser.streamlit.app/ (the analysis pages are public; betting tools, picks, SQL and Status need the owner password).

## What's in the app

| Page | What you get |
|------|--------------|
| **Fight Cards** | Upcoming cards with the model's win chances and a 1–5 star bet-opportunity rating. Click a fighter for their page, anything else for the bout. |
| **Bout** | Everything about one fight: fighter cards, odds, the model's prediction and how it got there (who wins, then how), expected fight stats (strikes, takedowns, control time), suggested bets in units, saved patterns that apply and what they mean. For past fights, how the prediction compared with what happened. |
| **Previous cards** | How the model and the bookmakers did on each finished card, e.g. last weekend's. |
| **Model performance** | The model's long-run record: winners picked, calibration, the last three cards, and what betting $100 a pick or sizing bets by edge would have made. |
| **Experiments** | Every idea tested on the models, including the ones that didn't help, and why. |
| **Value picks** | Fights where the model rates a fighter well above the bookmakers' price, and how those picks have done. |
| **My picks** | Enter your own picks before a card and see how you score against the model. |
| **Fighter** | A fighter's record, fight history, trends and recorded damage. |
| **Bout search / Matchup** | Find any past fight, or pit any two fighters against each other. |
| **Hypothesis Lab / Saved hypotheses** | Build and save questions about fight history. |
| **SQL / Status** | A read-only database console, and the health of the daily refresh. |

## How the model works (short version)

1. **Who wins.** A logistic regression compares the two fighters on about 20 things: ratings (an Elo across every pro promotion, a UFC Elo and a rating built from how much of each fight they won), age, reach, experience, current streak, and style indices for striking, wrestling, submissions and durability, adjusted for the strength of past opponents.
2. **How.** A second model splits each fighter's win chance between KO/TKO, submission and decision, from how they've won and lost across every promotion, knockdown power, durability and similar inputs.
3. **No peeking.** Every number for a past fight uses only fights before it, and the backtest trains on earlier years only.

Since 2012 it picks about **64%** of winners. The bookmakers' closing favourite wins about **67%**, so the market is still the better predictor. Treat the model as a second opinion, not betting advice.

## What we've tested

Ideas get tested before they go into the model. A change ships only if it improves out-of-sample accuracy by more than chance. The **Experiments** page in the app explains each one; full numbers are in [docs/NEXT_TASKS.md](docs/NEXT_TASKS.md).

| Idea | Result |
|------|--------|
| All-promotion rating (pre-UFC record) | **Shipped**, biggest single gain, especially for debutants |
| Rating built from share of each fight won | **Shipped**, small gain |
| Stronger opponent adjustment | **Shipped** |
| How a fighter wins and loses across every promotion (method model) | **Shipped**: the method model's biggest gain, and fixed its KO/TKO calibration |
| Momentum (last 1–3 results) | Real but small, and already explained by fighter quality. No gain. |
| Peak age around 30–33 | Not supported. In every division, younger fighters win more often, even after allowing for experience. The model's straight age penalty is the right shape. |
| Ring rust (time since last fight) | No usable signal. Fighters out 18–24 months under-performed, but not those out longer, and adding layoff to the model didn't help. |
| Gradient boosting, recency weighting, many extra stats | No gain, or broke calibration |
| Fight wear (damage absorbed) | Shown on fighter pages; kept out of the model by owner decision |

## Run it yourself

You need [uv](https://docs.astral.sh/uv/), which installs Python 3.14 for you.

```bash
cp .env.example .env   # optional; the defaults work
make setup             # install dependencies
make backfill          # download the fight history and build everything
make app               # open http://localhost:8501
```

Other useful commands: `make refresh` (the daily update), `make check` (lint, types, tests), `make errors` (logged errors). On Windows without `make`, run the same things through `uv run`; see [docs/NEXT_TASKS.md](docs/NEXT_TASKS.md).

Upcoming bookmaker odds need a free key from [The Odds API](https://the-odds-api.com/) in `ODDS_API_KEY`. Everything else works without it.

## Hosting

GitHub Actions rebuilds the database every day and Streamlit Community Cloud serves the app. It can also run on a home server with Docker. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## More detail

- [docs/NEXT_TASKS.md](docs/NEXT_TASKS.md): where things stand, every experiment and what's next
- [docs/PRD.md](docs/PRD.md): what we're building and why
- [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md): where the data comes from
- [docs/ANALYSIS_IDEAS.md](docs/ANALYSIS_IDEAS.md): the stats and question catalogue
- [AGENTS.md](AGENTS.md): rules for contributors, human or AI

## Data and licence

Fight history, round stats and closing odds come from [fight-canon-db](https://github.com/Jadevit/fight-canon-db), with [Greco1899/scrape_ufc_stats](https://github.com/Greco1899/scrape_ufc_stats) as a fallback (both originate from UFC Stats). Upcoming cards come from ESPN's public feed and Wikipedia, and upcoming odds from The Odds API. Code is [MIT](LICENSE); the data belongs to its sources. For personal, non-commercial analysis. Model numbers are not betting advice.
