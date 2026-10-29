"""Home: upcoming UFC cards and recent card changes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # run from a checkout without install

import streamlit as st

from fightdb.app import components, data

components.page("Upcoming cards")
st.title("Upcoming UFC cards")

events = data.upcoming_events()
changes = data.recent_changes(7)

left, right = st.columns([3, 1])
with right:
    st.subheader("Card changes (7 days)")
    if changes.empty:
        st.caption("No changes detected in the last week.")
    else:
        for row in changes.itertuples():
            st.markdown(
                f"**{row.change_type.replace('_', ' ')}** · {row.summary}  \n<small>{row.detected_at:%a %d %b %H:%M} UTC</small>",
                unsafe_allow_html=True,
            )

with left:
    if events.empty:
        st.info("No upcoming events found. The ESPN feed may be empty or the last refresh failed.")
    for ev in events.itertuples():
        with st.expander(
            f"**{ev.name}** · {ev.date:%a %d %b %Y} · {ev.bouts} bouts", expanded=ev.Index == 0
        ):
            if ev.location:
                st.caption(ev.location)
            card = data.event_card(ev.event_id)
            if card.empty:
                st.caption("No bouts listed yet.")
                continue
            show = card.assign(
                bout=card["fighter_name"] + "  vs  " + card["opp_name"],
                model=card["self_p_win"].map(lambda p: "—" if p != p else f"{p:.0%} / {1 - p:.0%}"),
                age_gap=card["diff_age_years"].map(lambda d: "—" if d != d else f"{d:+.1f} y"),
                reach_gap=card["diff_reach_cm"].map(components.fmt_diff_cm),
                exp=card["self_ufc_fights"].astype(int).astype(str)
                + " / "
                + card["opp_ufc_fights"].astype(int).astype(str),
            )
            st.dataframe(
                show[
                    [
                        "bout",
                        "weight_class",
                        "scheduled_rounds",
                        "model",
                        "age_gap",
                        "reach_gap",
                        "exp",
                    ]
                ],
                hide_index=True,
                width="stretch",
                column_config={
                    "bout": "Bout (main event first)",
                    "weight_class": "Weight class",
                    "scheduled_rounds": "Rds",
                    "model": st.column_config.TextColumn(
                        "Model win %", help="Calibrated model, first fighter / second"
                    ),
                    "age_gap": "Age gap",
                    "reach_gap": "Reach gap",
                    "exp": st.column_config.TextColumn(
                        "UFC fights", help="Prior UFC bouts, first / second fighter"
                    ),
                },
            )
            pick = st.selectbox(
                "Open tale of the tape",
                options=card["bout_id"].tolist(),
                format_func=lambda b, c=card: " vs ".join(
                    c.loc[c["bout_id"] == b, ["fighter_name", "opp_name"]].iloc[0]
                ),
                key=f"pick_{ev.event_id}",
                index=None,
                placeholder="Choose a bout…",
            )
            if pick:
                st.session_state["bout_id"] = pick
                st.switch_page("pages/1_Bout.py")

st.caption(
    "Data: fight-canon-db and Greco1899 (from UFC Stats), upcoming cards from ESPN. "
    "Model probabilities are for analysis, not betting advice."
)
