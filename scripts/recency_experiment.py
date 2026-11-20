"""Recency: do recent fights deserve more weight?

    python scripts/recency_experiment.py [--db path/to/fightdb.duckdb]

Two separate questions, printed as Markdown:

1. Training recency. When the model learns from past fights, should a 2025 fight count more
   than a 2012 fight (the sport changes)? Each walk-forward year's models are refitted with
   sample weights halving every H years before the test year, for the win model, the method
   model or both. Reports win and method log loss, paired bootstrap intervals and whether the
   method calibration gates still pass.
2. Fighter recency. Should a fighter's own recent fights count more than old ones (a long
   career like Dan Hooker's)? Adds recency-weighted striking, takedown, knockdown and control
   rates (each earlier fight's weight halving every H years), as gaps between the fighters, to
   the walk-forward win model.

Every input uses only bouts dated before the one being predicted.
"""

from __future__ import annotations

import argparse
import math

import duckdb
import numpy as np
import pandas as pd
from momentum_experiment import _boot, _walk_forward_losses
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from fightdb.config import get_settings
from fightdb.models import outcome

# --- 1. Training recency -------------------------------------------------------------------


def _weights(years: pd.Series, ref_year: int, half_life: float | None) -> np.ndarray:
    if half_life is None:
        return np.ones(len(years))
    return np.power(0.5, (ref_year - years.to_numpy(dtype=float)) / half_life)


def fit_weighted(
    train: pd.DataFrame, ref_year: int, hl_win: float | None, hl_method: float | None
) -> outcome.FittedModels:
    """`outcome.fit` with recency sample weights (scalers stay unweighted)."""
    decided = outcome._decided(outcome.add_matchup_diffs(train))
    xw = outcome._matrix(decided, outcome.DIFF_FEATURES)
    ws = StandardScaler(with_mean=False).fit(xw)
    win = LogisticRegression(C=0.5, fit_intercept=False, max_iter=2000)
    win.fit(
        ws.transform(xw),
        decided["won"].astype(int),
        sample_weight=_weights(decided["year"], ref_year, hl_win),
    )
    lab = outcome._method_label(decided)
    m = decided[lab.notna()]
    feats = outcome.METHOD_DIFF_FEATURES + outcome.LEVEL_FEATURES
    xm = outcome._matrix(m, feats)
    ms = StandardScaler().fit(xm)
    method = LogisticRegression(C=0.5, max_iter=3000)
    method.fit(
        ms.transform(xm), lab[lab.notna()], sample_weight=_weights(m["year"], ref_year, hl_method)
    )
    return outcome.FittedModels(ws, win, ms, method)


def walk_forward(
    sides: pd.DataFrame, hl_win: float | None, hl_method: float | None
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """(evals, per-bout win log loss, per-bout six-way method log loss), as in
    `outcome.walk_forward`."""
    done = sides[(sides["status"] == "completed") & (sides["year"] >= outcome.TRAIN_FROM_YEAR)]
    evals, win_ll, meth_ll = [], [], []
    for y in sorted(int(v) for v in done["year"].unique() if v >= outcome.FIRST_TEST_YEAR):
        train, test = done[done["year"] < y], done[done["year"] == y]
        p = fit_weighted(train, y, hl_win, hl_method).predict(test)
        evals.append(outcome._evaluate(test, p, y))
        me = outcome._evaluate_method(test, p, y)
        if me is not None:
            evals.append(me)
        evals += outcome._method_class_reliability(test, p, y)
        t = test.merge(p[["bout_id", "fighter_id", "p_win"]], on=["bout_id", "fighter_id"])
        t = t[t["is_a"] & ((t["won"] + t["lost"]) == 1)]
        q = np.clip(t["p_win"].to_numpy(float), 1e-6, 1 - 1e-6)
        yy = t["won"].to_numpy(float)
        win_ll.append(
            pd.Series(-(yy * np.log(q) + (1 - yy) * np.log(1 - q)), index=t["bout_id"].to_numpy())
        )
        labels, probs = outcome.method_probabilities(test, p)
        onehot = labels[:, None] == np.array(outcome.METHOD_CLASSES)[None, :]
        probs = np.clip(probs, 1e-6, 1.0)
        probs = probs / probs.sum(axis=1, keepdims=True)
        meth_ll.append(pd.Series(-np.log((probs * onehot).sum(axis=1))))
    return pd.DataFrame(evals), pd.concat(win_ll), pd.concat(meth_ll, ignore_index=True)


def training_recency(sides: pd.DataFrame) -> str:
    variants: dict[str, tuple[float | None, float | None]] = {
        "Current (no weighting)": (None, None)
    }
    for hl in (2.0, 4.0, 8.0):
        variants[f"Win model, half-life {hl:g} years"] = (hl, None)
        variants[f"Method model, half-life {hl:g} years"] = (None, hl)
        variants[f"Both, half-life {hl:g} years"] = (hl, hl)
    lines = [
        "| Weighting | Win log loss | Change (95% interval) | Method log loss | Change "
        "(95% interval) | KO / sub / dec gates |",
        "|---|---|---|---|---|---|",
    ]
    base_w = base_m = None
    for name, (hw, hm) in variants.items():
        ev, wl, ml = walk_forward(sides, hw, hm)
        if base_w is None:
            base_w, base_m = wl, ml
        dw, dm = (wl - base_w).to_numpy(), (ml - base_m).to_numpy()
        gates = " / ".join(
            "pass" if outcome.is_calibrated(ev, outcome.method_model_name(k)) else "fail"
            for k in outcome.METHODS
        )
        cw = (
            "—"
            if hw is None and hm is None
            else "{:+.4f} [{:+.4f}, {:+.4f}]".format(dw.mean(), *_boot(dw))
        )
        cm = (
            "—"
            if hw is None and hm is None
            else "{:+.4f} [{:+.4f}, {:+.4f}]".format(dm.mean(), *_boot(dm))
        )
        lines.append(f"| {name} | {wl.mean():.4f} | {cw} | {ml.mean():.4f} | {cm} | {gates} |")
    return "\n".join(lines)


# --- 2. Fighter recency --------------------------------------------------------------------

STATS = {"slpm": 60.0, "sapm": 60.0, "td15": 900.0, "kd15": 900.0, "ctrl": 1.0}
PRIOR_SECONDS = 900.0  # three rounds of the average fighter


def per_fight(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """One row per fighter per completed bout: date, seconds fought and counts."""
    return con.execute(
        """WITH r AS (
             SELECT bout_id, fighter_id, sum(sig_l) sig_l, sum(td_l) td_l, sum(kd) kd,
                    sum(ctrl_s) ctrl_s
             FROM round_stats GROUP BY ALL)
           SELECT s.bout_id, s.fighter_id, s.event_date AS date, b.fight_seconds AS secs,
                  r.sig_l AS slpm, o.sig_l AS sapm, r.td_l AS td15, r.kd AS kd15,
                  r.ctrl_s AS ctrl
           FROM bout_sides s JOIN bouts b USING (bout_id)
           JOIN r ON r.bout_id = s.bout_id AND r.fighter_id = s.fighter_id
           JOIN r o ON o.bout_id = s.bout_id AND o.fighter_id = s.opp_id
           WHERE s.status = 'completed' AND b.fight_seconds > 0"""
    ).df()


def recency_rates(f: pd.DataFrame, half_life: float | None) -> pd.DataFrame:
    """Per (bout, fighter): rates over the fighter's earlier fights, each weighted
    0.5^(years before this bout / half_life), shrunk towards the overall rate by
    `PRIOR_SECONDS`. `half_life=None` is the plain career rate."""
    f = f.sort_values(["fighter_id", "date"]).reset_index(drop=True)
    total = {k: f[k].sum() / f["secs"].sum() for k in STATS}
    out = []
    for _, g in f.groupby("fighter_id", sort=False):
        days = pd.to_datetime(g["date"]).to_numpy("datetime64[D]").astype(float)
        secs = g["secs"].to_numpy(float)
        counts = {k: g[k].to_numpy(float) for k in STATS}
        for i in range(len(g)):
            prior = days < days[i]
            w = (
                np.where(prior, 1.0, 0.0)
                if half_life is None
                else np.where(prior, np.power(0.5, (days[i] - days) / (365.25 * half_life)), 0.0)
            )
            sw = (w * secs).sum()
            row = {"bout_id": g["bout_id"].iat[i], "fighter_id": g["fighter_id"].iat[i]}
            for k, scale in STATS.items():
                num = (w * np.nan_to_num(counts[k])).sum() + PRIOR_SECONDS * total[k]
                row[k] = scale * num / (sw + PRIOR_SECONDS) if sw > 0 else math.nan
            out.append(row)
    return pd.DataFrame(out)


def add_rates(sides: pd.DataFrame, rates: pd.DataFrame, tag: str) -> pd.DataFrame:
    r = rates.rename(columns={k: f"{tag}_{k}" for k in STATS})
    s = sides.merge(
        r.rename(columns={f"{tag}_{k}": f"self_{tag}_{k}" for k in STATS}),
        on=["bout_id", "fighter_id"],
        how="left",
    )
    s = s.merge(
        r.rename(
            columns={"fighter_id": "opp_id", **{f"{tag}_{k}": f"opp_{tag}_{k}" for k in STATS}}
        ),
        on=["bout_id", "opp_id"],
        how="left",
    )
    for k in STATS:
        s[f"diff_{tag}_{k}"] = s[f"self_{tag}_{k}"] - s[f"opp_{tag}_{k}"]
    return s


def fighter_recency(sides: pd.DataFrame, f: pd.DataFrame) -> tuple[str, pd.DataFrame]:
    s = sides[sides["status"] == "completed"]
    rates = {"car": recency_rates(f, None)}
    for hl in (1.0, 2.0, 4.0):
        rates[f"ew{hl:g}"] = recency_rates(f, hl)
    for tag, r in rates.items():
        s = add_rates(s, r, tag)
    for hl in (1, 2, 4):
        for k in STATS:
            s[f"diff_trend{hl}_{k}"] = s[f"diff_ew{hl}_{k}"] - s[f"diff_car_{k}"]
    done = s[s["year"] >= outcome.TRAIN_FROM_YEAR]
    decided = outcome._decided(outcome.add_matchup_diffs(done))
    current = [x for x in outcome.DIFF_FEATURES if x in decided.columns]
    variants = {"Current model": current}
    for hl in (1, 2, 4):
        variants[f"+ recent rates, half-life {hl} years"] = [
            *current,
            *(f"diff_ew{hl}_{k}" for k in STATS),
        ]
        variants[f"+ recent minus career (trend), half-life {hl} years"] = [
            *current,
            *(f"diff_trend{hl}_{k}" for k in STATS),
        ]
    variants["+ plain career rates (control)"] = [*current, *(f"diff_car_{k}" for k in STATS)]
    base = _walk_forward_losses(decided, current)
    lines = [
        "| Win model | Log loss 2012+ | Log loss 2020+ | Change 2012+ (95% interval) |",
        "|---|---|---|---|",
    ]
    for name, feats in variants.items():
        r = base if feats is current else _walk_forward_losses(decided, feats)
        d = (r["loss"] - base["loss"]).to_numpy()
        ch = "—" if feats is current else "{:+.4f} [{:+.4f}, {:+.4f}]".format(d.mean(), *_boot(d))
        lines.append(
            f"| {name} | {r['loss'].mean():.4f} | {r.loc[r['year'] >= 2020, 'loss'].mean():.4f} "
            f"| {ch} |"
        )
    return "\n".join(lines), rates


def hooker(f: pd.DataFrame, con: duckdb.DuckDBPyConnection) -> str:
    """Dan Hooker today: career vs recency-weighted rates over all his UFC fights."""
    fid = con.execute(
        "SELECT fighter_id FROM fighters WHERE name = 'Dan Hooker' LIMIT 1"
    ).fetchone()
    if fid is None:
        return "Dan Hooker not found."
    g = f[f["fighter_id"] == fid[0]]
    today = pd.Timestamp("2026-11-21")
    probe = pd.DataFrame(
        [
            {
                "bout_id": "now",
                "fighter_id": fid[0],
                "date": today,
                "secs": 0,
                **dict.fromkeys(STATS, 0),
            }
        ]
    )
    both = pd.concat([g, probe], ignore_index=True)
    rows = []
    for name, hl in (
        ("Career", None),
        ("Half-life 4 years", 4.0),
        ("Half-life 2 years", 2.0),
        ("Half-life 1 year", 1.0),
    ):
        r = recency_rates(both, hl)
        r = r[r["bout_id"] == "now"].iloc[0]
        rows.append(
            f"| {name} | {r['slpm']:.2f} | {r['sapm']:.2f} | {r['td15']:.2f} | {r['kd15']:.2f} | "
            f"{r['ctrl']:.0%} |"
        )
    return (
        f"Dan Hooker, {len(g)} UFC fights with round stats:\n\n"
        "| Weighting | Sig. landed / min | Sig. absorbed / min | TD / 15 min | KD / 15 min | "
        "Control share |\n|---|---|---|---|---|---|\n" + "\n".join(rows)
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(get_settings().db_path))
    ap.add_argument("--part", choices=["1", "2", "all"], default="all")
    args = ap.parse_args()
    con = duckdb.connect(args.db, read_only=True)
    try:
        sides = con.execute("SELECT * FROM bout_sides").df()
        f = per_fight(con)
        if args.part in ("1", "all"):
            print("## Training recency (walk-forward, 2012+)\n")
            print(training_recency(sides))
        if args.part in ("2", "all"):
            print("\n## Fighter recency: recency-weighted rates in the win model\n")
            table, _ = fighter_recency(sides, f)
            print(table)
            print("\n" + hooker(f, con))
    finally:
        con.close()


if __name__ == "__main__":
    main()
