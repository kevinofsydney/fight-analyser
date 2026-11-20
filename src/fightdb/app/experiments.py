"""The experiment log shown on the Experiments page: every idea tested on the models, in plain
language. Full tables are in docs/NEXT_TASKS.md; add a row here whenever an experiment finishes.

Unless noted, "log loss" is the walk-forward win model over 2012+ bouts (each year scored by a
model trained only on earlier years; lower is better, the closing line scores about 0.606), and
a change counts only when its 95% interval excludes zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

Verdict = Literal["shipped", "no gain", "harmful", "not tested"]


@dataclass(frozen=True)
class Experiment:
    when: date
    title: str
    idea: str
    found: str
    numbers: str
    verdict: Verdict
    lesson: str = ""
    headline: str = ""
    # Change in walk-forward win-model log loss from adding the idea, with its 95% interval
    # (negative is better). Only for experiments measured that way.
    effect: tuple[float, float, float] | None = None


EXPERIMENTS: list[Experiment] = [
    Experiment(
        date(2026, 11, 20),
        "Momentum: the last 1, 2 or 3 results",
        "Fighters on a win streak keep winning; fighters on a skid keep losing.",
        "Real but small: three straight wins → 56% next time, three straight losses → 42%. "
        "But the model and the bookmakers already expected almost exactly those rates, because "
        "fighters on streaks tend to be better (or worse) fighters. Once quality is known, the "
        "streak adds nothing.",
        "Removing the streak input: +0.0005 [−0.0002, +0.0012]. Last result, net of last 3 UFC "
        "or pro fights instead or as well: all within ±0.0007, every interval spanning zero.",
        "no gain",
        "Streaks are mostly a symptom of quality, which the ratings already measure.",
        headline="Already explained by fighter quality.",
        effect=(0.0002, -0.0003, 0.0007),
    ),
    Experiment(
        date(2026, 11, 20),
        "Peak age around 30–33",
        "Fighters peak when experience and physical prime overlap; very young fighters should be "
        "penalised for inexperience as well as older ones for wear.",
        "Not supported in any division. Against a 29–30-year-old, a fighter under 25 wins 58% "
        "and one aged 37+ wins 33%, falling steadily with age, with or without experience held "
        "equal. A 23-year-old in the UFC is already an elite prospect, and inexperience shows up "
        "in the experience and rating inputs instead.",
        "Distance from 31 instead of the age gap: +0.0110 (as bad as dropping age). "
        "Separate under-29 and over-33 penalties on top of the age gap: −0.0000 [−0.0010, "
        "+0.0010]. Dropping age altogether costs +0.0110, so age matters, but in a straight line.",
        "no gain",
        "The existing straight-line age gap is the right shape.",
        headline="Younger fighters win more at every age; there's no peak.",
        effect=(0.0, -0.001, 0.001),
    ),
    Experiment(
        date(2026, 11, 20),
        "Ring rust: time since the last fight",
        "Long layoffs hurt (e.g. Brian Ortega, 455 days out).",
        "Fighters coming back after 18–24 months won 37% against the model's 48% (249 fighters), "
        "but those out 2+ years did fine, and shorter layoffs showed nothing. Looks like injury "
        "layoffs or noise rather than a steady rust effect.",
        "Log days since last fight: +0.0005. Layoff over 1 year or 18 months, or a layoff × age "
        "term: all within ±0.0003, intervals spanning zero.",
        "no gain",
        headline="A dip after 18–24 months out, but nothing the model can use.",
        effect=(0.0003, -0.0011, 0.0018),
    ),
    Experiment(
        date(2026, 11, 20),
        "Bigger edge, better bets?",
        "The further the model's chance is above the bookmakers' price, the better the bet.",
        "Grouping past bouts into fifths by the model's expected return at the best closing "
        "price, actual returns ranged from −1% to +14% with no steady rise. A big gap is about as "
        "likely to mean the model is missing something as that the price is wrong.",
        "5,781 bouts, 2015+.",
        "no gain",
        "The bet-opportunity stars on Fight Cards rank disagreement with the market, not proven "
        "value.",
        headline="Bigger gaps to the price haven't earned more.",
    ),
    Experiment(
        date(2026, 11, 13),
        "Performance rating variants",
        "Tweaks to the rating built from how much of each fight was won.",
        "Starting it from the all-promotion Elo at debut, a faster early K, an average of past "
        "fight shares instead of a rating, and blending the share with the plain result: none "
        "beat the simple version.",
        "0.6363–0.6398 against 0.6361 for the chosen version.",
        "no gain",
        headline="The simple version was best.",
    ),
    Experiment(
        date(2026, 11, 11),
        "Extra win-model inputs",
        "More stats should mean a sharper model.",
        "Log-ratio indices, offence × defence products, UFC win %, log of UFC fights, age² over "
        "30, layoff, height and the pace index all added nothing. Offence-minus-opponent's-"
        "defence gaps are exactly a sum of two inputs the model already has.",
        "All within ±0.002, intervals spanning zero.",
        "no gain",
        headline="Nothing the model didn't already have.",
    ),
    Experiment(
        date(2026, 11, 10),
        "Recency weighting",
        "Recent fights tell you more about today's UFC than fights from 2010.",
        "Weighting training fights with a 4-year half-life did lower log loss, but pushed the "
        "KO/TKO and decision probabilities out of calibration, which would have hidden their "
        "labels.",
        "0.6540 vs 0.6548 at the time; KO and decision gates failed.",
        "harmful",
        "Worth retrying: the method calibration problem that blocked it was fixed on 2026-11-20.",
        headline="Slightly better, but broke calibration. Worth retrying.",
    ),
    Experiment(
        date(2026, 11, 10),
        "Gradient boosting",
        "A tree model can find interactions a logistic regression can't.",
        "Worse on recent years. Its apparent gain on older years came from missing reach: "
        "fighters with no recorded reach lost more before 2020 because short careers never got "
        "measured. That's hindsight, not a usable signal.",
        "0.6490+ on 2020+ vs 0.6437.",
        "harmful",
        "Never add 'is this field missing?' flags for profile data.",
        headline="Worse on recent years; its apparent gain was hindsight.",
    ),
    Experiment(
        date(2026, 11, 10),
        "Shrinkage strength, age hinges, regularisation",
        "Tuning how hard small samples are pulled to the average, age cut-offs at 30/32/34, and "
        "the model's regularisation strength.",
        "No measurable change: the estimated settings were already about right.",
        "Shrinkage ×0.5, ×2, ×4: ±0.0007. Hinges and C 0.05–5: no change.",
        "no gain",
        headline="The settings were already about right.",
    ),
    Experiment(
        date(2026, 11, 11),
        "Fixing the KO/TKO calibration (first attempts)",
        "Feed the method model the win chance, weight class, sex or scheduled rounds.",
        "None closed the gap; filling missing finish rates with the median broke two gates. "
        "The real fix came later (all-promotion finish shares).",
        "Method log loss unchanged; KO gate still failing.",
        "no gain",
        headline="Didn't fix it; a later idea did.",
    ),
    Experiment(
        date(2026, 11, 9),
        "Fight wear (damage absorbed)",
        "Accumulated strikes and knockdowns absorbed, recent wars and KO losses predict decline.",
        "Built and shown on fighter pages and in saved hypotheses, but kept out of the model and "
        "not evaluated for it, by the owner's decision.",
        "—",
        "not tested",
        headline="Kept out of the model by choice.",
    ),
    # Shipped
    Experiment(
        date(2026, 11, 20),
        "How fighters win and lose across every promotion (method model)",
        "The KO/submission/decision model only saw UFC finish rates, so it gave Terrance "
        "McKinney (19-9, never a decision) 28% to win by decision.",
        "Adding the share of pro wins by finish and of pro losses by being finished, across "
        "every promotion, fixed it and made the KO/TKO probabilities pass calibration again.",
        "Method log loss 1.5985 → 1.5860; all three calibration gates pass. McKinney by "
        "decision 28% → 17%.",
        "shipped",
        headline="The method model's biggest gain, and it fixed the KO/TKO calibration.",
    ),
    Experiment(
        date(2026, 11, 13),
        "Rating from share of each fight won",
        "A second Elo that rewards a dominant win more than a split decision.",
        "Small but real gain, using judges' round scores where available.",
        "0.6402 → 0.6367, accuracy 63.6% → 64.0%.",
        "shipped",
        headline="A small, real gain.",
        effect=(-0.0035, -0.0059, -0.0012),
    ),
    Experiment(
        date(2026, 11, 11),
        "All-promotion rating and pro record",
        "A fighter's record before the UFC says a lot, especially for debutants.",
        "The biggest single gain. Bouts with a UFC debutant improved most.",
        "0.6548 → 0.6402, accuracy 61.2% → 63.6%.",
        "shipped",
        headline="The biggest single gain, especially for debutants.",
        effect=(-0.0144, -0.0188, -0.0101),
    ),
    Experiment(
        date(2026, 11, 10),
        "Stronger opponent adjustment",
        "Stats compiled against tough opponents should count for more.",
        "Squaring the strength-of-schedule adjustment helped a little; no adjustment was worst.",
        "0.6556 → 0.6548, accuracy 60.9% → 61.2%.",
        "shipped",
        headline="A small gain.",
    ),
]
