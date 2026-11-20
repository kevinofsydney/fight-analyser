"""Activity: does fighting often help, separately from whether those fights were won?

    python scripts/activity_experiment.py [--db path/to/fightdb.duckdb]

Prints Markdown. For every bout, each fighter's fight days in the 12 and 24 months before it
are counted across all promotions (pro records, plus UFC history for fighters not linked to
one), with recent wins and losses counted separately so activity can be told apart from form.
Part 1 compares win rates with the model's out-of-sample expectation and the bookmakers' by
how active each fighter was. Part 2 adds activity inputs to the walk-forward win model (with
the live model's recency weights), with paired bootstrap intervals on the log-loss change.
Part 3 shows what each variant would say for the upcoming bouts named on the command line.

Activity only: strikes absorbed, KO losses and other fight-wear inputs are out of scope
(AGENTS.md). Every input uses only fights dated strictly before the bout.
"""

from __future__ import annotations

import argparse

import duckdb
import numpy as np
import pandas as pd
from momentum_experiment import _boot

from fightdb.config import get_settings
from fightdb.models import outcome

WINDOWS = {"12m": 365, "18m": 545, "24m": 730}


def _load(db: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = duckdb.connect(db, read_only=True)
    try:
        sides = con.execute(
            "SELECT * FROM bout_sides WHERE status IN ('completed', 'scheduled')"
        ).df()
        pro = con.execute("SELECT date, fighter_a_id, fighter_b_id, result_a FROM pro_fights").df()
    finally:
        con.close()
    return sides, pro


def _history(sides: pd.DataFrame, pro: pd.DataFrame) -> pd.DataFrame:
    """One row per fighter per fight day: 1 if won, -1 if lost, 0 otherwise."""
    flip = {"W": "L", "L": "W"}
    a = pro.rename(columns={"fighter_a_id": "fighter_id"})[["date", "fighter_id", "result_a"]]
    b = pro.rename(columns={"fighter_b_id": "fighter_id"})[["date", "fighter_id", "result_a"]]
    b = b.assign(result_a=b["result_a"].map(flip).fillna(b["result_a"]))
    p = pd.concat([a, b], ignore_index=True)
    res = p["result_a"]
    p = p.assign(outcome=np.select([res == "W", res == "L"], [1, -1], 0))[
        ["fighter_id", "date", "outcome"]
    ]
    u = sides[sides["status"] == "completed"]
    u = pd.DataFrame(
        {
            "fighter_id": u["fighter_id"],
            "date": u["event_date"],
            "outcome": u["won"].fillna(0).astype(int) - u["lost"].fillna(0).astype(int),
        }
    )
    h = pd.concat([p, u], ignore_index=True)
    h["date"] = pd.to_datetime(h["date"])
    h["fighter_id"] = h["fighter_id"].astype(str)
    return h.drop_duplicates(["fighter_id", "date"], keep="first").sort_values(
        ["fighter_id", "date"]
    )


def add_activity(sides: pd.DataFrame, pro: pd.DataFrame) -> pd.DataFrame:
    """Per side: fights, wins and losses in each window before the bout (strictly earlier)."""
    hist = _history(sides, pro)
    by_f = {
        f: (g["date"].to_numpy(dtype="datetime64[D]"), g["outcome"].to_numpy())
        for f, g in hist.groupby("fighter_id", sort=False)
    }
    s = sides.copy()
    dates = pd.to_datetime(s["event_date"]).to_numpy(dtype="datetime64[D]")
    fids = s["fighter_id"].astype(str).to_numpy()
    out = {f"{k}_{w}": np.zeros(len(s)) for k in ("fights", "wins", "losses") for w in WINDOWS}
    for i, (f, d) in enumerate(zip(fids, dates, strict=True)):
        got = by_f.get(f)
        if got is None:
            continue
        ds, oc = got
        end = np.searchsorted(ds, d, side="left")
        for w, days in WINDOWS.items():
            start = np.searchsorted(ds, d - np.timedelta64(days, "D"), side="left")
            o = oc[start:end]
            out[f"fights_{w}"][i] = len(o)
            out[f"wins_{w}"][i] = (o == 1).sum()
            out[f"losses_{w}"][i] = (o == -1).sum()
    for k, v in out.items():
        s[f"self_{k}"] = v
    opp = s[["bout_id", "fighter_id", *[f"self_{k}" for k in out]]].rename(
        columns={"fighter_id": "opp_key", **{f"self_{k}": f"opp_{k}" for k in out}}
    )
    n = len(s)
    s["opp_key"] = s.groupby("bout_id")["fighter_id"].transform(lambda x: x.iloc[::-1].to_numpy())
    s = s.merge(opp, on=["bout_id", "opp_key"], how="left").drop(columns="opp_key")
    assert len(s) == n
    for side in ("self", "opp"):
        age = s[f"{side}_age_years"]
        s[f"{side}_log_fights_24m"] = np.log1p(s[f"{side}_fights_24m"])
        # Any promotion, so a UFC debutant who fought elsewhere recently counts as active.
        s[f"{side}_active_18m"] = (s[f"{side}_fights_18m"] > 0).astype(float)
        s[f"{side}_ufc_debut"] = s[f"{side}_days_since_last"].isna().astype(float)
        s[f"{side}_fights_24m_x_old"] = s[f"{side}_fights_24m"] * np.maximum(0, age - 33)
    extra = [
        *out,
        "log_fights_24m",
        "active_18m",
        "ufc_debut",
        "fights_24m_x_old",
    ]
    for f in extra:
        s[f"diff_{f}"] = s[f"self_{f}"] - s[f"opp_{f}"]
    return s


def activity_table(s: pd.DataFrame) -> str:
    d = s[
        (s["status"] == "completed")
        & (s["year"] >= outcome.FIRST_TEST_YEAR)
        & ((s["won"] + s["lost"]) == 1)
        & s["self_p_win"].notna()
    ]
    bands = pd.cut(d["self_fights_24m"], [-1, 0, 1, 2, 3, 100], labels=["0", "1", "2", "3", "4+"])
    lines = [
        "| Fights in the 24 months before | Fighters | Won | Model expected | Gap | "
        "Bookmakers expected | Won, priced bouts |",
        "|---|---|---|---|---|---|---|",
    ]
    for band, g in d.groupby(bands, observed=True):
        m = g[g["self_implied_prob"].notna()]
        gap = g["won"].mean() - g["self_p_win"].mean()
        se = np.sqrt((g["self_p_win"] * (1 - g["self_p_win"])).sum()) / len(g)
        lines.append(
            f"| {band} | {len(g):,} | {g['won'].mean():.1%} | {g['self_p_win'].mean():.1%} | "
            f"{100 * gap:+.1f} ± {196 * se:.1f} pts | {m['self_implied_prob'].mean():.1%} | "
            f"{m['won'].mean():.1%} |"
        )
    return "\n".join(lines)


def _weights(train: pd.DataFrame) -> np.ndarray:
    ref = int(train["year"].max()) + 1
    return outcome.recency_weights(train["year"], ref, outcome.WIN_HALF_LIFE_YEARS)


def _walk_forward(decided: pd.DataFrame, feats: list[str]) -> pd.DataFrame:
    out = []
    for y in sorted(int(v) for v in decided["year"].unique()):
        if y < outcome.FIRST_TEST_YEAR:
            continue
        train = decided[decided["year"] < y]
        test = decided[(decided["year"] == y) & decided["is_a"]]
        if train.empty or test.empty:
            continue
        ws, win = outcome._fit_win(train, feats, _weights(train))
        p = np.clip(win.predict_proba(ws.transform(outcome._matrix(test, feats)))[:, 1], 1e-6, 1)
        yt = test["won"].to_numpy(dtype=float)
        loss = -(yt * np.log(p) + (1 - yt) * np.log(1 - p + 1e-12))
        out.append(
            pd.DataFrame(
                {"bout_id": test["bout_id"], "year": y, "loss": loss, "hit": (p > 0.5) == (yt == 1)}
            )
        )
    return pd.concat(out).set_index("bout_id")


def variants(current: list[str]) -> dict[str, list[str]]:
    return {
        "Current model": current,
        "+ fights in last 12 months": [*current, "diff_fights_12m"],
        "+ fights in last 24 months": [*current, "diff_fights_24m"],
        "+ log fights in last 24 months": [*current, "diff_log_fights_24m"],
        "+ fought anywhere in last 18 months": [*current, "diff_active_18m"],
        "+ UFC debut flag (not activity; for comparison)": [*current, "diff_ufc_debut"],
        "+ fights, wins and losses in last 24 months": [
            *current,
            "diff_fights_24m",
            "diff_wins_24m",
            "diff_losses_24m",
        ],
        "+ fights in last 24 months × years past 33": [*current, "diff_fights_24m_x_old"],
    }


def model_test(s: pd.DataFrame) -> tuple[str, dict[str, list[str]]]:
    done = s[(s["status"] == "completed") & (s["year"] >= outcome.TRAIN_FROM_YEAR)]
    decided = outcome._decided(outcome.add_matchup_diffs(done))
    current = [f for f in outcome.DIFF_FEATURES if f in decided.columns]
    vs = variants(current)
    base = _walk_forward(decided, current)
    lines = [
        "| Model | Log loss 2012+ | Log loss 2020+ | Accuracy 2012+ | Change vs current, 2012+ "
        "(95% interval) |",
        "|---|---|---|---|---|",
    ]
    for name, feats in vs.items():
        r = base if feats is current else _walk_forward(decided, feats)
        r = r.loc[base.index]
        diff = (r["loss"] - base["loss"]).to_numpy()
        lo, hi = _boot(diff)
        change = "—" if feats is current else f"{diff.mean():+.4f} [{lo:+.4f}, {hi:+.4f}]"
        lines.append(
            f"| {name} | {r['loss'].mean():.4f} | {r.loc[r['year'] >= 2020, 'loss'].mean():.4f} "
            f"| {r['hit'].mean():.1%} | {change} |"
        )
    return "\n".join(lines), vs


def upcoming(s: pd.DataFrame, vs: dict[str, list[str]], names: list[str]) -> str:
    done = s[(s["status"] == "completed") & (s["year"] >= outcome.TRAIN_FROM_YEAR)]
    decided = outcome._decided(outcome.add_matchup_diffs(done))
    sched = outcome.add_matchup_diffs(s[s["status"] == "scheduled"])
    pick = sched[sched["fighter_name"].isin(names)]
    if pick.empty:
        return "No scheduled bout for those fighters."
    show = ["fights_12m", "fights_24m", "wins_24m", "losses_24m", "days_since_last"]
    lines = ["| Fighter | " + " | ".join(show) + " |", "|---" * (len(show) + 1) + "|"]
    for _, r in pick.iterrows():
        cells = [f"{r[f'self_{c}']:.0f}" for c in show]
        lines.append(f"| {r['fighter_name']} | " + " | ".join(cells) + " |")
    lines += ["", "| Model | " + " | ".join(pick["fighter_name"]) + " |"]
    lines.append("|---" * (len(pick) + 1) + "|")
    for name, feats in vs.items():
        ws, win = outcome._fit_win(decided, feats, _weights(decided))
        p = win.predict_proba(ws.transform(outcome._matrix(pick, feats)))[:, 1]
        # Average the two orientations, as the live model's symmetrised win chance does.
        p = (p + (1 - p[::-1])) / 2 if len(p) == 2 else p
        lines.append(f"| {name} | " + " | ".join(f"{v:.1%}" for v in p) + " |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(get_settings().db_path))
    ap.add_argument("--fighters", nargs="*", default=["Dan Hooker", "Brian Ortega"])
    args = ap.parse_args()
    sides, pro = _load(args.db)
    s = add_activity(sides, pro)
    print("## Results against the model by recent activity (2012+)\n")
    print(activity_table(s))
    print("\n## Walk-forward win model with activity inputs (recency-weighted, as live)\n")
    table, vs = model_test(s)
    print(table)
    print(f"\n## Upcoming: {' vs '.join(args.fighters)}\n")
    print(upcoming(s, vs, args.fighters))


if __name__ == "__main__":
    main()
