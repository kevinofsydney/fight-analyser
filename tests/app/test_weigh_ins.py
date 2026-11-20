from datetime import date

import pandas as pd

from fightdb.app import weigh_ins


def _misses() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "card_date": [date(2021, 5, 1), date(2024, 3, 2), date(2024, 8, 1)],
            "card_name": ["UFC A", "UFC B", "UFC C"],
            "fighter_id": ["f1", "f1", "f1"],
            "bout_id": [None, "b2", "b3"],
            "record_type": ["official_miss", "official_miss", "weight_unavailable"],
            "pounds_over": [None, 2.5, None],
            "notes": ["Cancelled", None, None],
            "source_url": ["https://x.test/a", "https://x.test/b", "https://x.test/c"],
        }
    )


def test_this_bout_labels():
    m = _misses()
    reviewed = date(2024, 11, 1)
    assert weigh_ins.this_bout(m, "f1", "b2", date(2024, 3, 2), reviewed) == "Missed by 2.5 lb"
    assert weigh_ins.this_bout(m, "f2", "b2", date(2024, 3, 2), reviewed) == "No miss recorded"
    # Before the list starts, or after its latest reviewed card: unknown.
    assert weigh_ins.this_bout(m, "f2", "x", date(2017, 6, 1), reviewed) == "—"
    assert weigh_ins.this_bout(m, "f2", "x", date(2025, 1, 1), reviewed) == "—"
    assert weigh_ins.this_bout(m, "f2", "x", date(2024, 3, 2), None) == "—"


def test_earlier_counts_official_misses_strictly_before():
    m = _misses()
    assert weigh_ins.earlier(m, "f1", date(2021, 5, 1)) == "None since 2018"
    assert weigh_ins.earlier(m, "f1", date(2024, 3, 2)) == "1 (last 1 May 2021)"
    assert weigh_ins.earlier(m, "f1", date(2025, 1, 1)) == "2 (last 2 Mar 2024)"


def test_table_newest_first():
    t = weigh_ins.table(_misses())
    assert list(t["Card"]) == ["UFC C", "UFC B", "UFC A"]
    assert list(t["Weigh-in"]) == [
        "Weight-cut trouble (no official weight)",
        "Missed by 2.5 lb",
        "Missed (amount not published)",
    ]
