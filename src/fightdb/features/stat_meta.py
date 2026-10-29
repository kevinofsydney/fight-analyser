"""Every comparable per-fighter feature. Drives the tale of the tape and the hypothesis form.

Contract file: see AGENTS.md before changing. Keys are columns of `fighter_snapshots`, and
appear in `bout_sides` as `self_<key>`, `opp_<key>` and `diff_<key>` (= self - opp).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stat:
    key: str
    label: str
    group: str
    unit: str
    higher_is_better: bool | None
    fmt: str
    description: str
    shrinkable: bool = False


STATS: tuple[Stat, ...] = (
    # Physical
    Stat("age_years", "Age", "Physical", "years", None, "{:.1f}", "Age on fight night"),
    Stat("height_cm", "Height", "Physical", "cm", None, "{:.0f}", "Height"),
    Stat("reach_cm", "Reach", "Physical", "cm", True, "{:.0f}", "Arm span"),
    Stat("ape_index_cm", "Ape index", "Physical", "cm", True, "{:+.0f}", "Reach minus height"),
    # Record (UFC only, before this bout)
    Stat(
        "ufc_fights", "UFC fights", "Record", "fights", None, "{:.0f}", "UFC bouts before this one"
    ),
    Stat("ufc_wins", "UFC wins", "Record", "wins", True, "{:.0f}", "UFC wins before this bout"),
    Stat(
        "ufc_losses",
        "UFC losses",
        "Record",
        "losses",
        False,
        "{:.0f}",
        "UFC losses before this bout",
    ),
    Stat(
        "win_pct", "UFC win %", "Record", "%", True, "{:.0%}", "Wins / (wins + losses) in the UFC"
    ),
    Stat(
        "streak",
        "Streak",
        "Record",
        "fights",
        True,
        "{:+.0f}",
        "Current streak: +3 = three wins, -2 = two losses",
    ),
    Stat(
        "finish_rate",
        "Finish rate",
        "Record",
        "%",
        True,
        "{:.0%}",
        "Share of UFC wins by KO/TKO or submission",
    ),
    Stat(
        "ko_losses",
        "KO/TKO losses",
        "Record",
        "losses",
        False,
        "{:.0f}",
        "Times finished by strikes in the UFC",
    ),
    Stat(
        "sub_losses",
        "Submission losses",
        "Record",
        "losses",
        False,
        "{:.0f}",
        "Times submitted in the UFC",
    ),
    # Activity
    Stat(
        "days_since_last",
        "Days since last fight",
        "Activity",
        "days",
        None,
        "{:.0f}",
        "Layoff before this bout",
    ),
    Stat(
        "cage_minutes",
        "UFC cage time",
        "Activity",
        "min",
        None,
        "{:.0f}",
        "Total minutes fought in the UFC",
    ),
    Stat(
        "elo",
        "Elo rating",
        "Activity",
        "pts",
        True,
        "{:.0f}",
        "Results-based strength rating (1500 = debut)",
    ),
    # Striking
    Stat(
        "slpm",
        "Sig. strikes landed / min",
        "Striking",
        "per min",
        True,
        "{:.2f}",
        "Significant strikes landed per minute",
    ),
    Stat(
        "sapm",
        "Sig. strikes absorbed / min",
        "Striking",
        "per min",
        False,
        "{:.2f}",
        "Significant strikes absorbed per minute",
    ),
    Stat(
        "sig_acc",
        "Striking accuracy",
        "Striking",
        "%",
        True,
        "{:.0%}",
        "Significant strikes landed / attempted",
    ),
    Stat(
        "sig_def",
        "Striking defence",
        "Striking",
        "%",
        True,
        "{:.0%}",
        "Share of opponents' significant strikes that missed",
    ),
    Stat(
        "str_diff",
        "Striking differential",
        "Striking",
        "per min",
        True,
        "{:+.2f}",
        "Landed minus absorbed per minute",
    ),
    Stat(
        "kd_p15",
        "Knockdowns / 15 min",
        "Striking",
        "per 15",
        True,
        "{:.2f}",
        "Knockdowns scored per 15 minutes",
    ),
    # Grappling
    Stat(
        "td_att_p15",
        "Takedown attempts / 15 min",
        "Grappling",
        "per 15",
        None,
        "{:.2f}",
        "Takedowns attempted per 15 minutes",
    ),
    Stat(
        "td_p15",
        "Takedowns / 15 min",
        "Grappling",
        "per 15",
        True,
        "{:.2f}",
        "Takedowns landed per 15 minutes",
    ),
    Stat(
        "td_acc",
        "Takedown accuracy",
        "Grappling",
        "%",
        True,
        "{:.0%}",
        "Takedowns landed / attempted",
    ),
    Stat(
        "td_def",
        "Takedown defence",
        "Grappling",
        "%",
        True,
        "{:.0%}",
        "Share of opponents' takedown attempts stopped",
    ),
    Stat(
        "sub_att_p15",
        "Submission attempts / 15 min",
        "Grappling",
        "per 15",
        True,
        "{:.2f}",
        "Submission attempts per 15 minutes",
    ),
    Stat(
        "ctrl_p15",
        "Control time / 15 min",
        "Grappling",
        "min",
        True,
        "{:.1f}",
        "Minutes of control per 15 minutes fought",
    ),
    # Shrunk (small samples pulled towards the division average)
    Stat(
        "slpm_s",
        "Sig. strikes landed / min (adj.)",
        "Adjusted",
        "per min",
        True,
        "{:.2f}",
        "Shrunk towards division average",
        True,
    ),
    Stat(
        "sapm_s",
        "Sig. strikes absorbed / min (adj.)",
        "Adjusted",
        "per min",
        False,
        "{:.2f}",
        "Shrunk towards division average",
        True,
    ),
    Stat(
        "sig_acc_s",
        "Striking accuracy (adj.)",
        "Adjusted",
        "%",
        True,
        "{:.0%}",
        "Shrunk towards division average",
        True,
    ),
    Stat(
        "sig_def_s",
        "Striking defence (adj.)",
        "Adjusted",
        "%",
        True,
        "{:.0%}",
        "Shrunk towards division average",
        True,
    ),
    Stat(
        "td_att_p15_s",
        "Takedown attempts / 15 (adj.)",
        "Adjusted",
        "per 15",
        None,
        "{:.2f}",
        "Shrunk towards division average",
        True,
    ),
    Stat(
        "td_acc_s",
        "Takedown accuracy (adj.)",
        "Adjusted",
        "%",
        True,
        "{:.0%}",
        "Shrunk towards division average",
        True,
    ),
    Stat(
        "td_def_s",
        "Takedown defence (adj.)",
        "Adjusted",
        "%",
        True,
        "{:.0%}",
        "Shrunk towards division average",
        True,
    ),
    Stat(
        "sub_att_p15_s",
        "Submission attempts / 15 (adj.)",
        "Adjusted",
        "per 15",
        True,
        "{:.2f}",
        "Shrunk towards division average",
        True,
    ),
    Stat(
        "kd_p15_s",
        "Knockdowns / 15 (adj.)",
        "Adjusted",
        "per 15",
        True,
        "{:.2f}",
        "Shrunk towards division average",
        True,
    ),
    # Indices: 100 = division average
    Stat(
        "idx_str_off",
        "Striking offence index",
        "Indices",
        "index",
        True,
        "{:.0f}",
        "Adj. strikes landed vs division average (100)",
    ),
    Stat(
        "idx_str_def",
        "Striking defence index",
        "Indices",
        "index",
        True,
        "{:.0f}",
        "Division average absorbed / adj. absorbed (100 = average)",
    ),
    Stat(
        "idx_wrest_off",
        "Wrestling offence index",
        "Indices",
        "index",
        True,
        "{:.0f}",
        "Adj. takedowns landed vs division average",
    ),
    Stat(
        "idx_wrest_def",
        "Wrestling defence index",
        "Indices",
        "index",
        True,
        "{:.0f}",
        "Division TD success / takedowns allowed (100 = average)",
    ),
    Stat(
        "idx_sub_threat",
        "Submission threat index",
        "Indices",
        "index",
        True,
        "{:.0f}",
        "Adj. submission attempts vs division average",
    ),
    Stat(
        "idx_durability",
        "Durability index",
        "Indices",
        "index",
        True,
        "{:.0f}",
        "Knockdowns absorbed per strike absorbed, inverted (100 = average)",
    ),
    Stat(
        "idx_pace",
        "Pace / cardio index",
        "Indices",
        "index",
        True,
        "{:.0f}",
        "Round-3 output relative to round 1, vs division average",
    ),
)

BY_KEY = {s.key: s for s in STATS}
GROUPS = tuple(dict.fromkeys(s.group for s in STATS))

# Per-bout outcome columns available to hypotheses (on `bout_sides`).
OUTCOMES = {
    "won": "Won",
    "won_ko": "Won by KO/TKO",
    "won_sub": "Won by submission",
    "won_dec": "Won by decision",
    "lost_ko": "Lost by KO/TKO",
    "lost_sub": "Lost by submission",
    "went_distance": "Fight went the distance",
    "finished_r1": "Fight finished in round 1",
}


def feature_keys() -> list[str]:
    return [s.key for s in STATS]
