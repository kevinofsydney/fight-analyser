"""App entry point: the page list and sidebar names. Each page is its own script in pages/."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # run from a checkout without install

import streamlit as st

PAGES = [
    ("pages/0_Fight_Cards.py", "Fight Cards", "🥊"),
    ("pages/12_Previous_Cards.py", "Previous Cards", "🗓️"),
    ("pages/1_Bout.py", "Bout", "🥊"),
    ("pages/2_Bout_Search.py", "Bout Search", "🔎"),
    ("pages/3_Matchup.py", "Matchup", "⚖️"),
    ("pages/8_Fighter.py", "Fighter", "👤"),
    ("pages/4_Model_Performance.py", "Model Performance", "📈"),
    ("pages/5_Value_Picks.py", "Value Picks", "🎯"),
    ("pages/11_My_Picks.py", "My Picks", "✍️"),
    ("pages/6_Hypothesis_Lab.py", "Hypothesis Lab", "🧪"),
    ("pages/7_Saved_Hypotheses.py", "Saved Hypotheses", "📚"),
    ("pages/9_SQL.py", "SQL", "🧮"),
    ("pages/10_Status.py", "Status", "🩺"),
]

st.navigation(
    [
        st.Page(path, title=title, icon=icon, default=i == 0)
        for i, (path, title, icon) in enumerate(PAGES)
    ]
).run()
