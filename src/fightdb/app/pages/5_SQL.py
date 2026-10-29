"""Read-only SQL console against the DuckDB database."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import threading

import duckdb
import streamlit as st

from fightdb.app import components, data

components.page("SQL console", "🧮")
st.title("SQL console")
st.caption(
    "Read-only. Results are capped at 5,000 rows and 20 seconds. `bout_sides` has one row per fighter per bout."
)

ROW_LIMIT = 5000
TIMEOUT_S = 20

default = """-- Win rate of the older fighter by age gap, men's lightweight since 2010
SELECT floor(diff_age_years) AS age_gap, count(*) AS n, avg(won) AS win_rate
FROM completed_bout_sides
WHERE division = 'lightweight' AND event_date >= '2010-01-01'
  AND diff_age_years >= 0 AND draw = 0 AND no_contest = 0
GROUP BY 1 ORDER BY 1"""
sql = st.text_area("Query", default, height=200)

with st.expander("Tables and columns"):
    tables = data.query(
        "SELECT table_name, string_agg(column_name, ', ' ORDER BY ordinal_position) AS columns "
        "FROM information_schema.columns GROUP BY 1 ORDER BY 1"
    )
    st.dataframe(tables, hide_index=True, width="stretch")

if st.button("Run", type="primary"):
    con = data.connect()
    timer = threading.Timer(TIMEOUT_S, con.interrupt)
    timer.start()
    try:
        df = con.execute(f"SELECT * FROM ({sql.rstrip().rstrip(';')}) LIMIT {ROW_LIMIT}").df()
        st.dataframe(df, hide_index=True, width="stretch")
        st.caption(f"{len(df):,} rows")
        st.download_button("Download CSV", df.to_csv(index=False), "query.csv", "text/csv")
    except duckdb.Error as exc:
        st.error(str(exc))
    finally:
        timer.cancel()
        con.close()
