"""Peak age by division, and ring rust: do they predict wins beyond what the model knows?

    python scripts/age_layoff_experiment.py [--db path/to/fightdb.duckdb]

Prints Markdown. Part 1 estimates each division's age curve: a logistic regression of the result
on age bands (own band minus opponent's band) with the model's other inputs held fixed, so
experience (pro and UFC fights, ratings) is separated from age. Part 2 shows win rate against
the model's out-of-sample expectation by layoff length. Part 3 adds peak-age and layoff inputs
to the walk-forward win model, with paired bootstrap intervals on the log-loss change.
Every input uses only bouts dated before the one being predicted.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from momentum_experiment import _boot, _walk_forward_losses
from sklearn.linear_model import LogisticRegression

from fightdb.config import get_settings
from fightdb.models import outcome

AGE_BANDS = [0, 25, 27, 29, 31, 33, 35, 37, 100]
BAND_LABELS = ["<25", "25-26", "27-28", "29-30", "31-32", "33-34", "35-36", "37+"]
REFERENCE = "29-30"
DIVISION_GROUPS = {
    "Flyweight & bantamweight": ["flyweight", "bantamweight"],
    "Featherweight & lightweight": ["featherweight", "lightweight"],
    "Welterweight & middleweight": ["welterweight", "middleweight"],
    "Light heavyweight & heavyweight": ["light_heavyweight", "heavyweight"],
    "Women (all)": ["w_strawweight", "w_flyweight", "w_bantamweight", "w_featherweight"],
}
LAYOFF_BANDS = [0, 120, 240, 365, 545, 730, 100_000]
LAYOFF_LABELS = [
    "<4 months",
    "4-8 months",
    "8-12 months",
    "12-18 months",
    "18-24 months",
    "2+ years",
]


def _load(db: str) -> pd.DataFrame:
    import duckdb

    con = duckdb.connect(db, read_only=True)
    try:
        return con.execute("SELECT * FROM bout_sides WHERE status = 'completed'").df()
    finally:
        con.close()


def _band(age: pd.Series) -> pd.Series:
    return pd.cut(age, AGE_BANDS, labels=BAND_LABELS, right=False)


def age_curves(s: pd.DataFrame, *, hold_fixed: bool = True) -> str:
    """Win chance at each age band against an opponent aged 29-30: with the model's other inputs
    (experience, ratings, skills) equal, or with age alone when `hold_fixed` is False."""
    d = outcome._decided(outcome.add_matchup_diffs(s[s["year"] >= outcome.TRAIN_FROM_YEAR]))
    d = d[d["self_age_years"].notna() & d["opp_age_years"].notna()]
    others = (
        [f for f in outcome.DIFF_FEATURES if f != "diff_age_years" and f in d.columns]
        if hold_fixed
        else []
    )
    bands = [b for b in BAND_LABELS if b != REFERENCE]
    own = pd.get_dummies(_band(d["self_age_years"])).reindex(columns=BAND_LABELS, fill_value=0)
    opp = pd.get_dummies(_band(d["opp_age_years"])).reindex(columns=BAND_LABELS, fill_value=0)
    age_x = (own[bands].astype(float) - opp[bands].astype(float)).to_numpy()
    groups = {
        "All divisions": d.index,
        **{name: d.index[d["division"].isin(divs)] for name, divs in DIVISION_GROUPS.items()},
    }
    header = "| Division | Bouts | " + " | ".join(BAND_LABELS) + " | Peak |"
    lines = [header, "|" + "---|" * (len(BAND_LABELS) + 3)]
    for name, idx in groups.items():
        pos = d.index.get_indexer(idx)
        if len(pos) < 400:
            continue
        x = age_x[pos]
        if others:
            x_other = outcome._matrix(d.iloc[pos], others)
            x = np.hstack([x, (x_other - x_other.mean(0)) / (x_other.std(0) + 1e-9)])
        y = d.iloc[pos]["won"].astype(int).to_numpy()
        lr = LogisticRegression(C=1.0, fit_intercept=False, max_iter=3000).fit(x, y)
        coefs = dict(zip(bands, lr.coef_[0][: len(bands)], strict=True)) | {REFERENCE: 0.0}
        n_band = own.iloc[pos].sum()
        cells = [
            f"{1 / (1 + np.exp(-coefs[b])):.0%}" if n_band[b] >= 60 else "—" for b in BAND_LABELS
        ]
        valid = {b: c for b, c in coefs.items() if n_band[b] >= 60}
        peak = max(valid, key=lambda b: valid[b])
        lines.append(f"| {name} | {len(pos) // 2:,} | " + " | ".join(cells) + f" | {peak} |")
    return "\n".join(lines)


def layoff_table(s: pd.DataFrame) -> str:
    d = s[(s["year"] >= outcome.FIRST_TEST_YEAR) & ((s["won"] + s["lost"]) == 1)]
    d = d[d["self_days_since_last"].notna() & d["self_p_win"].notna()]
    d = d.assign(band=pd.cut(d["self_days_since_last"], LAYOFF_BANDS, labels=LAYOFF_LABELS))
    lines = [
        "| Time since last UFC fight | Fighters | Won | Model expected | Gap | "
        "Bookmakers expected | Won, priced bouts |",
        "|---|---|---|---|---|---|---|",
    ]
    for band, g in d.groupby("band", observed=True):
        m = g[g["self_implied_prob"].notna()]
        gap = g["won"].mean() - g["self_p_win"].mean()
        se = np.sqrt((g["self_p_win"] * (1 - g["self_p_win"])).sum()) / len(g)
        lines.append(
            f"| {band} | {len(g):,} | {g['won'].mean():.1%} | {g['self_p_win'].mean():.1%} | "
            f"{100 * gap:+.1f} ± {196 * se:.1f} pts | {m['self_implied_prob'].mean():.1%} | "
            f"{m['won'].mean():.1%} |"
        )
    return "\n".join(lines)


def add_features(s: pd.DataFrame, peak: float = 31.0) -> pd.DataFrame:
    s = s.copy()
    for side in ("self", "opp"):
        age = s[f"{side}_age_years"]
        s[f"{side}_age_from_peak"] = (age - peak).abs()
        s[f"{side}_young"] = np.maximum(0, (peak - 2) - age)
        s[f"{side}_old"] = np.maximum(0, age - (peak + 2))
        days = s[f"{side}_days_since_last"]
        s[f"{side}_log_layoff"] = np.log1p(days.fillna(days.median()))
        s[f"{side}_layoff_1y"] = (days > 365).astype(float)
        s[f"{side}_layoff_18m"] = (days > 545).astype(float)
        s[f"{side}_layoff_x_old"] = s[f"{side}_layoff_1y"] * np.maximum(0, age - 33)
    for f in (
        "age_from_peak",
        "young",
        "old",
        "log_layoff",
        "layoff_1y",
        "layoff_18m",
        "layoff_x_old",
    ):
        s[f"diff_{f}"] = s[f"self_{f}"] - s[f"opp_{f}"]
    return s


def model_test(s: pd.DataFrame) -> str:
    done = add_features(s[s["year"] >= outcome.TRAIN_FROM_YEAR])
    decided = outcome._decided(outcome.add_matchup_diffs(done))
    current = [f for f in outcome.DIFF_FEATURES if f in decided.columns]
    no_age = [f for f in current if f != "diff_age_years"]
    variants = {
        "Current model (linear age gap)": current,
        "No age input at all": no_age,
        "Age replaced by distance from 31": [*no_age, "diff_age_from_peak"],
        "Age replaced by 'under 29' and 'over 33' penalties": [*no_age, "diff_young", "diff_old"],
        "Current + 'under 29' and 'over 33' penalties": [*current, "diff_young", "diff_old"],
        "Current + log days since last fight": [*current, "diff_log_layoff"],
        "Current + layoff over 1 year": [*current, "diff_layoff_1y"],
        "Current + layoff over 18 months": [*current, "diff_layoff_18m"],
        "Current + layoff over 1 year × years past 33": [*current, "diff_layoff_x_old"],
    }
    base = _walk_forward_losses(decided, current)
    lines = [
        "| Model | Log loss 2012+ | Log loss 2020+ | Accuracy 2012+ | Change vs current, 2012+ "
        "(95% interval) |",
        "|---|---|---|---|---|",
    ]
    for name, feats in variants.items():
        r = base if feats is current else _walk_forward_losses(decided, feats)
        recent = r["year"] >= 2020
        diff = (r["loss"] - base["loss"]).to_numpy()
        lo, hi = _boot(diff)
        change = "—" if feats is current else f"{diff.mean():+.4f} [{lo:+.4f}, {hi:+.4f}]"
        lines.append(
            f"| {name} | {r['loss'].mean():.4f} | {r.loc[recent, 'loss'].mean():.4f} | "
            f"{r['hit'].mean():.1%} | {change} |"
        )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(get_settings().db_path))
    args = ap.parse_args()
    s = _load(args.db)
    print("## Age curve by division, age alone (win chance vs an opponent aged 29-30)\n")
    print(age_curves(s, hold_fixed=False))
    print("\n## Age curve by division, experience and skills held equal\n")
    print(age_curves(s))
    print("\n## Ring rust: results against the model by layoff (2012+)\n")
    print(layoff_table(s))
    print("\n## Walk-forward win model with age-curve and layoff inputs\n")
    print(model_test(s))


if __name__ == "__main__":
    main()
