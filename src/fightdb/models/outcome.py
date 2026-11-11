"""Win and method-of-victory probabilities, trained walk-forward and checked for calibration.

Symmetry: features are self-minus-opponent differences and the win model has no intercept,
so P(A wins) + P(B wins) = 1. The method model is fit on both sides and each bout's
probabilities are averaged with the mirrored prediction from the other side.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.preprocessing import StandardScaler

from fightdb.models.ratings import SCHEDULE_ADJUSTED

DIFF_FEATURES = [
    "diff_elo",
    # Rating and record over every pro fight, not just the UFC. Most of the gain is in bouts with
    # a debutant, where the UFC-only inputs know nothing (walk-forward log loss on those bouts
    # 0.664 -> 0.643). Unknown history is NaN, which `_matrix` turns into "no gap".
    "diff_pro_elo",
    "diff_pro_fights",
    "diff_pro_win_pct",
    "diff_age_years",
    "diff_reach_cm",
    "diff_ufc_fights",
    "diff_streak",
    "diff_idx_str_off",
    "diff_idx_str_def",
    "diff_idx_wrest_off",
    "diff_idx_wrest_def",
    "diff_idx_sub_threat",
    "diff_idx_sub_def",
    "diff_idx_durability",
    "diff_x_sig_r",
    "diff_x_td_r",
    "diff_x_kd_r",
    "diff_x_ctrl_s_r",
]
# The method model uses the indices *before* the strength-of-schedule adjustment: with the
# adjusted ones it was more under-confident for big favourites winning decisions. The win model
# gains from the adjustment (walk-forward Brier 0.2334 -> 0.2322) and stays calibrated.
# Watch: favourites winning on points are the least certain corner. In 2024-2026 the 0.5-0.6
# "wins by decision" bin predicted ~53% and observed ~64%, but on only 47 fighters, below the
# gate's 100-per-bin minimum. If it fills up and stays off, the gate hides that one label.
METHOD_DIFF_FEATURES = [
    f"{c}_raw" if c.removeprefix("diff_") in SCHEDULE_ADJUSTED else c for c in DIFF_FEATURES
]
# Method model also sees each side's own style levels (not just differences).
LEVEL_FEATURES = [
    "self_idx_sub_threat",
    "opp_idx_sub_threat",
    "self_idx_sub_def",
    "opp_idx_sub_def",
    "self_idx_durability",
    "opp_idx_durability",
    "self_kd_p15_s",
    "opp_kd_p15_s",
    "self_x_kd_r",
    "opp_x_kd_r",
    "self_x_ctrl_s_r",
    "opp_x_ctrl_s_r",
    # How each side's fights usually end. Without these the model under-predicted favourites
    # winning decisions (0.4-0.5 bin: 44% predicted, 51% observed on 2024-2026).
    "self_finish_rate",
    "opp_finish_rate",
    "self_ko_losses",
    "opp_ko_losses",
    "self_sub_losses",
    "opp_sub_losses",
    "self_age_years",
    "opp_age_years",
    "self_distance_rate",
    "opp_distance_rate",
]
METHOD_CLASSES = ["win_ko", "win_sub", "win_dec", "loss_ko", "loss_sub", "loss_dec"]
_MIRROR = {
    "win_ko": "loss_ko",
    "win_sub": "loss_sub",
    "win_dec": "loss_dec",
    "loss_ko": "win_ko",
    "loss_sub": "win_sub",
    "loss_dec": "win_dec",
}
TRAIN_FROM_YEAR = 2005
FIRST_TEST_YEAR = 2012
WIN_MODEL = "win_logistic"
METHOD_MODEL = "method_multinomial"


def add_matchup_diffs(s: pd.DataFrame) -> pd.DataFrame:
    s = s.copy()
    for c in ("x_sig_r", "x_td_r", "x_kd_r", "x_ctrl_s_r"):
        if f"self_{c}" in s.columns:
            s[f"diff_{c}"] = s[f"self_{c}"] - s[f"opp_{c}"]
    return s


def _matrix(s: pd.DataFrame, cols: list[str]) -> np.ndarray:
    x = s.reindex(columns=cols).astype(float)
    return x.fillna(0.0).to_numpy()


def _method_label(s: pd.DataFrame) -> pd.Series:
    mg = s["method_group"].map({"ko_tko": "ko", "sub": "sub", "dec": "dec"})
    side = np.where(s["won"] == 1, "win", np.where(s["lost"] == 1, "loss", ""))
    lab = pd.Series(
        [f"{a}_{b}" if a and isinstance(b, str) else None for a, b in zip(side, mg, strict=True)],
        index=s.index,
    )
    return lab


@dataclass
class FittedModels:
    win_scaler: StandardScaler
    win: LogisticRegression
    method_scaler: StandardScaler
    method: LogisticRegression

    def predict(self, s: pd.DataFrame) -> pd.DataFrame:
        """Per side: p_win, p_win_ko, p_win_sub, p_win_dec, p_distance. Symmetrised by bout."""
        s = add_matchup_diffs(s)
        xw = self.win_scaler.transform(_matrix(s, DIFF_FEATURES))
        p_win_raw = self.win.predict_proba(xw)[:, 1]
        xm = self.method_scaler.transform(_matrix(s, METHOD_DIFF_FEATURES + LEVEL_FEATURES))
        pm = pd.DataFrame(
            self.method.predict_proba(xm), columns=self.method.classes_, index=s.index
        )
        for c in METHOD_CLASSES:
            if c not in pm.columns:
                pm[c] = 0.0
        out = pd.DataFrame(
            {
                "bout_id": s["bout_id"].to_numpy(),
                "fighter_id": s["fighter_id"].to_numpy(),
                "opp_id": s["opp_id"].to_numpy(),
                "p_win_raw": p_win_raw,
            },
            index=s.index,
        )
        out = pd.concat([out, pm[METHOD_CLASSES]], axis=1)
        mirror = out.rename(columns={"fighter_id": "opp_id", "opp_id": "fighter_id"})
        mirror = mirror.rename(
            columns={"p_win_raw": "m_p_win_raw", **{c: f"m_{_MIRROR[c]}" for c in METHOD_CLASSES}}
        )
        both = out.merge(mirror, on=["bout_id", "fighter_id", "opp_id"], how="left")
        res = pd.DataFrame({"bout_id": both["bout_id"], "fighter_id": both["fighter_id"]})
        res["p_win"] = (
            both["p_win_raw"] + (1 - both["m_p_win_raw"].fillna(1 - both["p_win_raw"]))
        ) / 2
        for c in METHOD_CLASSES:
            both[c] = (both[c] + both[f"m_{c}"].fillna(both[c])) / 2
        win_total = both[["win_ko", "win_sub", "win_dec"]].sum(axis=1)
        # Scale method split so it agrees with the (better calibrated) win model.
        for c in ("win_ko", "win_sub", "win_dec"):
            res[f"p_{c}"] = np.where(win_total > 0, both[c] / win_total * res["p_win"], np.nan)
        res["p_distance"] = both["win_dec"] + both["loss_dec"]
        return res


def _decided(s: pd.DataFrame) -> pd.DataFrame:
    return s[s["won"].isin([0, 1]) & ((s["won"] + s["lost"]) == 1)]


def _fit_win(decided: pd.DataFrame, feats: list[str]) -> tuple[StandardScaler, LogisticRegression]:
    ws = StandardScaler(with_mean=False).fit(_matrix(decided, feats))
    win = LogisticRegression(C=0.5, fit_intercept=False, max_iter=2000)
    win.fit(ws.transform(_matrix(decided, feats)), decided["won"].astype(int))
    return ws, win


def fit(train: pd.DataFrame) -> FittedModels:
    train = add_matchup_diffs(train)
    decided = _decided(train)
    ws, win = _fit_win(decided, DIFF_FEATURES)

    lab = _method_label(decided)
    m = decided[lab.notna()]
    ms = StandardScaler().fit(_matrix(m, METHOD_DIFF_FEATURES + LEVEL_FEATURES))
    method = LogisticRegression(C=0.5, max_iter=3000)
    method.fit(
        ms.transform(_matrix(m, METHOD_DIFF_FEATURES + LEVEL_FEATURES)),
        lab[lab.notna()],
    )
    return FittedModels(ws, win, ms, method)


WEIGHT_COLUMNS = ["model_year", "model", "cls", "feature", "coef", "intercept", "mean", "scale"]


def model_weights(model: FittedModels, model_year: int | None) -> pd.DataFrame:
    """Both models as plain numbers (one row per class and feature), so the app can rebuild them
    without unpickling anything. `model_year` is the walk-forward test year, None for the final
    model used on scheduled bouts."""
    rows = []
    for name, scaler, lr, feats in (
        ("win", model.win_scaler, model.win, DIFF_FEATURES),
        ("method", model.method_scaler, model.method, METHOD_DIFF_FEATURES + LEVEL_FEATURES),
    ):
        means = scaler.mean_ if scaler.with_mean else np.zeros(len(feats))
        classes = [str(c) for c in lr.classes_]
        labels = classes if lr.coef_.shape[0] == len(classes) else classes[-1:]
        for i, cls in enumerate(labels):
            for j, feat in enumerate(feats):
                rows.append(
                    (
                        model_year,
                        name,
                        cls,
                        feat,
                        float(lr.coef_[i, j]),
                        float(lr.intercept_[i]) if lr.fit_intercept else 0.0,
                        float(means[j]),
                        float(scaler.scale_[j]),
                    )
                )
    out = pd.DataFrame(rows, columns=WEIGHT_COLUMNS)
    out["model_year"] = out["model_year"].astype("Int64")
    return out


def _rebuild(
    w: pd.DataFrame, feats: list[str], binary: bool
) -> tuple[StandardScaler, LogisticRegression]:
    classes = list(dict.fromkeys(w["cls"]))
    first = w[w["cls"] == classes[0]].set_index("feature").reindex(feats)
    scaler = StandardScaler(with_mean=bool((first["mean"] != 0).any()))
    # Weights saved before an input existed (app deployed ahead of the next refresh): that input
    # gets no weight instead of turning every probability into NaN.
    scaler.mean_ = first["mean"].fillna(0.0).to_numpy(dtype=float)
    scaler.scale_ = first["scale"].fillna(1.0).to_numpy(dtype=float)
    scaler.var_ = scaler.scale_**2
    scaler.n_features_in_ = len(feats)
    scaler.n_samples_seen_ = 1
    lr = LogisticRegression(fit_intercept=bool((w["intercept"] != 0).any()))
    coef = np.vstack(
        [
            w[w["cls"] == c].set_index("feature").reindex(feats)["coef"].fillna(0.0).to_numpy()
            for c in classes
        ]
    )
    lr.coef_ = coef
    lr.intercept_ = np.array(
        [w.loc[w["cls"] == c, "intercept"].iloc[0] for c in classes], dtype=float
    )
    lr.classes_ = np.array([0, 1]) if binary else np.array(classes, dtype=object)
    lr.n_features_in_ = len(feats)
    return scaler, lr


def from_weights(weights: pd.DataFrame) -> FittedModels:
    """Inverse of `model_weights` for one model_year."""
    ws, win = _rebuild(weights[weights["model"] == "win"], DIFF_FEATURES, binary=True)
    ms, method = _rebuild(
        weights[weights["model"] == "method"], METHOD_DIFF_FEATURES + LEVEL_FEATURES, binary=False
    )
    return FittedModels(ws, win, ms, method)


def win_contributions(model: FittedModels, side: pd.DataFrame) -> pd.DataFrame:
    """How each input moved one side's win probability (first row of `side`).

    The win model is a weighted sum of self-minus-opponent gaps with no intercept, so the pushes
    add up exactly to the log-odds behind p_win: p_win = 1 / (1 + exp(-sum(push))). Missing
    gaps count as 0, as in training.
    """
    s = add_matchup_diffs(side.iloc[[0]])
    gaps = s.reindex(columns=DIFF_FEATURES).astype(float).iloc[0]
    scale = model.win_scaler.scale_
    coef = model.win.coef_[0]
    return pd.DataFrame(
        {
            "feature": DIFF_FEATURES,
            "gap": gaps.to_numpy(),
            "typical_gap": scale,
            "weight": coef,
            "push": coef * gaps.fillna(0.0).to_numpy() / scale,
        }
    )


IMPORTANCE_COLUMNS = [
    "feature",
    "log_loss_without",
    "accuracy_without",
    "log_loss_alone",
    "accuracy_alone",
    "log_loss_all",
    "accuracy_all",
    "log_loss_none",
    "n",
]


def _walk_forward_win(decided: pd.DataFrame, feats: list[str]) -> tuple[float, float, int]:
    """Out-of-sample (log loss, accuracy, n) of the win model on `feats`, trained on earlier
    years only and scored once per bout (A side), as in `walk_forward`."""
    losses, hits, n = 0.0, 0, 0
    for y in sorted(int(v) for v in decided["year"].unique()):
        if y < FIRST_TEST_YEAR:
            continue
        train, test = (
            decided[decided["year"] < y],
            decided[(decided["year"] == y) & decided["is_a"]],
        )
        if train.empty or test.empty:
            continue
        if feats:
            ws, win = _fit_win(train, feats)
            p = win.predict_proba(ws.transform(_matrix(test, feats)))[:, 1]
        else:
            p = np.full(len(test), 0.5)
        p = np.clip(p, 1e-6, 1 - 1e-6)
        y_true = test["won"].to_numpy(dtype=float)
        losses += float(-(y_true * np.log(p) + (1 - y_true) * np.log(1 - p)).sum())
        hits += int(((p > 0.5) == (y_true == 1)).sum())
        n += len(test)
    return (losses / n, hits / n, n) if n else (math.nan, math.nan, 0)


def feature_importance(sides: pd.DataFrame) -> pd.DataFrame:
    """How much each win-model input adds, judged out of sample: walk-forward log loss and
    accuracy with every input, without each one, and with each one alone."""
    done = sides[(sides["status"] == "completed") & (sides["year"] >= TRAIN_FROM_YEAR)]
    decided = _decided(add_matchup_diffs(done))
    ll_all, acc_all, n = _walk_forward_win(decided, DIFF_FEATURES)
    ll_none = _walk_forward_win(decided, [])[0]
    rows = []
    for f in DIFF_FEATURES:
        ll_wo, acc_wo, _ = _walk_forward_win(decided, [g for g in DIFF_FEATURES if g != f])
        ll_al, acc_al, _ = _walk_forward_win(decided, [f])
        rows.append((f, ll_wo, acc_wo, ll_al, acc_al, ll_all, acc_all, ll_none, n))
    return pd.DataFrame(rows, columns=IMPORTANCE_COLUMNS)


def _reliability(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict[str, float]]:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    out = []
    for i in range(bins):
        m = idx == i
        if m.sum():
            out.append(
                {
                    "bin": float(edges[i]),
                    "mean_pred": float(p[m].mean()),
                    "mean_obs": float(y[m].mean()),
                    "n": int(m.sum()),
                }
            )
    return out


def walk_forward(
    sides: pd.DataFrame, models: dict[int, FittedModels] | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Train on years before Y, predict year Y. Returns (out-of-sample predictions, evals).

    Pass `models` to also collect each year's fitted model, keyed by test year."""
    done = sides[(sides["status"] == "completed") & (sides["year"] >= TRAIN_FROM_YEAR)]
    years = sorted(y for y in done["year"].unique() if y >= FIRST_TEST_YEAR)
    preds, evals = [], []
    for y in years:
        train, test = done[done["year"] < y], done[done["year"] == y]
        if train.empty or test.empty:
            continue
        model = fit(train)
        if models is not None:
            models[int(y)] = model
        p = model.predict(test)
        preds.append(p)
        evals.append(_evaluate(test, p, y))
        method_eval = _evaluate_method(test, p, y)
        if method_eval is not None:
            evals.append(method_eval)
            evals += _method_class_reliability(test, p, y)
    pred_df = pd.concat(preds, ignore_index=True) if preds else pd.DataFrame()
    return pred_df, pd.DataFrame(evals)


def _evaluate(test: pd.DataFrame, p: pd.DataFrame, year: int) -> dict[str, object]:
    t = test.merge(p[["bout_id", "fighter_id", "p_win"]], on=["bout_id", "fighter_id"])
    t = t[t["is_a"] & ((t["won"] + t["lost"]) == 1)]  # one row per decisive bout
    y = t["won"].to_numpy(dtype=float)
    pw = np.clip(t["p_win"].to_numpy(dtype=float), 1e-6, 1 - 1e-6)
    mk = t["self_implied_prob"].notna().to_numpy()
    return {
        "model_name": WIN_MODEL,
        "test_year": int(year),
        "n": int(len(t)),
        "brier": float(brier_score_loss(y, pw)),
        "log_loss": float(log_loss(y, pw, labels=[0, 1])),
        "accuracy": float(((pw > 0.5) == (y == 1)).mean()),
        "brier_baseline": 0.25,
        "brier_market": float(
            brier_score_loss(y[mk], t["self_implied_prob"].to_numpy(dtype=float)[mk])
        )
        if mk.sum()
        else None,
        "brier_model_on_market_rows": float(brier_score_loss(y[mk], pw[mk])) if mk.sum() else None,
        "n_market": int(mk.sum()),
        # Reliability over both sides of each bout: the model ignores corners on purpose (the
        # A/red corner is usually the favourite), and its probabilities are shown per fighter.
        "reliability": json.dumps(_reliability(np.r_[y, 1 - y], np.r_[pw, 1 - pw])),
    }


def method_probabilities(
    test: pd.DataFrame, p: pd.DataFrame, a_side_only: bool = True
) -> tuple[np.ndarray, np.ndarray]:
    """(labels, probabilities) for the six method classes, one row per decisive bout (A side), or
    one per bout side with `a_side_only=False`.

    A side's "loss_*" probabilities are its opponent's "win_*" probabilities, so rows sum to 1.
    """
    cols = ["p_win_ko", "p_win_sub", "p_win_dec"]
    t = test.merge(p[["bout_id", "fighter_id", *cols]], on=["bout_id", "fighter_id"])
    opp = p[["bout_id", "fighter_id", *cols]].rename(
        columns={"fighter_id": "opp_id", **{c: f"o_{c}" for c in cols}}
    )
    t = t.merge(opp, on=["bout_id", "opp_id"], how="left")
    if a_side_only:
        t = t[t["is_a"]]
    lab = _method_label(t)
    keep = lab.notna().to_numpy()
    probs = t[[*cols, *(f"o_{c}" for c in cols)]].to_numpy(dtype=float)[keep]
    labels = lab[keep].to_numpy()
    ok = ~np.isnan(probs).any(axis=1)
    return labels[ok], probs[ok]


def _evaluate_method(test: pd.DataFrame, p: pd.DataFrame, year: int) -> dict[str, object] | None:
    labels, probs = method_probabilities(test, p)
    if len(labels) == 0:
        return None
    onehot = (labels[:, None] == np.array(METHOD_CLASSES)[None, :]).astype(float)
    probs = np.clip(probs, 1e-6, 1.0)
    probs = probs / probs.sum(axis=1, keepdims=True)
    return {
        "model_name": METHOD_MODEL,
        "test_year": int(year),
        "n": int(len(labels)),
        "brier": float(((probs - onehot) ** 2).sum(axis=1).mean()),
        "log_loss": float(-np.log((probs * onehot).sum(axis=1)).mean()),
        "accuracy": float((probs.argmax(axis=1) == onehot.argmax(axis=1)).mean()),
        "brier_baseline": float(((onehot.mean(axis=0) - onehot) ** 2).sum(axis=1).mean()),
        "reliability": json.dumps(_method_reliability(test, p)),
    }


METHODS = ("ko", "sub", "dec")


def method_model_name(method: str) -> str:
    """`model_evals` row holding the reliability of one method's "wins by X" probability."""
    return f"{METHOD_MODEL}_{method}"


def _win_by_method(test: pd.DataFrame, p: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Each fighter's "wins by KO / sub / decision" probabilities and outcomes, both sides of
    every decisive bout, each number counted once. These are exactly what the Bout page shows.
    (The six-class "loss_*" columns are the opponent's "win_*" numbers again, so pooling all six
    would count every probability twice and double the apparent sample size.)"""
    labels, probs = method_probabilities(test, p, a_side_only=False)
    wins = np.clip(probs[:, :3], 0.0, 1.0)
    hits = (labels[:, None] == np.array([f"win_{m}" for m in METHODS])[None, :]).astype(float)
    return hits, wins


def _method_reliability(test: pd.DataFrame, p: pd.DataFrame) -> list[dict[str, float]]:
    hits, wins = _win_by_method(test, p)
    return _reliability(hits.ravel(), wins.ravel())


def _method_class_reliability(
    test: pd.DataFrame, p: pd.DataFrame, year: int
) -> list[dict[str, object]]:
    hits, wins = _win_by_method(test, p)
    return [
        {
            "model_name": method_model_name(m),
            "test_year": int(year),
            "n": int(len(hits)),
            "reliability": json.dumps(_reliability(hits[:, k], wins[:, k])),
        }
        for k, m in enumerate(METHODS)
        if len(hits)
    ]


def is_calibrated(
    evals: pd.DataFrame,
    model_name: str,
    recent_years: int = 3,
    tol: float = 0.05,
    min_n: int = 100,
) -> bool:
    """True when every well-populated reliability bin of the recent test years is within `tol`.

    Bins are pooled across the years so that each has enough bouts to judge.
    """
    ev = evals[evals["model_name"] == model_name]
    if ev.empty:
        return False
    ev = ev.sort_values("test_year").tail(recent_years)
    bins = pd.concat(
        [pd.DataFrame(json.loads(r)) for r in ev["reliability"] if r], ignore_index=True
    )
    if bins.empty:
        return False
    pooled = (
        bins.assign(sp=bins.mean_pred * bins.n, so=bins.mean_obs * bins.n)
        .groupby("bin")[["sp", "so", "n"]]
        .sum()
    )
    pooled = pooled[pooled["n"] >= min_n]
    if pooled.empty:
        return False
    gap = (pooled["sp"] - pooled["so"]).abs() / pooled["n"]
    return bool((gap <= tol).all())
