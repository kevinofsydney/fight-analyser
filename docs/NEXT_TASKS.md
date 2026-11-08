# Next Tasks

Updated 2026-11-09. Every engineering item from the post-v1 review is done on branch `task/next-tasks` (table below). What remains needs the owner, or is a model limitation we chose not to tune away.

**Windows note:** without `make`, run `make check` as:
```bash
export PYTHONPATH=src
uv run ruff check src tests scripts && uv run ruff format --check src tests scripts && uv run mypy src && uv run pytest -q
```

---

## Owner decisions / human-only

1. **Fill in the Decision column in [DECISIONS.md](DECISIONS.md).** Every row is still ⬜, although the build uses the recommended defaults.
2. **License (D19).** There's no `LICENSE` file, so the public repo is "all rights reserved" by default.
3. **Branch protection (D21):** require CI to pass on `main` (GitHub settings).
4. **Starter saved hypotheses (D27):** `hypotheses/` only has `examples/`.
5. **Deployment checks (T8.x):** confirm the public instance serves the `data-latest` DB and `APP_PASSWORD` is set where the app is public.
6. **Review and merge `task/next-tasks`**, then close GitHub issue #1 (source canary), which it fixes.
7. **B1 contract change was made under the "fix all outstanding issues" instruction** (`sources/models.py`: `UpcomingEvent.source`, `source_event_id`, `source_bout_id`, `source_fighter_id`). ESPN IDs and stored data are unchanged. Review it in the PR.

## Known model limitation (not a bug)

**Method-of-victory labels are hidden.** The method model is calibrated in every reliability bin except one: pooled over 2024–2026, its 0.5–0.6 bin predicts about 54% and observes about 67% (n=130), mostly big favourites winning on points. The calibration gate (FR-31) therefore hides the Bout page's "low / medium / high" labels; the probabilities themselves are still shown. Temperature, Platt and isotonic recalibration, recency weighting, weaker regularisation, a year trend and interaction terms were all tried walk-forward and none closed the gap; tuning further against the same three years would just fit the evaluation. Revisit when more 2026–2027 results arrive, or if the owner wants a different label policy.

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
| S3 | Method model gained finish-tendency and distance-rate features (log loss 1.6334 → 1.6237); see the known limitation above. |
