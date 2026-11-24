"""App entry point: the page list and sidebar names. Each page is its own script in pages/."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # run from a checkout without install

import streamlit as st

from fightdb.app import components

PAGES = [
    ("pages/0_Fight_Cards.py", "Fight Cards"),
    ("pages/12_Previous_Cards.py", "Previous Cards"),
    ("pages/1_Bout.py", "Bout"),
    ("pages/2_Bout_Search.py", "Bout Search"),
    ("pages/3_Matchup.py", "Matchup"),
    ("pages/8_Fighter.py", "Fighter"),
    ("pages/4_Model_Performance.py", "Model Performance"),
    ("pages/13_Experiments.py", "Experiments"),
    ("pages/5_Value_Picks.py", "Value Picks"),
    ("pages/11_My_Picks.py", "My Picks"),
    ("pages/6_Hypothesis_Lab.py", "Hypothesis Lab"),
    ("pages/7_Saved_Hypotheses.py", "Saved Hypotheses"),
    ("pages/9_SQL.py", "SQL"),
    ("pages/10_Status.py", "Status"),
    ("pages/14_About.py", "About"),
]

owner = components.is_owner()
# Visitors land on About; the owner lands on Fight Cards as before.
default = "Fight Cards" if owner else "About"
order = PAGES if owner else [PAGES[-1], *PAGES[:-1]]
pages = [
    st.Page(path, title=title, default=title == default)
    for path, title in order
    if owner or title in components.PUBLIC_PAGES
]
if not owner:
    pages.append(st.Page(components.login_page, title="Owner login", url_path="login"))
st.navigation(pages).run()
