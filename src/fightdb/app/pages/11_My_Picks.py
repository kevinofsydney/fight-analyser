"""My picks: enter your own pre-fight picks and see how they score against the model."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fightdb.app.freshness import reload_changed_modules

reload_changed_modules()

from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from fightdb import picks
from fightdb.app import components, data, value
from fightdb.config import get_settings
from fightdb.sources.identity import is_placeholder

components.page("My picks", "✍️")
st.title("My picks")
st.caption(
    "Pick a winner and say how sure you are before each card. Once the results are in, your "
    "picks are scored against the model and the bookmakers on the same fights. Picks lock when "
    "the event date has passed."
)

settings = get_settings()
path = settings.picks_path
today = datetime.now(UTC).date()
NO_PICK = "No pick"
DEFAULT_PCT = 60

if settings.data_release_url:
    st.warning(
        "This copy of the app runs on a host that may not keep files between restarts. "
        "Download your picks below after saving them."
    )
if message := st.session_state.pop("picks_saved", None):
    st.success(message)

saved = picks.load(path)

st.subheader("Upcoming cards")
events = data.upcoming_events()
events = events[events["bouts"] > 0].reset_index(drop=True)
if events.empty:
    st.info("No upcoming bouts are listed yet.")
else:
    labels = {e.event_id: f"{e.name} · {components.fmt_date(e.date)}" for e in events.itertuples()}
    event_id = st.selectbox("Event", list(labels), format_func=labels.get)
    card = data.query(
        """SELECT bout_id, event_name, event_date, fighter_id, fighter_name, opp_id, opp_name,
                  weight_class, self_p_win
           FROM bout_sides WHERE event_id = ? AND is_a AND status = 'scheduled'
           ORDER BY card_order""",
        [event_id],
    )
    card = card[
        ~card["fighter_name"].map(is_placeholder) & ~card["opp_name"].map(is_placeholder)
    ].reset_index(drop=True)
    market = value.bouts_with_market(event_id)
    market_p = dict(zip(market["bout_id"], market["market_p"], strict=True)) if len(market) else {}
    existing = saved.set_index("bout_id")

    show_numbers = st.toggle(
        "Show the model's and bookmakers' numbers while I pick",
        value=False,
        help="Off by default so your picks are your own view, not an adjustment of the model's.",
    )
    with st.form(f"picks_{event_id}"):
        entered: list[tuple[pd.Series, str, int]] = []
        for _, bout in card.iterrows():
            a, b = str(bout["fighter_name"]), str(bout["opp_name"])
            had = existing.loc[bout["bout_id"]] if bout["bout_id"] in existing.index else None
            index, pct = 0, DEFAULT_PCT
            if had is not None:
                index = 1 if had["pick_id"] == bout["fighter_id"] else 2
                pct = round(float(had["p_pick"]) * 100)
            left, mid, right = st.columns([4, 3, 3])
            choice = left.radio(
                f"{a} vs {b}" + (f" · {bout['weight_class']}" if bout["weight_class"] else ""),
                [NO_PICK, a, b],
                index=index,
                horizontal=True,
                key=f"pick_{bout['bout_id']}",
            )
            sure = mid.slider(
                "Chance your pick wins (%)",
                min_value=50,
                max_value=99,
                value=pct,
                key=f"pct_{bout['bout_id']}",
                help="50 is a coin flip. Being 90% sure and wrong costs far more than 60% and wrong.",
            )
            if show_numbers:
                p = bout["self_p_win"]
                lines = []
                if pd.notna(p):
                    lines.append(f"Model: {a if p >= 0.5 else b} {max(p, 1 - p):.0%}")
                m = market_p.get(bout["bout_id"])
                if m is not None and pd.notna(m):
                    lines.append(f"Bookmakers: {a if m >= 0.5 else b} {max(m, 1 - m):.0%}")
                right.caption("  \n".join(lines) or "No model or bookmaker number yet.")
            entered.append((bout, choice, sure))
        submitted = st.form_submit_button("Save picks", type="primary")

    if submitted:
        n_saved = n_removed = 0
        try:
            for bout, choice, sure in entered:
                if choice == NO_PICK:
                    if bout["bout_id"] in existing.index:
                        picks.delete(path, bout["bout_id"], today)
                        n_removed += 1
                    continue
                picked_a = choice == bout["fighter_name"]
                p, m = bout["self_p_win"], market_p.get(bout["bout_id"])
                picks.save(
                    path,
                    picks.Pick(
                        bout_id=bout["bout_id"],
                        event_name=bout["event_name"],
                        event_date=pd.Timestamp(bout["event_date"]).date(),
                        fighter_a_id=bout["fighter_id"],
                        fighter_a_name=bout["fighter_name"],
                        fighter_b_id=bout["opp_id"],
                        fighter_b_name=bout["opp_name"],
                        pick_id=bout["fighter_id"] if picked_a else bout["opp_id"],
                        p_pick=sure / 100,
                        model_p_pick=None if pd.isna(p) else float(p if picked_a else 1 - p),
                        market_p_pick=(
                            None if m is None or pd.isna(m) else float(m if picked_a else 1 - m)
                        ),
                    ),
                    today,
                )
                n_saved += 1
        except picks.PickLockedError:
            st.error("This event's date has passed, so its picks are locked.")
        else:
            done = [
                f"{verb} {n} pick{'' if n == 1 else 's'}"
                for verb, n in (("Saved", n_saved), ("Removed", n_removed))
                if n
            ]
            st.session_state["picks_saved"] = (", ".join(done) or "Nothing to save") + "."
            st.rerun()

st.subheader("Scoreboard")
if saved.empty:
    st.info("No picks saved yet. Results appear here after each card you picked.")
    st.stop()

completed = data.query(
    """SELECT bout_id, event_date, fighter_id, fighter_name, opp_id, opp_name, won, lost, draw,
              no_contest, method, self_implied_prob
       FROM bout_sides WHERE status = 'completed' AND is_a AND event_date >= ?""",
    [(saved["event_date"].min() - pd.Timedelta(days=picks.MATCH_DAYS)).date()],
)
scored = picks.score(saved, completed)
summary = picks.summarise(scored)

if summary.empty:
    st.caption("None of your picks has a result yet.")
else:
    model = summary[summary["who"] == "Model"]
    you = summary[summary["who"] == "You"]
    if not model.empty:
        same = you[you["on"] == "same picks as model"]
        mine, theirs = (same if not same.empty else you).iloc[0], model.iloc[0]
        n = int(theirs["n"])
        gap = mine["log_loss"] - theirs["log_loss"]
        verdict = (
            f"You are **ahead of** the model over {n} picks"
            if gap < 0
            else f"The model is **ahead of** you over {n} picks"
            if gap > 0
            else f"You and the model are level over {n} picks"
        )
        st.markdown(
            f"{verdict}: you picked {mine['accuracy']:.0%} of winners (model "
            f"{theirs['accuracy']:.0%}), with a log loss of {mine['log_loss']:.3f} against "
            f"{theirs['log_loss']:.3f}."
        )
        if n < 100:
            st.caption(
                "With fewer than about 100 picks, a gap this size is mostly luck. "
                "Keep going before drawing a conclusion."
            )
    st.dataframe(
        summary.rename(
            columns={
                "who": "Who",
                "on": "Scored on",
                "n": "Picks",
                "accuracy": "Picked the winner",
                "log_loss": "Log loss",
                "brier": "Brier score",
            }
        ),
        hide_index=True,
        width="stretch",
        column_config={
            "Picks": st.column_config.NumberColumn(format="%d"),
            "Picked the winner": st.column_config.NumberColumn(format="percent"),
            "Log loss": st.column_config.NumberColumn(
                format="%.3f",
                help="Rewards being confident and right, punishes being confident and wrong. "
                "Lower is better; always saying 50% scores 0.693.",
            ),
            "Brier score": st.column_config.NumberColumn(
                format="%.3f", help="Lower is better; always saying 50% scores 0.250."
            ),
        },
    )
    st.caption(
        "The model's number is what it showed when you saved the pick. The bookmakers' number "
        "is the closing line where known, otherwise the price when you saved the pick."
    )

OUTCOMES = {
    "won": "✓ Won",
    "lost": "✗ Lost",
    "draw": "Draw",
    "nc": "No contest",
    "pending": "Awaiting result",
}
st.dataframe(
    pd.DataFrame(
        {
            "Date": scored["event_date"].map(components.fmt_date),
            "Event": scored["event_name"],
            "Bout": scored["fighter_a_name"] + " vs " + scored["fighter_b_name"],
            "My pick": scored["pick_name"],
            "My %": scored["p_pick"],
            "Model % for my pick": scored["model_p"],
            "Bookmakers % for my pick": scored["market_p"],
            "Result": scored["outcome"].map(OUTCOMES),
            "Method": scored["method"].fillna("—"),
        }
    ),
    hide_index=True,
    width="stretch",
    column_config={
        c: st.column_config.NumberColumn(format="percent")
        for c in ("My %", "Model % for my pick", "Bookmakers % for my pick")
    },
)
st.download_button(
    "Download my picks (CSV)",
    scored.to_csv(index=False).encode("utf-8"),
    file_name="my_picks.csv",
    mime="text/csv",
)
