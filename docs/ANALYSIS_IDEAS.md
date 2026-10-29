# Analysis Ideas: Metrics, Matchup Models and Questions

UFC versions of xG (soccer) and offensive/defensive ratings (basketball). The aim is to turn "Fighter A shoots 2 takedowns every 5 minutes, Fighter B's takedown defence is 50%, A is a submission specialist" into **numbers with honest uncertainty**: expected takedowns per round, and a submission probability that's "high" *relative to the division baseline*.

League baselines below were computed from the Greco1899 dataset, UFC fights 2015 → 2026-10 (5,914 bouts):

| Baseline | Value |
|----------|-------|
| Takedown accuracy (so average TDD ≈ 64%) | **36.3%** |
| Takedown attempts per fighter per 15 min | **3.86** |
| Significant-strike accuracy | **45.9%** |
| Significant strikes landed per fighter per minute | **3.83** |
| Method of victory | KO/TKO **31.5%**, Submission **17.6%**, Decision **49.1%**, other 1.9% |

(These get recomputed per division and era by the app. Heavyweight finishes far more often than flyweight.)

---

## 1. Core building blocks

### 1.1 Opponent-adjusted matchup rates (the "log5" / odds-ratio method)
Combines one fighter's offence with the other's defence, relative to league average. Bill James used the same idea for batter-vs-pitcher matchups.

```
odds(p) = p / (1 - p)
odds(matchup) = odds(A's success rate) × odds(B's allowed rate) / odds(league rate)
p(matchup) = odds(matchup) / (1 + odds(matchup))
```

**Worked example (your takedown scenario):**
- A attempts **2 takedowns per 5 min** (= 6.0 per 15; league average 3.86), landing **45%** of them.
- B's takedown defence is **50%**, so B allows 50% of attempts. That's poor: the league average allows 36.3%.
- odds(A) = 0.818, odds(B allows) = 1.000, odds(league) = 0.570 → odds = 1.435 → **p = 58.9%** per attempt.
- **Expected takedowns: 6.0 × 0.589 ≈ 3.5 per 15 min ≈ 1.2 per round.** The league average is about 1.4 per fighter per 15 min.

The same formula works for strikes landed, knockdowns per strike, submission attempts and control time.

### 1.2 Shrinkage (don't trust small samples)
A fighter who has defended 4 of 4 takedowns doesn't have "100% TDD". Pull each rate towards the division average in proportion to how little data there is (empirical Bayes):

```
shrunk_rate = (successes + k × division_rate) / (attempts + k)
```

`k` (the "prior strength", e.g. about 15 takedown attempts) is estimated from the data per stat, not guessed. The UI shows raw and shrunk values side by side. This is the single biggest accuracy improvement over the naive stats on UFCStats.

### 1.3 Strength of schedule
Landing 60% of takedowns against strikers isn't the same as against wrestlers. Adjust offence and defence for the quality of opponents faced: either a ridge regression with fighter offence and defence coefficients (like adjusted plus-minus in the NBA), or simpler, compare each performance to what *that opponent* usually allows.

### 1.4 Point-in-time and calibration
Every metric is computed **from fights before the bout in question** (PRD FR-12). Every probability model is checked for **calibration** (when we say 30%, does it happen about 30% of the time?) using Brier score and log loss, and compared against (a) a naive baseline and (b) the **closing betting line**, which is free in fight-canon-db. The closing line is the toughest benchmark there is. If a metric adds nothing beyond it, we say so.

---

## 2. Fighter ratings (the basketball-style indices)

All are opponent-adjusted, shrunk and point-in-time, scaled so **100 = division average** (like OPS+ / ERA+).

| Index | Built from | Reads as |
|-------|------------|----------|
| **Striking Offence (SOI)** | Sig strikes landed/min, accuracy, knockdowns per landed strike, head-strike share | "Lands 30% more effective damage than an average LW" |
| **Striking Defence (SDI)** | Sig strikes absorbed/min, strike defence %, knockdowns absorbed per strike absorbed | |
| **Wrestling Offence (WOI)** | TD attempts/15, TD accuracy (matchup-adjusted), control time per TD | |
| **Wrestling Defence (WDI)** | TDD %, get-up speed (control time absorbed per TD conceded) | |
| **Submission Threat (SubT)** | Sub attempts per minute on top, sub finishes per sub attempt | |
| **Submission Defence (SubD)** | Sub attempts survived per sub attempt faced, sub losses | |
| **Durability** | KO/TKO losses per sig strike absorbed, knockdowns per head strike absorbed, adjusted for age | The chin index |
| **Pace / Cardio** | Output in round 3 (and 4–5) versus round 1; late-round differential | |
| **Elo / Glicko-2** | Results only, with margin (finish > decision) and uncertainty | Overall strength, the "power rating" |

The tale of the tape (PRD §4.5) shows these as paired bars, plus the **matchup** versions: A's SOI vs B's SDI, A's WOI vs B's WDI, and so on.

---

## 3. Matchup projections (the "xG" layer)

For every upcoming bout, per round and per fight:

| Projection | Method | Example output |
|------------|--------|----------------|
| **xTD:** expected takedowns | Attempt rate × matchup accuracy (§1.1) | "A: 1.2 TD/round · B: 0.2" |
| **xCTRL:** expected control time | xTD × A's control per TD, adjusted by B's get-up rate | "A projected to control ~2:10 per round" |
| **xSS:** expected sig strikes | Both fighters' attempt rates (pace interaction) × matchup accuracy | "A 48, B 61 over 3 rounds" |
| **xKD:** expected knockdowns | xSS to the head × A's KD rate × B's durability | |
| **Grappling share** | xCTRL / total fight time | "Expect ~45% of the fight on the mat" |
| **Win probability** | Logistic regression or gradient boosting on point-in-time rating differentials, calibrated | "A 61% (model) vs 58% (market)" |
| **Method of victory** | Multinomial model: P(A by KO), P(A by Sub), P(A by Dec), P(B by …) | "Sub chance: 24% (division baseline 18%), **medium-high**" |
| **Goes the distance** | From the method model | "P(decision) = 56%" |

**"Low / medium / high" labels** come from the probability *relative to the division baseline*, never from hand-written rules. For example: high means at least 1.5× baseline, low means at most 0.67×. So "A is a strong submission specialist" turns into: A's SubT is 160, B's SubD is 85, xCTRL is high, P(A by sub) = 24% against an 18% baseline, which is medium-high.

---

## 4. Question catalogue

Each of these can be a saved hypothesis (YAML) or a sweep chart. ★ marks ones that match your examples.

### Physical
1. ★ When one fighter is X+ years older, how often do they win, by division? (Sweep X = 0–15.)
2. ★ Does reach advantage matter? Win rate by reach-difference bucket, by division. Does it matter more for strikers than for wrestlers?
3. Height advantage vs reach advantage: which predicts better once you control for the other? (Ape index = reach − height.)
4. Southpaw vs orthodox: does the southpaw edge exist in the UFC, and is it shrinking over time?
5. At what age does each division's win rate fall off a cliff? (Age curve by division.)

### Style matchups
6. Wrestler vs striker (classified by WOI and SOI): who wins, by division and era?
7. ★ When the attacker's TD accuracy is above X and the defender's TDD is below Y, how often does the attacker win? Does that hold at 5 rounds?
8. ★ Submission specialist vs weak submission defence: how much does P(sub) rise over baseline?
9. Does volume (high pace) beat power (high KD rate)? Does that flip in 5-round fights?
10. Do counter-strikers (high defence, low output) lose close decisions disproportionately?

### Durability and wear
11. After a KO loss, what's a fighter's chance of being KO'd in the next fight, versus their earlier rate?
12. Career sig strikes absorbed vs future KO-loss rate (accumulated damage).
13. Do fighters 35+ coming off a KO loss win less than 35% of the time?

### Activity and context
14. Ring rust: win rate by days since last fight (sweep 90–720).
15. ★ Short-notice replacements: win rate when a fighter joined the card less than 14 days out. (Our card-change log detects this; few public datasets have it.)
16. Moving up or down a division: first fight in the new division.
17. Home-country advantage: win rate and decision rate when fighting at home.
18. Altitude (Mexico City, Salt Lake City, Denver): does the better-cardio fighter win more often?
19. Title fights and 5-round main events: do the usual edges hold or shrink?
20. Fighters coming off a long win streak vs a long loss streak (momentum, or just quality?).

### Fight dynamics
21. If a fighter wins round 1 on strikes, how often do they win the fight?
22. Which fighters fade? Round-3 output vs round-1 output, and does that predict late finishes?
23. Does taking the opponent down early reduce their later striking output?
24. How often does the fighter with more control time lose the decision?

### Market (analysis only, not betting advice)
25. Are betting favourites over- or under-priced by division, era or fight type (calibration curve of implied odds)?
26. Which of our metrics add information beyond the closing line? (If none, that's a finding too.)
27. How often does an underdog with a big physical edge (reach, age) win, compared with the implied odds?

### Judging
28. Which judges favour control time over striking? (Uses fight-canon-db's `judge_scores`, 2020–2024.)
29. Do home-country fighters win split decisions more often?

---

## 5. Guardrails (built into the app)

- **Point-in-time only.** No feature uses information from on or after the bout date.
- **Shrink small samples.** Show "based on N UFC fights", and grey out metrics with N < 3.
- **Multiple testing.** Count hypotheses tested per session. Sweep charts beat cherry-picked thresholds. Long-term, report false-discovery-rate-adjusted results in an "insight finder".
- **Confounding.** Older fighters are often more experienced *and* past their prime. Offer a "control for" option (logistic regression with covariates such as Elo difference) on any hypothesis.
- **Calibration before confidence.** A projection doesn't get a "high/medium/low" label in the UI until its model has a reliability chart on held-out years.
