"""Fight cards: upcoming UFC cards and recent card changes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fightdb.app.freshness import reload_changed_modules

reload_changed_modules()

import math

import streamlit as st

from fightdb.app import bet_recs, components, data, value

components.page("Fight cards")
st.title("Upcoming UFC cards")

events = data.upcoming_events()
changes = data.recent_changes(7)

with st.expander(f"Card changes in the last 7 days ({len(changes)})", expanded=False):
    if changes.empty:
        st.caption("No changes detected in the last week.")
    else:
        for row in changes.itertuples():
            st.markdown(
                f"**{row.change_type.replace('_', ' ')}** · {row.summary}  \n<small>{components.fmt_date(row.detected_at, with_time=True)} UTC</small>",
                unsafe_allow_html=True,
            )

with st.container():
    if events.empty:
        st.info("No upcoming events found. The ESPN feed may be empty or the last refresh failed.")
    for ev in events.itertuples():
        with st.expander(
            f"**{ev.name}** · {components.fmt_date(ev.date)} · {ev.bouts} bouts",
            expanded=ev.Index == 0,
        ):
            if ev.location:
                st.caption(ev.location)
            card = data.event_card(ev.event_id)
            if card.empty:
                st.caption("No bouts listed yet.")
                continue
            card = card.reset_index(drop=True)
            priced = value.bouts_with_market(ev.event_id)
            best = (
                priced.set_index("bout_id")[["best_a", "best_b"]].to_dict("index")
                if not priced.empty
                else {}
            )
            nan = {"best_a": math.nan, "best_b": math.nan}
            opportunities = [
                bet_recs.opportunity_label(
                    float(r.self_p_win),
                    float(best.get(r.bout_id, nan)["best_a"]),
                    float(best.get(r.bout_id, nan)["best_b"]),
                    str(r.fighter_name),
                    str(r.opp_name),
                )
                for r in card.itertuples()
            ]
            show = card.assign(
                opportunity=opportunities,
                model=card["self_p_win"].map(lambda p: "—" if p != p else f"{p:.0%} / {1 - p:.0%}"),
                age_gap=card["diff_age_years"].map(lambda d: "—" if d != d else f"{d:+.1f} y"),
                reach_gap=card["diff_reach_cm"].map(components.fmt_diff_cm),
            )
            components.nav_table(
                show[
                    [
                        "fighter_name",
                        "opp_name",
                        "weight_class",
                        "scheduled_rounds",
                        "model",
                        "age_gap",
                        "reach_gap",
                        "opportunity",
                    ]
                ],
                key=f"card_{ev.event_id}",
                fighter_ids={"fighter_name": card["fighter_id"], "opp_name": card["opp_id"]},
                bout_ids=card["bout_id"],
                column_config={
                    "fighter_name": "Fighter (main event first)",
                    "opp_name": "Opponent",
                    "weight_class": "Weight class",
                    "scheduled_rounds": "Rds",
                    "model": st.column_config.TextColumn(
                        "Model win %", help="Calibrated model, fighter / opponent"
                    ),
                    "age_gap": "Age gap",
                    "reach_gap": "Reach gap",
                    "opportunity": st.column_config.TextColumn(
                        "Bet opportunity",
                        help="How far the model's chance is above the best current price "
                        "for the better side, from 1 to 5 stars, with that fighter and the "
                        "expected return per $1 if the model were right. Stars split past bouts "
                        "into fifths (5%, 15%, 27% and 50% cut-offs). In the backtest more stars "
                        "did not mean better results: the bookmakers are more accurate than the "
                        "model, so a big gap is often the model missing something. See the "
                        "Bout page's suggested bets before acting on it.",
                    ),
                },
            )
            st.caption(components.NAV_HINT)

st.caption(
    "Data: fight-canon-db and Greco1899 (from UFC Stats), upcoming cards from ESPN. "
    "Model probabilities are for analysis, not betting advice."
)
