"""About: what the project is and how it's built. The landing page for visitors."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fightdb.app.freshness import reload_changed_modules

reload_changed_modules()

import numpy as np
import streamlit as st

from fightdb.app import components, data

REPO = "https://github.com/kevinofsydney/fight-analyser"
SCORED_FROM = 2012

components.page("About")
st.title("Fight Analyser")
st.markdown(
    "A UFC prediction and analysis app. It keeps every UFC fight since 1993 in a database, "
    "refreshes it daily and uses it to predict upcoming fights: who wins, how, and why the "
    "model thinks so. Every model result shown here is out of sample."
)

counts = data.query(
    """SELECT (SELECT count(*) FROM bouts WHERE status = 'completed') AS fights,
              (SELECT count(*) FROM fighters) AS fighters"""
).iloc[0]
scored = data.query(
    """SELECT self_p_win AS p, won, self_implied_prob AS market FROM bout_sides
       WHERE status = 'completed' AND is_a AND self_p_win IS NOT NULL AND won + lost = 1
         AND year >= ?""",
    [SCORED_FROM],
)
c = st.columns(4)
c[0].metric("UFC fights", f"{int(counts['fights']):,}")
c[1].metric("Fighters", f"{int(counts['fighters']):,}")
if not scored.empty:
    won = scored["won"].to_numpy(dtype=float) == 1
    c[2].metric(
        "Model accuracy",
        f"{np.mean((scored['p'].to_numpy(dtype=float) > 0.5) == won):.1%}",
        help=f"Winners picked in {len(scored):,} fights since {SCORED_FROM}, each scored by a "
        "model trained only on earlier years.",
    )
    priced = scored["market"].notna().to_numpy()
    if priced.any():
        market = scored.loc[priced, "market"].to_numpy(dtype=float)
        c[3].metric(
            "Bookmakers' accuracy",
            f"{np.mean((market > 0.5) == won[priced]):.1%}",
            help="How often the closing-line favourite won, on fights with closing odds. The "
            "market is the benchmark to beat.",
        )

st.subheader("Where to look")
for path, label, what in (
    ("pages/0_Fight_Cards.py", "Fight Cards", "upcoming cards with the model's win chances."),
    (
        "pages/1_Bout.py",
        "Bout",
        "one fight in depth: the prediction, how the model got there, expected fight stats.",
    ),
    (
        "pages/4_Model_Performance.py",
        "Model Performance",
        "the long-run record, calibration and which inputs matter.",
    ),
    (
        "pages/13_Experiments.py",
        "Experiments",
        "every idea tested on the model, including the ones that didn't help.",
    ),
    ("pages/8_Fighter.py", "Fighter", "any fighter's record, history and trends."),
):
    left, right = st.columns([1, 3])
    components.page_link(left, path, label)
    right.markdown(what)

st.subheader("How it works")
st.markdown(
    """
- **Data pipeline.** A daily GitHub Actions job imports full fight history from public
  datasets and upcoming cards from ESPN, validates each source with Pydantic models, matches
  fighter identities across sources, and publishes a DuckDB database the app downloads.
- **Point-in-time features.** Every number for a past fight uses only fights dated before it,
  so backtests can't see the future.
- **Models.** A logistic regression predicts the winner from about 20 differences between the
  fighters: Elo ratings across every promotion, age, reach, experience and style indices
  adjusted for opponent strength. A second model splits the win between KO/TKO, submission
  and decision. Both are calibrated and trained on earlier years only.
- **Experiments.** A new idea ships only if it improves out-of-sample accuracy by more than
  chance. Most don't, and the Experiments page records why.
- **Engineering.** Python 3.14, DuckDB, pandas, scikit-learn and Streamlit. Typed code,
  linted and tested in CI against recorded fixtures, with no network calls in tests.
"""
)
st.link_button("Source code on GitHub", REPO)
components.footer()
