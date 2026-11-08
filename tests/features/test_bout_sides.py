import math

import pytest

from fightdb.features import bout_sides


def _event(con, eid, day, status):
    con.execute(
        "INSERT INTO events (event_id, name, date, status, source) VALUES (?, ?, ?, ?, 'test')",
        [eid, eid, day, status],
    )


def _bout(con, bid, eid, a, b, status, first_seen):
    con.execute(
        """INSERT INTO bouts (bout_id, event_id, fighter_a_id, fighter_b_id, status, source, first_seen_at)
           VALUES (?, ?, ?, ?, ?, 'test', ?)""",
        [bid, eid, a, b, status, first_seen],
    )


@pytest.fixture
def notice(con):
    # Tracking starts 2026-10-30 with X vs Y already on the card. Y drops out on 11-05 and Z
    # replaces them. W is booked on 11-01 against a debutant ESPN can't match yet ("tba:").
    _event(con, "espn-ev", "2026-11-14", "past")
    _bout(con, "espn:1", "espn-ev", "X", "Y", "cancelled", "2026-10-30 12:00")
    _bout(con, "espn:2", "espn-ev", "X", "Z", "past", "2026-11-05 12:00")
    _bout(con, "espn:3", "espn-ev", "W", "tba:3:b", "past", "2026-11-01 12:00")
    _event(con, "espn-next", "2026-12-05", "upcoming")
    _bout(con, "espn:4", "espn-next", "P", "Q", "scheduled", "2026-11-20 12:00")
    # The same night as imported later from the history source (different IDs, one day off).
    _event(con, "hist-ev", "2026-11-15", "completed")
    _bout(con, "h1", "hist-ev", "X", "Z", "completed", "2026-11-16 12:00")
    _bout(con, "h2", "hist-ev", "D", "W", "completed", "2026-11-16 12:00")
    df = bout_sides.days_notice(con)
    return {(r.bout_id, r.fighter_id): r.days_notice for r in df.itertuples()}


def test_replacement_gets_short_notice(notice):
    assert notice[("h1", "Z")] == 9
    assert notice[("espn:4", "P")] == 15


def test_on_card_before_tracking_is_unknown(notice):
    assert math.isnan(notice[("h1", "X")])


def test_debutant_linked_through_opponent(notice):
    assert notice[("h2", "D")] == 13
    assert notice[("h2", "W")] == 13


def test_cancelled_bouts_are_not_sides(notice):
    assert ("espn:1", "X") not in notice
