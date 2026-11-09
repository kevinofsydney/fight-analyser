# Next Tasks

Updated 2026-11-09. Every item from the post-v1 review is done (table below). Nothing is open; the one thing left is a deployment that doesn't exist yet (see below).

**Windows note:** without `make`, run `make check` as:
```bash
export PYTHONPATH=src
uv run ruff check src tests scripts && uv run ruff format --check src tests scripts && uv run mypy src && uv run pytest -q
```

---

## Owner decisions / human-only

Done 2026-11-09: MIT license added, every row in [DECISIONS.md](DECISIONS.md) marked decided (recommended options), branch protection on `main`, `task/next-tasks` merged (closes issue #1). The B1 contract change (`sources/models.py`: source-neutral upcoming IDs) went in under the owner's "fix all outstanding issues" instruction.

- **Starter saved hypotheses (D27):** done. `load_all` reads `hypotheses/` recursively, so the five ★ questions in `hypotheses/examples/` (age gap, reach, TD vs TDD, sub specialist, short notice) are already saved and matched against every upcoming bout. A hypothesis the database can't evaluate is now reported and skipped instead of breaking `run-all`, the Saved Hypotheses page or the Bout page.
- **Deployment (T8.x):** the free path's data side works (the Daily refresh publishes `data-latest`; last asset 2026-11-08 16:51 UTC). **No public app is deployed yet:** the repo has no homepage, deployments or environments, so there's no `APP_PASSWORD` to check. Deploying to Streamlit Community Cloud needs the owner's Streamlit account: follow [DEPLOYMENT.md §B](DEPLOYMENT.md), set `DATA_RELEASE_URL` to the `data-latest` asset and set `APP_PASSWORD` (D16).

## Calibration gate (resolved)

The "low / medium / high" labels were hidden by a bug in the gate, not the model: the six-class reliability check counted every probability twice (A's "loses by decision" is B's "wins by decision"), doubling bin sizes so a sparse 47-fighter bin looked like n≈100+. The gate now checks what the Bout page shows (each fighter's "wins by KO / submission / decision", counted once) pooled and per method, and gates each method's label separately. On 2024–2026 every bin with n ≥ 100 is within 3.5 pp for all three methods, so labels show. Watch: big favourites winning on points (0.5–0.6 bin, 53% predicted, 64% observed, n=47); if that bin passes 100 fighters and stays off, the gate hides only the decision label.

---

## Done on 2026-11-09 (branch `task/next-tasks`)

| ID | What changed |
|----|--------------|
| N1 | Every text file read/write uses `encoding="utf-8"` (4 tests failed on Windows). |
| N2 | Canary (issue #1): two upstream control-time disagreements (1 s, 4 s), not an adapter bug. Cross-source test tolerates ≤5 s control-time drift and `max(2, 1%)` differing rows, and names them on failure. |
| N3 | Actions on Node 24: `actions/checkout@v7`, `astral-sh/setup-uv@v10.3.3`, `gitleaks/gitleaks-action@v3` (v2 stopped running on 2026-09-16). |
| N4 | New tests: download client, stats edge cases, schema, backfill idempotency and failover, notifications, every hypothesis outcome, `between`, `division_rate`, `matches_upcoming`. |
| N5 | Data-quality gate before the new DB is swapped in (`pipeline/quality.py`, `DQ_*` settings): two different fighters per bout, completed bouts and snapshots never shrink, ≤15% missing reach since 2015 (5.5% today), ≥95% round-stat coverage since 2010 (100%). |
| N6 | Method model evaluated walk-forward (`method_multinomial` in `model_evals`, Status page, `fightdb model eval`); labels gated on `outcome.is_calibrated`. Reliability is measured over both sides of each bout, since the models are corner-blind by design (FR-15). |
| N7 | `idx_sub_def` (submission defence index); overdispersion correction skipped for it because a sub loss ends the fight (k ≈ 16 instead of the 1000 cap). |
| N8 | `self/opp_days_notice` and `_short_notice` per fighter; NULL for cards set before tracking began. Example `short-notice.yaml`. |
| N9 | Two identity overrides (Duclos, Osmanli); five debutants left unmatched. Card diff no longer reports a newly identified fighter as a change. |
| N10 / B1 | **Wikipedia fallback** (`sources/wikipedia.py`): upcoming models are source-neutral; on ESPN failure Wikipedia adds missing bouts only (matched by date and names, with name-order and short-name tolerance) and never cancels; stored bouts it lacks become warnings; ESPN's return folds fallback events and bouts in silently. Canary checks the live pages. |
| N11 | README walkthrough, late-cron note, Windows commands; BUILD_PLAN and DATA_SOURCES updated. |
| S1 | **Strength of schedule**: striking and wrestling indices adjusted for opponents' pre-fight quality (minutes-weighted, shrunk to neutral, re-centred on the trailing 3-year league average). Win model walk-forward Brier 0.2334 → 0.2322, log loss 0.659 → 0.6562, still calibrated. |
| S1 (found while testing) | **Look-ahead fix**: shrinkage prior strengths were fitted on final career totals, so past snapshots shifted slightly with later fights. Now fitted walk-forward per calendar year; a test rewrites all fights after a cutoff and asserts no earlier snapshot changes. |
| S2 | **Judge scores** imported (`judge_scores`, 71,518 UFC round scores, 467 judges) with a `judge_rounds` view (margin next to strike, control, takedown and knockdown differences) for questions 28–29. |
| S3 | Method model gained finish-tendency and distance-rate features (log loss 1.6334 → 1.6237); the calibration gate was fixed to count each probability once and gate each method separately (see above). |
