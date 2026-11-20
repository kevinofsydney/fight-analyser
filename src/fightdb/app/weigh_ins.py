"""Weigh-in misses on screen: labels for the Bout page and the Fighter page table."""

from __future__ import annotations

from datetime import date

import pandas as pd

from fightdb.app import components
from fightdb.pipeline.weight_misses import COVERAGE_START

# From scripts/weight_miss_experiment.py (2018+, 249 fights), for the Bout page note.
MISSER_WON = 0.426
MISSER_PRICED = 0.473


def describe(row: pd.Series) -> str:
    lb = row["pounds_over"]
    amount = f" by {lb:g} lb" if pd.notna(lb) else ""
    return {
        "official_miss": f"Missed{amount}" if amount else "Missed (amount not published)",
        "weight_unavailable": "Weight-cut trouble (no official weight)",
        "revised_limit": f"Limit raised before weigh-ins (over the original{amount})",
        "title_eligibility": f"Over the title limit{amount}",
    }.get(str(row["record_type"]), str(row["record_type"]))


def _as_date(d: object) -> date:
    return pd.Timestamp(d).date()  # type: ignore[arg-type]


def this_bout(
    misses: pd.DataFrame,
    fighter_id: str,
    bout_id: str,
    bout_date: object,
    reviewed_through: date | None,
) -> str:
    """The Bout page's weigh-in cell. "No miss recorded" only for cards the hand-kept list has
    reviewed (2018 up to its latest card); otherwise, e.g. before weigh-ins, it's unknown."""
    own = misses[(misses["fighter_id"] == fighter_id) & (misses["bout_id"] == bout_id)]
    if not own.empty:
        return describe(own.iloc[0])
    day = _as_date(bout_date)
    if reviewed_through is not None and COVERAGE_START <= day <= reviewed_through:
        return "No miss recorded"
    return "—"


def official_before(misses: pd.DataFrame, fighter_id: str, before: object) -> pd.DataFrame:
    day = pd.Timestamp(before)
    m = misses[(misses["fighter_id"] == fighter_id) & (misses["record_type"] == "official_miss")]
    return m[pd.to_datetime(m["card_date"]) < day]


def earlier(misses: pd.DataFrame, fighter_id: str, before: object) -> str:
    m = official_before(misses, fighter_id, before)
    if m.empty:
        return "None since 2018"
    last = components.fmt_date(m["card_date"].iloc[-1])
    return f"{len(m)} (last {last})"


def table(misses: pd.DataFrame) -> pd.DataFrame:
    """The Fighter page list, newest first."""
    m = misses.sort_values("card_date", ascending=False)
    return pd.DataFrame(
        {
            "Date": [components.fmt_date(d) for d in m["card_date"]],
            "Card": m["card_name"].to_numpy(),
            "Weigh-in": [describe(r) for _, r in m.iterrows()],
            "Notes": m["notes"].fillna("").to_numpy(),
            "Source": m["source_url"].to_numpy(),
        }
    )
