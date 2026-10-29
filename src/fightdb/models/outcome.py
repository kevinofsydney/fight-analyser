"""Win and method-of-victory probabilities, trained walk-forward and checked for calibration.

Symmetry: features are self-minus-opponent differences and the win model has no intercept,
so P(A wins) + P(B wins) = 1. The method model is fit on both sides and each bout's
probabilities are averaged with the mirrored prediction from the other side.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.preprocessing import StandardScaler

DIFF_FEATURES = [
    "diff_elo",
    "diff_age_years",
    "diff_reach_cm",
    "diff_ufc_fights",
    "diff_streak",
    "diff_idx_str_off",
    "diff_idx_str_def",
    "diff_idx_wrest_off",
    "diff_idx_wrest_def",
    "diff_idx_sub_threat",
    "diff_idx_durability",
    "diff_x_sig_r",
    "diff_x_td_r",
    "diff_x_kd_r",
    "diff_x_ctrl_s_r",
]
# Method model also sees each side's own style levels (not just differences).
LEVEL_FEATURES = [
    "self_idx_sub_threat",
    "opp_idx_sub_threat",
    "self_idx_durability",
    "opp_idx_durability",
    "self_kd_p15_s",
    "opp_kd_p15_s",
    "self_x_kd_r",
    "opp_x_kd_r",
    "self_x_ctrl_s_r",
    "opp_x_ctrl_s_r",
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
        xm = self.method_scaler.transform(_matrix(s, DIFF_FEATURES + LEVEL_FEATURES))
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


def fit(train: pd.DataFrame) -> FittedModels:
    train = add_matchup_diffs(train)
    decided = train[train["won"].isin([0, 1]) & ((train["won"] + train["lost"]) == 1)]
    ws = StandardScaler(with_mean=False).fit(_matrix(decided, DIFF_FEATURES))
    win = LogisticRegression(C=0.5, fit_intercept=False, max_iter=2000)
    win.fit(ws.transform(_matrix(decided, DIFF_FEATURES)), decided["won"].astype(int))

    lab = _method_label(decided)
    m = decided[lab.notna()]
    ms = StandardScaler().fit(_matrix(m, DIFF_FEATURES + LEVEL_FEATURES))
    method = LogisticRegression(C=0.5, max_iter=3000)
    method.fit(ms.transform(_matrix(m, DIFF_FEATURES + LEVEL_FEATURES)), lab[lab.notna()])
    return FittedModels(ws, win, ms, method)


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


def walk_forward(sides: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Train on years before Y, predict year Y. Returns (out-of-sample predictions, evals)."""
    done = sides[(sides["status"] == "completed") & (sides["year"] >= TRAIN_FROM_YEAR)]
    years = sorted(y for y in done["year"].unique() if y >= FIRST_TEST_YEAR)
    preds, evals = [], []
    for y in years:
        train, test = done[done["year"] < y], done[done["year"] == y]
        if train.empty or test.empty:
            continue
        model = fit(train)
        p = model.predict(test)
        preds.append(p)
        evals.append(_evaluate(test, p, y))
    pred_df = pd.concat(preds, ignore_index=True) if preds else pd.DataFrame()
    return pred_df, pd.DataFrame(evals)


def _evaluate(test: pd.DataFrame, p: pd.DataFrame, year: int) -> dict[str, object]:
    t = test.merge(p[["bout_id", "fighter_id", "p_win"]], on=["bout_id", "fighter_id"])
    t = t[t["is_a"] & ((t["won"] + t["lost"]) == 1)]  # one row per decisive bout
    y = t["won"].to_numpy(dtype=float)
    pw = np.clip(t["p_win"].to_numpy(dtype=float), 1e-6, 1 - 1e-6)
    mk = t["self_implied_prob"].notna().to_numpy()
    return {
        "model_name": "win_logistic",
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
        "reliability": json.dumps(_reliability(y, pw)),
    }
