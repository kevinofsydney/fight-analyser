"""Tale of the tape: side-by-side stats, matchup projections and matching hypotheses."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import logging
import math

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

from fightdb.analytics import hypothesis, schema
from fightdb.app import components, data
from fightdb.config import get_settings
from fightdb.features.stat_meta import BY_KEY, STATS
from fightdb.models import outcome, ratings

components.page("Bout")
st.title("Bout")
reset_requested = st.button("Reset", help="Return to the next upcoming event and its main event")

events = data.query(
    """(SELECT event_id, name, date, status FROM events WHERE status = 'upcoming')
       UNION ALL
       (SELECT event_id, name, date, status FROM events WHERE status = 'completed' ORDER BY date DESC LIMIT 30)
       ORDER BY status DESC, date"""
)
if reset_requested:
    if (events["status"] == "upcoming").any():
        for key in ("bout_id", "bout_event_selector", "bout_selector"):
            st.session_state.pop(key, None)
    else:
        st.info("No upcoming event is available yet.")
preset = st.session_state.get("bout_id")
preset_event = None
if preset:
    row = data.query(
        "SELECT e.event_id, e.name, e.date, e.status FROM events e JOIN bouts b USING (event_id) WHERE b.bout_id = ?",
        [preset],
    )
    preset_event = row["event_id"].iloc[0] if not row.empty else None
    if preset_event and preset_event not in events["event_id"].values:
        events = pd.concat([events, row], ignore_index=True)

ev_ids = events["event_id"].tolist()
ev_label = dict(
    zip(
        events["event_id"],
        events["name"] + " · " + events["date"].map(components.fmt_date),
        strict=True,
    )
)
c1, c2 = st.columns(2)
event_id = c1.selectbox(
    "Event",
    ev_ids,
    format_func=ev_label.get,
    index=ev_ids.index(preset_event) if preset_event in ev_ids else 0,
    key="bout_event_selector",
)
card = data.event_card(event_id)
if card.empty:
    st.info("No bouts on this card yet.")
    st.stop()
b_ids = card["bout_id"].tolist()
bout_id = c2.selectbox(
    "Bout",
    b_ids,
    format_func=lambda b: " vs ".join(
        card.loc[card["bout_id"] == b, ["fighter_name", "opp_name"]].iloc[0]
    ),
    index=b_ids.index(preset) if preset in b_ids else 0,
    key="bout_selector",
)
st.session_state["bout_id"] = bout_id

sides = data.bout_sides(bout_id)
if len(sides) != 2:
    st.error("Bout data incomplete.")
    st.stop()
a, b = sides.iloc[0], sides.iloc[1]


def num(x: object) -> float:
    try:
        v = float(x)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return math.nan
    return v


# Fighter details are current; bout snapshots and age remain as they were for the fight.
bios = data.query(
    "SELECT fighter_id, nickname, pro_wins, pro_losses, pro_draws FROM fighters WHERE fighter_id IN (?, ?)",
    [a.fighter_id, b.fighter_id],
).set_index("fighter_id")
aliases = data.query(
    "SELECT fighter_id, source_id FROM fighter_aliases WHERE source = ? AND fighter_id IN (?, ?)",
    ["espn", a.fighter_id, b.fighter_id],
)
closing = (
    data.query(
        "SELECT fighter_id, close_low, close_high FROM odds WHERE bout_id = ?", [bout_id]
    ).set_index("fighter_id")
    if a.status == "completed"
    else pd.DataFrame()
)
has_odds_table = not data.query(
    "SELECT table_name FROM information_schema.tables WHERE table_name = ?", ["upcoming_odds"]
).empty
prices = (
    data.query(
        """SELECT bookmaker, fighter_id, decimal_odds, updated_at, fetched_at
           FROM upcoming_odds WHERE bout_id = ? ORDER BY bookmaker, fighter_id""",
        [bout_id],
    )
    if a.status == "scheduled" and has_odds_table
    else pd.DataFrame()
)

st.caption(
    f"{a.weight_class or a.division} · {int(a.scheduled_rounds) if num(a.scheduled_rounds) == num(a.scheduled_rounds) else '?'} rounds · "
    f"{a.event_name} · {components.fmt_date(a.event_date)}"
)
profile_cols = st.columns(2)
profiles = []
for col, side in zip(profile_cols, (a, b), strict=True):
    fighter_id = str(side.fighter_id)
    matches = aliases.loc[aliases["fighter_id"] == fighter_id, "source_id"].dropna().astype(str)
    espn_ids = sorted({value for value in matches if value.isascii() and value.isdigit()})
    if not espn_ids and fighter_id.startswith("espn:"):
        espn_ids = [fighter_id.removeprefix("espn:")]
    espn_id = espn_ids[0] if len(espn_ids) == 1 else None
    with col:
        st.markdown(
            components.fighter_portrait(str(side.fighter_name), espn_id), unsafe_allow_html=True
        )
        st.subheader(side.fighter_name)
        bio = bios.loc[fighter_id] if fighter_id in bios.index else None
        nickname = bio["nickname"] if bio is not None else None
        if nickname is not None and pd.notna(nickname) and nickname:
            st.caption(f"“{nickname}”")
    record = (
        components.pro_record(bio["pro_wins"], bio["pro_losses"], bio["pro_draws"])
        if bio is not None
        else "—"
    )
    if side.status == "completed":
        quote = closing.loc[fighter_id] if fighter_id in closing.index else None
        odds_value = (
            components.format_closing_range(quote["close_low"], quote["close_high"])
            if quote is not None
            else "—"
        )
        odds_label = "Closing odds"
    else:
        odds_value = (
            components.odds_range(prices.loc[prices["fighter_id"] == fighter_id, "decimal_odds"])
            if not prices.empty
            else "—"
        )
        odds_label = "Bookmaker odds (decimal)"
    profiles.append(
        [
            "—",
            record,
            odds_value,
            components.fmt_stat("age_years", num(side.self_age_years)),
            "—",
            components.fmt_stat("height_cm", num(side.self_height_cm)),
            components.fmt_stat("reach_cm", num(side.self_reach_cm)),
            str(side.self_stance).strip()
            if pd.notna(side.self_stance) and str(side.self_stance).strip()
            else "—",
            "—",
        ]
    )

profile_table = pd.DataFrame(
    {
        "Stat": [
            "Current rank",
            "Current pro record",
            odds_label,
            "Age at bout",
            "Weigh-in weight",
            "Height",
            "Reach",
            "Stance",
            "Gym",
        ],
        str(a.fighter_name): profiles[0],
        str(b.fighter_name): profiles[1],
    }
)


def highlight_profile_advantage(row: pd.Series) -> list[str]:
    fields = {
        "Age at bout": ("self_age_years", True),
        "Height": ("self_height_cm", False),
        "Reach": ("self_reach_cm", False),
    }
    styles = [""] * len(row)
    if row["Stat"] in fields:
        field, lower_is_better = fields[row["Stat"]]
        left, right = num(a[field]), num(b[field])
        if math.isfinite(left) and math.isfinite(right) and left != right:
            winner = 1 if (left < right if lower_is_better else left > right) else 2
            styles[winner] = "color: #26d16d; font-weight: bold"
    return styles


st.dataframe(
    profile_table.style.apply(highlight_profile_advantage, axis=1),
    hide_index=True,
    width="stretch",
    height="content",
)

if a.status == "completed":
    winner = a.fighter_name if a.won == 1 else b.fighter_name if b.won == 1 else None
    st.success(
        f"Result: {winner + ' won' if winner else 'No winner'} · {a.method or ''} · round {a.end_round}"
    )

ELO_HELP = (
    f"Every fighter starts at {ratings.ELO_START:.0f}. After each UFC fight, Elo changes by "
    f"K × (result − expected): result is 1 for a win, 0.5 for a draw and 0 for a loss; "
    f"expected = 1 / (1 + 10^((opponent Elo − own Elo) / 400)); "
    f"K = {ratings.ELO_K:.0f}, or {ratings.ELO_K * ratings.FINISH_MULT:.0f} for a KO/TKO or "
    "submission. No contests don't count. Open “How Elo was calculated” below for each fight."
)
RESULT_LABELS = {"W": "Win", "L": "Loss", "D": "Draw", "NC": "No contest"}
METHOD_LABELS = {"ko_tko": "KO/TKO", "sub": "Submission", "dec": "Decision"}

elo_cols = st.columns(2)
for col, side in zip(elo_cols, (a, b), strict=True):
    col.metric(
        f"{side.fighter_name} · Elo before fight",
        f"{num(side.self_elo):.0f}" if not math.isnan(num(side.self_elo)) else "—",
        help=ELO_HELP,
    )

with st.expander("How Elo was calculated"):
    st.markdown(
        f"Each fighter starts at **{ratings.ELO_START:.0f}**. After every UFC fight the rating "
        "moves by **K × (result − expected)**:\n"
        "- **result**: 1 for a win, 0.5 for a draw, 0 for a loss (no contests don't count)\n"
        "- **expected**: the chance of winning implied by the two ratings, "
        "1 / (1 + 10^((opponent − own) / 400)); a 100-point edge means about 64%\n"
        f"- **K**: {ratings.ELO_K:.0f} for a decision, "
        f"{ratings.ELO_K * ratings.FINISH_MULT:.0f} for a KO/TKO or submission\n\n"
        "Beating a higher-rated opponent earns more than beating a lower-rated one, and the "
        "opponent loses what the winner gains. Fights on the same day use ratings from before "
        "that day. Only fights before this bout count."
    )
    for side in (a, b):
        history = data.query(
            """SELECT bout_id, event_date, opp_name, self_elo, opp_elo, result, method_group
               FROM bout_sides
               WHERE fighter_id = ? AND status = 'completed' AND event_date < ?""",
            [side.fighter_id, side.event_date],
        )
        steps = ratings.elo_breakdown(history)
        st.markdown(f"**{side.fighter_name}**")
        if steps.empty:
            st.caption(
                f"No earlier UFC fights, so the rating is the {ratings.ELO_START:.0f} starting value."
            )
            continue
        st.dataframe(
            pd.DataFrame(
                {
                    "Date": steps["event_date"].map(components.fmt_date),
                    "Opponent": steps["opp_name"],
                    "Elo before": steps["self_elo"].round(0),
                    "Opponent Elo": steps["opp_elo"].round(0),
                    "Expected win": (100 * steps["expected"]).round(0).astype(int).astype(str)
                    + "%",
                    "Result": steps["result"].map(lambda r: RESULT_LABELS.get(r, r or "—")),
                    "Method": steps["method_group"].map(lambda m: METHOD_LABELS.get(m, m or "—")),
                    "K": steps["k"],
                    "Change": steps["change"].round(1),
                    "Elo after": steps["elo_after"].round(0),
                }
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "Change": st.column_config.NumberColumn(format="%+.1f"),
                "Elo before": st.column_config.NumberColumn(format="%.0f"),
                "Opponent Elo": st.column_config.NumberColumn(format="%.0f"),
                "Elo after": st.column_config.NumberColumn(format="%.0f"),
                "K": st.column_config.NumberColumn(format="%.0f"),
            },
        )
        st.caption(
            f"{ratings.ELO_START:.0f} {steps['change'].sum():+.1f} over {len(steps)} fights "
            f"= {num(side.self_elo):.0f} before this bout."
        )

for side in (a, b):
    if num(side.self_ufc_fights) < 3:
        st.warning(
            f"{side.fighter_name} has {int(num(side.self_ufc_fights))} prior UFC fights: rates are mostly the division average (shrinkage) and noisy."
        )

if a.status == "completed":
    fight_stats = data.query(
        """SELECT r.round AS "Round", f.name AS "Fighter", r.kd AS "Knockdowns",
                  r.sig_l AS "Sig. strikes landed", r.sig_a AS "Sig. strikes attempted",
                  r.tot_l AS "Total strikes landed", r.tot_a AS "Total strikes attempted",
                  r.td_l AS "Takedowns landed", r.td_a AS "Takedowns attempted",
                  r.sub_att AS "Submission attempts", r.rev AS "Reversals", r.ctrl_s AS "Control seconds"
           FROM round_stats r JOIN fighters f USING (fighter_id)
           WHERE r.bout_id = ? ORDER BY r.round, f.name""",
        [bout_id],
    )
    st.subheader("Fight statistics by round")
    if fight_stats.empty:
        st.caption("Round statistics are not available for this fight.")
    else:
        st.dataframe(fight_stats, hide_index=True, width="stretch")

# --- Market odds ---------------------------------------------------------------------------
st.subheader("Fight odds")
if a.status == "completed":
    columns = st.columns(2)
    for col, side in zip(columns, (a, b), strict=True):
        price = closing.loc[side.fighter_id] if side.fighter_id in closing.index else None
        col.metric(
            f"{side.fighter_name} · closing odds",
            components.format_closing_range(price["close_low"], price["close_high"])
            if price is not None
            else "—",
        )
    st.caption(
        "Historical closing price range from fight-canon-db; availability varies by fight and bookmaker."
    )
else:
    if prices.empty:
        st.caption("No upcoming bookmaker odds are available for this fight yet.")
    else:
        names = {a.fighter_id: a.fighter_name, b.fighter_id: b.fighter_name}
        prices["Fighter"] = prices["fighter_id"].map(names)
        odds_table = prices.pivot_table(
            index="bookmaker", columns="Fighter", values="decimal_odds", aggfunc="last"
        )
        odds_table = odds_table.reindex(columns=[a.fighter_name, b.fighter_name])
        odds_table = odds_table.map(lambda value: f"{value:.2f}" if pd.notna(value) else "—")
        updated = (
            prices.groupby("bookmaker")["updated_at"]
            .max()
            .map(lambda value: components.fmt_date(value, with_time=True))
        )
        odds_table["Bookmaker update (UTC)"] = updated
        st.dataframe(
            odds_table.rename_axis("Bookmaker").reset_index(), hide_index=True, width="stretch"
        )
        st.caption(
            f"The Odds API · Australian bookmakers · last fetched {components.fmt_date(prices['fetched_at'].max(), with_time=True)} UTC. Odds may have changed since the last daily refresh."
        )
has_history_table = not data.query(
    "SELECT table_name FROM information_schema.tables WHERE table_name = ?",
    ["upcoming_odds_history"],
).empty
history = (
    data.query(
        """SELECT fighter_id, fetched_at, median(decimal_odds) AS median_odds,
                  min(decimal_odds) AS low, max(decimal_odds) AS high,
                  count(*) AS books
           FROM upcoming_odds_history WHERE bout_id = ?
           GROUP BY fighter_id, fetched_at ORDER BY fetched_at""",
        [bout_id],
    )
    if has_history_table
    else pd.DataFrame()
)
if not history.empty:
    st.markdown("**Betting line movement**")
    order = [str(a.fighter_name), str(b.fighter_name)]
    history["Fighter"] = history["fighter_id"].map({a.fighter_id: order[0], b.fighter_id: order[1]})
    history["implied"] = 1 / history["median_odds"]
    color = alt.Color(
        "Fighter:N",
        scale=alt.Scale(domain=order, range=["#3987e5", "#d95926"]),
        legend=alt.Legend(orient="top", title=None, symbolType="circle", symbolOpacity=1),
    )
    x = alt.X(
        "fetched_at:T", title="Price fetched (UTC)", axis=alt.Axis(format="%-d %b", tickCount="day")
    )
    tooltip = [
        alt.Tooltip("Fighter:N"),
        alt.Tooltip("fetched_at:T", title="Fetched", format="%-d %b %Y %H:%M"),
        alt.Tooltip("median_odds:Q", title="Median odds", format=".2f"),
        alt.Tooltip("low:Q", title="Lowest", format=".2f"),
        alt.Tooltip("high:Q", title="Highest", format=".2f"),
        alt.Tooltip("implied:Q", title="Implied chance", format=".0%"),
        alt.Tooltip("books:Q", title="Bookmakers"),
    ]
    base = alt.Chart(history).encode(x=x, color=color)
    band = base.mark_area(opacity=0.18).encode(
        y=alt.Y("low:Q", title="Decimal odds", scale=alt.Scale(zero=False)), y2="high:Q"
    )
    line = base.mark_line(strokeWidth=2).encode(y="median_odds:Q")
    points = base.mark_point(filled=True, size=70).encode(y="median_odds:Q", tooltip=tooltip)
    st.altair_chart((band + line + points).properties(height=280), width="stretch")
    polls = history["fetched_at"].nunique()
    st.caption(
        "Median price across bookmakers at each daily fetch; the shaded band spans the lowest "
        "to highest price. Shorter odds mean the market rates that fighter more likely to win."
        + (" Movement appears as more daily fetches are recorded." if polls < 2 else "")
    )

if a.status == "scheduled":
    st.link_button(
        "Refresh odds via GitHub",
        "https://github.com/kevinofsydney/fight-analyser/actions/workflows/refresh.yml",
        help="Open the Daily refresh workflow and select Run workflow. Odds are fetched at most once per UTC day.",
    )
    st.caption(
        "Run the Daily refresh workflow on GitHub to request an update; the daily odds limit still applies."
    )
st.caption("Prices are for comparison and are not betting advice.")

# --- Win and method probabilities ---------------------------------------------------------
div = a.division
base = data.query(
    """SELECT avg(won_ko) AS ko, avg(won_sub) AS sub, avg(won_dec) AS dec, avg(went_distance) AS dist
       FROM bout_sides WHERE status = 'completed' AND division = ? AND event_date >= current_date - INTERVAL 3 YEAR""",
    [div],
).iloc[0]

st.subheader("Model outlook")
# Low / medium / high labels only for methods whose probabilities pass the calibration check
# (FR-31), each method judged on its own.
levels_ok = data.method_labels_calibrated()
cols = st.columns(2)
for col, s in zip(cols, (a, b), strict=True):
    with col:
        pw = num(s.self_p_win)
        st.metric(
            s.fighter_name,
            "—" if math.isnan(pw) else f"{pw:.0%} to win",
            help="Calibrated logistic model on point-in-time ratings (walk-forward). Not betting advice.",
        )
        if not math.isnan(num(s.self_implied_prob)):
            st.caption(f"Closing line implied: {num(s.self_implied_prob):.0%}")
        rows = []
        for label, key, bkey in (
            ("KO/TKO", "self_p_win_ko", "ko"),
            ("Submission", "self_p_win_sub", "sub"),
            ("Decision", "self_p_win_dec", "dec"),
        ):
            p = num(s[key])
            rows.append(
                {
                    "Method": f"Wins by {label}",
                    "Probability": "—" if math.isnan(p) else f"{p:.0%}",
                    "Division avg": f"{num(base[bkey]):.0%}",
                    "Level": components.level_vs_baseline(p, num(base[bkey]))
                    if levels_ok.get(bkey)
                    else "—",
                }
            )
        table = pd.DataFrame(rows)
        if not any(levels_ok.values()):
            table = table.drop(columns=["Level"])
        st.dataframe(table, hide_index=True, width="stretch")
hidden = [m for m, ok in levels_ok.items() if not ok]
if hidden:
    st.caption(
        "Levels hidden for "
        + ", ".join({"ko": "KO/TKO", "sub": "submission", "dec": "decision"}[m] for m in hidden)
        + ": those probabilities haven't passed their calibration check."
    )
pd_ = num(a.self_p_distance)
if not math.isnan(pd_):
    st.caption(
        f"Chance the fight goes the distance: {pd_:.0%} (division average {num(base['dist']):.0%})."
    )

with st.expander("What goes into the model outlook?"):
    st.write(
        "The win model compares the fighters using the pre-fight inputs below. "
        "Index values are relative to the division average (100); projected numbers are per round. "
        "These are model inputs, not a breakdown of how much each one changed the probability."
    )
    projection_labels = {
        "x_sig_r": "Projected significant strikes landed / round",
        "x_td_r": "Projected takedowns / round",
        "x_kd_r": "Projected knockdowns / round",
        "x_ctrl_s_r": "Projected control seconds / round",
    }

    def input_label(key: str) -> str:
        raw = key.endswith("_raw")
        name = key.removesuffix("_raw")
        label = BY_KEY[name].label if name in BY_KEY else projection_labels.get(name, name)
        return label + (" (before schedule adjustment)" if raw else "")

    def input_value(side: pd.Series, key: str) -> str:
        value = num(side.get(f"self_{key}"))
        if math.isnan(value):
            return "—"
        if key.endswith("_raw"):
            return f"{value:.1f}"
        if key in BY_KEY:
            return components.fmt_stat(key, value)
        return f"{value:.2f}"

    index_explanations = {
        "idx_str_off": (
            "100 × the fighter's adjusted significant strikes landed per minute ÷ "
            "the division's average significant strikes landed per minute."
        ),
        "idx_str_def": (
            "100 × the division's average significant strikes landed per minute ÷ "
            "the fighter's adjusted significant strikes absorbed per minute. "
            "A higher value means fewer strikes absorbed."
        ),
        "idx_wrest_off": (
            "100 × (adjusted takedown attempts per 15 minutes × adjusted takedown accuracy) "
            "÷ the division's average takedowns landed per 15 minutes."
        ),
        "idx_wrest_def": (
            "100 × the division's average takedown accuracy ÷ the fighter's adjusted "
            "proportion of opponent takedown attempts allowed. A higher value means fewer allowed."
        ),
        "idx_sub_threat": (
            "100 × the fighter's adjusted submission attempts per 15 minutes ÷ "
            "the division's average submission attempts per 15 minutes."
        ),
        "idx_sub_def": (
            "100 × the division's average submission losses per opponent attempt ÷ "
            "the fighter's adjusted submission losses per opponent attempt. "
            "A higher value means fewer submission losses per attempt faced."
        ),
        "idx_durability": (
            "100 × the division's average knockdowns absorbed per significant strike absorbed "
            "÷ the fighter's adjusted knockdowns absorbed per significant strike absorbed. "
            "A higher value means fewer knockdowns per strike absorbed; this is not a medical assessment."
        ),
    }

    def index_help(key: str) -> str | None:
        raw = key.endswith("_raw")
        name = key.removesuffix("_raw")
        explanation = index_explanations.get(name)
        if explanation is None:
            return None
        details = (
            " Rates are adjusted towards the division average when the fighter has limited "
            "UFC data. Division averages usually use the three years before the fight; "
            "broader data is used when the sample is small. "
            "100 represents the division baseline."
        )
        if name in ("idx_str_off", "idx_str_def", "idx_wrest_off", "idx_wrest_def"):
            details += (
                " This version is before the opponent-strength adjustment."
                if raw
                else " This version also adjusts for the strength of previous opponents."
            )
        return explanation + details

    def input_table(keys: list[str]) -> None:
        header = st.columns([2, 1.45, 1.45])
        for col, label in zip(header, ("Input", a.fighter_name, b.fighter_name), strict=True):
            col.caption(label)
        for key in keys:
            cols = st.columns([2, 1.45, 1.45], vertical_alignment="center")
            cols[0].markdown(input_label(key), help=index_help(key))
            cols[1].text(input_value(a, key))
            cols[2].text(input_value(b, key))

    win_keys = [key.removeprefix("diff_") for key in outcome.DIFF_FEATURES]
    st.markdown("**Win probability inputs**")
    st.caption("Hover over the question mark next to an index to see how it is calculated.")
    input_table(win_keys)
    method_keys = list(
        dict.fromkeys(
            [key.removeprefix("diff_") for key in outcome.METHOD_DIFF_FEATURES]
            + [
                key.removeprefix("self_")
                for key in outcome.LEVEL_FEATURES
                if key.startswith("self_")
            ]
        )
    )
    st.markdown("**Method-of-victory probability inputs**")
    st.caption(
        "The method model also considers the individual levels below. It uses the unadjusted versions of certain indices, as labelled."
    )
    input_table(method_keys)
    st.caption(
        "Missing inputs are replaced with zero in the model; a dash means no recorded value. "
        "Method probabilities are scaled to agree with the win probability. "
        "The closing betting line and division averages are shown for comparison and are not model inputs."
    )

# --- Matchup projections -------------------------------------------------------------------
st.subheader("Projected per round")
proj_rows = [
    ("Takedowns landed", "self_x_td_r", "{:.2f}"),
    ("Takedown attempts", "self_x_td_att_r", "{:.2f}"),
    ("Takedown success chance", "self_p_td_success", "{:.0%}"),
    ("Sig. strikes landed", "self_x_sig_r", "{:.1f}"),
    ("Sig. strike accuracy", "self_p_sig_land", "{:.0%}"),
    ("Control time (sec)", "self_x_ctrl_s_r", "{:.0f}"),
    ("Knockdowns", "self_x_kd_r", "{:.3f}"),
    ("Submission attempts", "self_x_sub_att_r", "{:.2f}"),
]


def f(v: object, fmt: str) -> str:
    x = num(v)
    return "—" if math.isnan(x) else fmt.format(x)


st.dataframe(
    pd.DataFrame(
        [
            {"Projection": lab, a.fighter_name: f(a[k], fm), b.fighter_name: f(b[k], fm)}
            for lab, k, fm in proj_rows
        ]
    ),
    hide_index=True,
    width="stretch",
)
with st.expander("Show the maths (takedowns)"):
    snap = data.query("SELECT * FROM fighter_snapshots WHERE bout_id = ?", [bout_id]).set_index(
        "fighter_id"
    )
    for s, o in ((a, b), (b, a)):
        if s.fighter_id not in snap.index or o.fighter_id not in snap.index:
            continue
        sa, so = snap.loc[s.fighter_id], snap.loc[o.fighter_id]
        base_att = sa["b_td_att_pm"] * 15
        st.markdown(
            f"**{s.fighter_name}** attempts {sa['td_att_p15_s']:.2f} TD/15 (adj.); {o.fighter_name}'s opponents attempt "
            f"{so['opp_td_att_p15_s']:.2f}/15; division average {base_att:.2f}/15 → expected attempts "
            f"{sa['td_att_p15_s']:.2f} × {so['opp_td_att_p15_s']:.2f} / {base_att:.2f} = {num(s.self_x_td_att_r) * 3:.2f}/15.  \n"
            f"Success: {s.fighter_name} lands {sa['td_acc_s']:.0%} (adj.), {o.fighter_name} allows {so['td_allowed_s']:.0%}, "
            f"division average {sa['b_td_acc']:.0%} → odds-ratio combination = **{num(s.self_p_td_success):.0%}** per attempt.  \n"
            f"Expected takedowns: **{num(s.self_x_td_r):.2f} per round**."
        )

# --- Tale of the tape -----------------------------------------------------------------------
st.subheader("Side by side (before this fight)")
show_adjusted = st.toggle("Include adjusted (shrunk) stats", value=False)
rows = []
for stat in STATS:
    if (stat.group == "Adjusted" and not show_adjusted) or f"self_{stat.key}" not in a.index:
        continue
    va, vb = num(a.get(f"self_{stat.key}")), num(b.get(f"self_{stat.key}"))
    edge = ""
    if stat.higher_is_better is not None and not (math.isnan(va) or math.isnan(vb)) and va != vb:
        better_a = (va > vb) == stat.higher_is_better
        margin = abs(va - vb)
        m = (
            components.fmt_diff_cm(margin).split(" / ")[0].lstrip("+")
            if stat.unit == "cm"
            else components.fmt_stat(stat.key, margin).lstrip("+")
        )
        edge = f"{(a if better_a else b).fighter_name} (+{m})"
    rows.append(
        {
            "Group": stat.group,
            "Stat": stat.label,
            a.fighter_name: components.fmt_stat(stat.key, va),
            b.fighter_name: components.fmt_stat(stat.key, vb),
            "Edge": edge,
        }
    )
tape = pd.DataFrame(rows)
st.dataframe(tape, hide_index=True, width="stretch", height=min(38 * (len(tape) + 1), 1400))
st.caption(
    f"Reach difference: {a.fighter_name} {components.fmt_diff_cm(num(a.diff_reach_cm))}. Stances: {a.self_stance or '?'} vs {b.self_stance or '?'}."
)

st.caption(
    "Fight-wear totals count recorded UFC fights only. Missing round statistics make totals unavailable; strikes absorbed are exposure, not a medical diagnosis."
)

# --- Matching hypotheses ---------------------------------------------------------------------
st.subheader("Saved hypotheses that apply")
found = False
con = data.connect()
try:
    for _path, h in schema.load_all(get_settings().hypotheses_dir):
        try:
            m = hypothesis.matches_upcoming(con, h) if a.status == "scheduled" else pd.DataFrame()
            hits = m[m["bout_id"] == bout_id] if not m.empty else m
            for row in hits.itertuples():
                r = hypothesis.evaluate(con, h, keep_bouts=False)
                found = True
                st.markdown(f"**{h.title}** applies to **{row.fighter_name}**.  \n{r.verdict}")
        except duckdb.Error as exc:  # one bad hypothesis must not break the page
            logging.getLogger(__name__).warning("hypothesis %s failed: %s", h.id, exc)
finally:
    con.close()
if not found:
    st.caption(
        "None of the saved hypotheses' conditions hold for this bout."
        if a.status == "scheduled"
        else "Shown for upcoming bouts only."
    )

# --- Common opponents ------------------------------------------------------------------------
common = data.query(
    """SELECT x.opp_name AS opponent, x.fighter_name AS fighter, x.result, x.method, x.event_date
       FROM bout_sides x
       WHERE x.status = 'completed' AND x.fighter_id IN (?, ?) AND x.event_date < ?
         AND x.opp_id IN (
           SELECT opp_id FROM bout_sides WHERE status = 'completed' AND fighter_id = ? AND event_date < ?
           INTERSECT
           SELECT opp_id FROM bout_sides WHERE status = 'completed' AND fighter_id = ? AND event_date < ?)
       ORDER BY opponent, event_date""",
    [
        a.fighter_id,
        b.fighter_id,
        a.event_date,
        a.fighter_id,
        a.event_date,
        b.fighter_id,
        a.event_date,
    ],
)
if not common.empty:
    st.subheader("Common opponents")
    st.dataframe(
        components.format_dates(common, dates=("event_date",)), hide_index=True, width="stretch"
    )
