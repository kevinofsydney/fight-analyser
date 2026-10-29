"""Tale of the tape: side-by-side stats, matchup projections and matching hypotheses."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import math

import pandas as pd
import streamlit as st

from fightdb.analytics import hypothesis, schema
from fightdb.app import components, data
from fightdb.config import get_settings
from fightdb.features.stat_meta import STATS

components.page("Tale of the tape")
st.title("Tale of the tape")

events = data.query(
    """(SELECT event_id, name, date, status FROM events WHERE status = 'upcoming')
       UNION ALL
       (SELECT event_id, name, date, status FROM events WHERE status = 'completed' ORDER BY date DESC LIMIT 30)
       ORDER BY status DESC, date"""
)
preset = st.session_state.get("bout_id")
preset_event = None
if preset:
    row = data.query("SELECT event_id FROM bouts WHERE bout_id = ?", [preset])
    preset_event = row["event_id"].iloc[0] if not row.empty else None

ev_ids = events["event_id"].tolist()
ev_label = dict(
    zip(events["event_id"], events["name"] + " · " + events["date"].astype(str), strict=True)
)
c1, c2 = st.columns(2)
event_id = c1.selectbox(
    "Event",
    ev_ids,
    format_func=ev_label.get,
    index=ev_ids.index(preset_event) if preset_event in ev_ids else 0,
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
    except (TypeError, ValueError):
        return math.nan
    return v


st.header(f"{a.fighter_name}  vs  {b.fighter_name}")
st.caption(
    f"{a.weight_class or a.division} · {int(a.scheduled_rounds) if num(a.scheduled_rounds) == num(a.scheduled_rounds) else '?'} rounds · "
    f"{a.event_name} · {a.event_date:%d %b %Y}"
)
if a.status == "completed":
    winner = a.fighter_name if a.won == 1 else b.fighter_name if b.won == 1 else None
    st.success(
        f"Result: {winner + ' won' if winner else 'No winner'} · {a.method or ''} · round {a.end_round}"
    )

for side in (a, b):
    if num(side.self_ufc_fights) < 3:
        st.warning(
            f"{side.fighter_name} has {int(num(side.self_ufc_fights))} prior UFC fights: rates are mostly the division average (shrinkage) and noisy."
        )

# --- Win and method probabilities ---------------------------------------------------------
div = a.division
base = data.query(
    """SELECT avg(won_ko) AS ko, avg(won_sub) AS sub, avg(won_dec) AS dec, avg(went_distance) AS dist
       FROM bout_sides WHERE status = 'completed' AND division = ? AND event_date >= current_date - INTERVAL 3 YEAR""",
    [div],
).iloc[0]

st.subheader("Model outlook")
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
                    "Level": components.level_vs_baseline(p, num(base[bkey])),
                }
            )
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
pd_ = num(a.self_p_distance)
if not math.isnan(pd_):
    st.caption(
        f"Chance the fight goes the distance: {pd_:.0%} (division average {num(base['dist']):.0%})."
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
    if stat.group == "Adjusted" and not show_adjusted:
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

# --- Matching hypotheses ---------------------------------------------------------------------
st.subheader("Saved hypotheses that apply")
found = False
con = data.connect()
try:
    for _path, h in schema.load_all(get_settings().hypotheses_dir):
        m = hypothesis.matches_upcoming(con, h) if a.status == "scheduled" else pd.DataFrame()
        hits = m[m["bout_id"] == bout_id] if not m.empty else m
        for row in hits.itertuples():
            found = True
            r = hypothesis.evaluate(con, h, keep_bouts=False)
            st.markdown(f"**{h.title}** applies to **{row.fighter_name}**.  \n{r.verdict}")
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
    st.dataframe(common, hide_index=True, width="stretch")
