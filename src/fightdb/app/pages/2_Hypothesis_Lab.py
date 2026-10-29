"""Build a hypothesis from a form, see honest statistics, sweep thresholds, export as YAML."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import datetime as dt
import math

import altair as alt
import streamlit as st
from pydantic import ValidationError

from fightdb.analytics import hypothesis, schema
from fightdb.app import components, data
from fightdb.features.stat_meta import BY_KEY, OUTCOMES, STATS
from fightdb.sources.units import DIVISIONS

components.page("Hypothesis Lab", "🧪")
st.title("Hypothesis Lab")
st.caption(
    "Every bout counts twice, once from each fighter's side. A condition like "
    "'diff_age_years ≥ 8' means *this fighter is at least 8 years older than the opponent*."
)

st.session_state.setdefault("tests_run", 0)

PERSPECTIVES = {
    "diff": "difference (this fighter − opponent)",
    "self": "this fighter",
    "opp": "the opponent",
}
feature_options = [s.key for s in STATS]

with st.form("hyp"):
    st.subheader("1. Population")
    c1, c2, c3, c4 = st.columns(4)
    divisions = c1.multiselect("Divisions", DIVISIONS, default=["lightweight"])
    sex = c2.selectbox("Sex", ["M", "F", "Any"], index=0)
    date_from = c3.date_input("From", dt.date(2010, 1, 1), min_value=dt.date(1993, 1, 1))
    min_fights = c4.number_input("Min prior UFC fights (each fighter)", 0, 30, 0)
    c5, c6 = st.columns(2)
    title_only = c5.checkbox("Title fights only")
    five_rounds = c6.checkbox("5-round fights only")

    st.subheader("2. Condition")
    n_clauses = st.number_input("Number of conditions (all must hold)", 1, 4, 1)
    clauses = []
    for i in range(int(n_clauses)):
        cc = st.columns([2, 3, 1, 2])
        persp = cc[0].selectbox("Of", list(PERSPECTIVES), format_func=PERSPECTIVES.get, key=f"p{i}")
        feat = cc[1].selectbox(
            "Stat",
            feature_options,
            format_func=lambda k: f"{BY_KEY[k].label} ({BY_KEY[k].unit})",
            index=feature_options.index("age_years") if i == 0 else 0,
            key=f"f{i}",
        )
        op = cc[2].selectbox("Op", [">=", ">", "<=", "<", "==", "!="], key=f"o{i}")
        val = cc[3].number_input(
            "Value",
            value=8.0 if i == 0 else 0.0,
            step=0.5,
            key=f"v{i}",
            help="Percentages are fractions: 50% = 0.5",
        )
        clauses.append({"feature": f"{persp}_{feat}", "op": op, "value": val})

    st.subheader("3. Outcome and comparison")
    c7, c8 = st.columns(2)
    outcome = c7.selectbox("Success means", list(OUTCOMES), format_func=OUTCOMES.get)
    compare = c8.selectbox(
        "Compare against",
        ["baseline_50", "complement", "division_rate"],
        format_func={
            "baseline_50": "50% (coin flip)",
            "complement": "Fighters where the condition is false",
            "division_rate": "Everyone in the population",
        }.get,
    )
    sweep_on = st.checkbox("Also sweep the first condition's threshold", value=True)
    s1, s2, s3 = st.columns(3)
    sw_from = s1.number_input("Sweep from", value=0.0)
    sw_to = s2.number_input("Sweep to", value=14.0)
    sw_step = s3.number_input("Step", value=1.0, min_value=0.01)
    submitted = st.form_submit_button("Run", type="primary")

if not submitted and "last_h" not in st.session_state:
    st.stop()

if submitted:
    try:
        h = schema.Hypothesis(
            id="lab",
            title="Hypothesis Lab query",
            population=schema.Population(
                divisions=divisions,
                sex=None if sex == "Any" else sex,
                date_from=date_from,
                min_ufc_fights_each=int(min_fights),
                title_only=title_only,
                scheduled_rounds=5 if five_rounds else None,
            ),
            condition=[schema.Clause(**c) for c in clauses],
            outcome=outcome,
            compare_to=compare,
            sweep=schema.Sweep(
                feature=clauses[0]["feature"],
                op=clauses[0]["op"],
                start=sw_from,
                stop=sw_to,
                step=sw_step,
            )
            if sweep_on
            else None,
        )
    except ValidationError as exc:
        st.error(str(exc))
        st.stop()
    st.session_state["last_h"] = h
    st.session_state["tests_run"] += 1
h = st.session_state["last_h"]

con = data.connect()
try:
    r = hypothesis.evaluate(con, h)
    sw = hypothesis.sweep(con, h) if h.sweep else None
    upcoming = hypothesis.matches_upcoming(con, h)
finally:
    con.close()

st.divider()
m1, m2, m3, m4 = st.columns(4)
m1.metric(
    "Success rate",
    "—" if math.isnan(r.rate) else f"{r.rate:.1%}",
    f"{r.diff_pp:+.1f} pp vs baseline" if r.n else None,
)
m2.metric("95% CI", f"{r.ci_low:.0%} – {r.ci_high:.0%}")
m3.metric("Sample (bout-sides)", f"{r.n:,}")
m4.metric("p-value", f"{r.p_value:.3f}")
(st.success if r.significant and r.n >= hypothesis.SMALL_N else st.info)(r.verdict)
for w in r.warnings:
    st.warning(w)
if st.session_state["tests_run"] >= 5:
    st.caption(
        f"You've run {st.session_state['tests_run']} hypotheses this session. With many tries, about 1 in 20 "
        "will look 'significant' by chance alone. Prefer the sweep chart over a single cherry-picked threshold."
    )

if sw is not None and not sw.empty:
    st.subheader("Sweep")
    sw = sw.dropna(subset=["rate"])
    band = (
        alt.Chart(sw)
        .mark_area(opacity=0.25)
        .encode(
            x=alt.X("threshold:Q", title=h.sweep.feature if h.sweep else ""),
            y=alt.Y("ci_low:Q", title="Success rate", axis=alt.Axis(format="%")),
            y2="ci_high:Q",
        )
    )
    line = (
        alt.Chart(sw)
        .mark_line(point=True)
        .encode(
            x="threshold:Q",
            y="rate:Q",
            tooltip=[
                "threshold",
                "n",
                alt.Tooltip("rate:Q", format=".1%"),
                alt.Tooltip("p_value:Q", format=".3f"),
            ],
        )
    )
    rule = alt.Chart(sw).mark_rule(strokeDash=[4, 4]).encode(y="mean(baseline):Q")
    st.altair_chart(band + line + rule, width="stretch")
    st.caption(
        "Shaded band = 95% confidence interval. Dashed line = baseline. Watch n shrink as the threshold rises."
    )

if not upcoming.empty:
    st.subheader("Applies to upcoming bouts")
    st.dataframe(
        upcoming[["event_name", "event_date", "fighter_name", "opp_name"]],
        hide_index=True,
        width="stretch",
    )

with st.expander(f"Matching bouts ({len(r.bout_ids):,})"):
    if r.bout_ids:
        ids = r.bout_ids[:2000]
        feats = list(dict.fromkeys(c.feature for c in h.condition))
        sel = ", ".join(f'"{f}"' for f in feats)
        df = data.query(
            f"""SELECT event_date, event_name, fighter_name, opp_name, result, method, {sel}
                FROM bout_sides WHERE bout_id IN (SELECT unnest(?)) ORDER BY event_date DESC""",
            [ids],
        )
        st.dataframe(df, hide_index=True, width="stretch")

st.subheader("Save it")
st.caption(
    "Copy this into a new file under `hypotheses/` and commit it. Saved hypotheses are checked against every upcoming bout."
)
st.code(
    schema.to_yaml(h.model_copy(update={"id": "my-hypothesis", "title": "Describe it here"})),
    language="yaml",
)

with st.expander("Divisional context"):
    ctx = data.query(
        """SELECT division, count(*) / 2 AS bouts, avg(won_ko) * 2 AS ko_rate, avg(won_sub) * 2 AS sub_rate,
                  avg(went_distance) AS decision_rate
           FROM bout_sides WHERE status = 'completed' AND event_date >= ? GROUP BY 1 ORDER BY bouts DESC""",
        [h.population.date_from or dt.date(1993, 1, 1)],
    )
    st.dataframe(
        ctx.style.format({"ko_rate": "{:.0%}", "sub_rate": "{:.0%}", "decision_rate": "{:.0%}"}),
        hide_index=True,
    )
