"""Scheduled fighters with no UFC bout are linked to their earlier pro fights."""

from datetime import date, timedelta

import numpy as np
import pytest

from fightdb.features import snapshots
from fightdb.pipeline import upcoming, upsert
from fightdb.sources import identity
from fightdb.sources.models import (
    Fighter,
    FighterAlias,
    ProFight,
    ProFighter,
    UpcomingBout,
    UpcomingEvent,
    UpcomingFighter,
)
from tests.features.test_snapshots import _history

TODAY = date(2026, 11, 12)


def seed_pro(con, fighter_id, names, results, last=date(2026, 6, 1), **details):
    """One pro fight a month ending at `last`; `results` is e.g. "WWL" (oldest first)."""
    n = len(results)
    upsert.upsert_records(
        con,
        "pro_fights",
        [
            ProFight(
                fight_id=f"{fighter_id}-{i}",
                date=last - timedelta(days=30 * (n - 1 - i)),
                promotion="Regional",
                fighter_a_id=fighter_id,
                fighter_b_id=f"opp-{fighter_id}-{i}",
                result_a=r,
            )
            for i, r in enumerate(results)
        ],
    )
    upsert.upsert_records(
        con,
        "pro_fighters",
        [ProFighter(fighter_id=fighter_id, name=name, **details) for name in names],
    )


def test_unique_recent_namesake_is_linked_under_any_spelling(con):
    seed_pro(con, "fc-fighter:1", ["Ivan Novak", "Iván Nowak"], "WWWWWWW")
    m = identity.FighterMatcher(con, today=TODAY)
    assert m.match("10", "Ivan Novak", "7-0-0") == identity.Match("fc-fighter:1", "pro")
    assert m.match("10", "Novak Ivan").fighter_id == "fc-fighter:1"  # no record on the listing
    assert m.match("10", "Ivan Nowak", "8-0-0").fighter_id == "fc-fighter:1"
    assert m.match("10", "Ivan Novakov", "7-0-0").fighter_id is None  # no fuzzy pro matching


def test_namesakes_are_told_apart_by_record_or_left_unlinked(con):
    seed_pro(con, "fc-fighter:2", ["Joao Silva"], "WWWWWWWWWWLL")
    seed_pro(con, "fc-fighter:3", ["João Silva"], "WWWWLLLLL")
    m = identity.FighterMatcher(con, today=TODAY)
    assert m.match("11", "Joao Silva", "10-2-0").fighter_id == "fc-fighter:2"
    assert m.match("11", "Joao Silva", "4-5-0").fighter_id == "fc-fighter:3"
    assert m.match("11", "Joao Silva").fighter_id is None  # nothing to choose by
    assert m.match("11", "Joao Silva", "7-4-0").fighter_id is None  # close to both
    assert {c.fighter_id for c in m.pro_candidates("Joao Silva")} == {
        "fc-fighter:2",
        "fc-fighter:3",
    }


def test_wrong_record_or_long_inactive_namesake_is_not_linked(con):
    seed_pro(con, "fc-fighter:4", ["Sam Stone"], "WWLL")
    seed_pro(con, "fc-fighter:5", ["Rex Old"], "WWWW", last=date(2021, 1, 1))
    m = identity.FighterMatcher(con, today=TODAY)
    assert m.match("12", "Sam Stone", "15-1-0").fighter_id is None
    assert m.match("13", "Rex Old", "4-0-0").fighter_id is None
    assert m.pro_candidates("Rex Old") == []
    # Fights on or after "today" do not count as history.
    assert identity.FighterMatcher(con, today=date(2021, 1, 1)).match("13", "Rex Old", "3-0-0")


def test_pro_id_is_followed_to_the_ufc_id_after_the_debut(con):
    upsert.upsert_records(
        con,
        "fighters",
        [
            Fighter(fighter_id="fc-fighter:9", name="Dana Debut", source="fight_canon"),
            Fighter(fighter_id="abc123", name="Dana Debut", pro_wins=6, source="fight_canon"),
        ],
    )
    upsert.upsert_records(
        con,
        "fighter_aliases",
        [
            FighterAlias(source="espn", source_id="77", fighter_id="fc-fighter:9", method="pro"),
            FighterAlias(source="fight_canon", source_id="9", fighter_id="abc123"),
        ],
    )
    m = identity.FighterMatcher(con, overrides={"78": "fc-fighter:9", "79": "fc-fighter:50"})
    assert m.match("77", "Dana Debut").fighter_id == "abc123"  # saved alias
    assert m.match("78", "Dana Debut").fighter_id == "abc123"  # override
    assert m.match(None, "Dana Debut").fighter_id == "abc123"  # by name: the old row is ignored
    assert m.match("79", "Someone Else") == identity.Match("fc-fighter:50", "override")


def _card(name="Dana Debut", record="5-1-0"):
    return [
        UpcomingEvent(
            source_event_id="900",
            name="UFC Test Night",
            date=date(2026, 12, 5),
            bouts=[
                UpcomingBout(
                    source_bout_id="901",
                    weight_class="Lightweight",
                    card_order=0,
                    fighter_a=UpcomingFighter(source_fighter_id="77", name=name, record=record),
                    fighter_b=UpcomingFighter(source_fighter_id="1", name="A", record="3-1-0"),
                )
            ],
        )
    ]


def _bout_fighters(con):
    return con.execute(
        "SELECT fighter_a_id, fighter_b_id FROM bouts WHERE bout_id = 'espn:901'"
    ).fetchone()


def test_debutant_on_an_upcoming_card_gets_a_pro_rating(con):
    upsert.upsert_history(con, _history())
    seed_pro(con, "fc-fighter:9", ["Dana Debut"], "WWLWWW", dob=date(1998, 3, 4), sex="M")
    res = upcoming.ingest_upcoming(con, _card(), TODAY)
    assert res.unmatched == []
    assert _bout_fighters(con) == ("fc-fighter:9", "a")
    row = con.execute(
        "SELECT name, dob, source, pro_wins, pro_losses FROM fighters WHERE fighter_id = ?",
        ["fc-fighter:9"],
    ).fetchone()
    assert row == ("Dana Debut", date(1998, 3, 4), "fight_canon", 5, 1)
    alias = con.execute(
        "SELECT fighter_id, method FROM fighter_aliases WHERE source = 'espn' AND source_id = '77'"
    ).fetchone()
    assert alias == ("fc-fighter:9", "pro")

    s, _ = snapshots.build_snapshots(con)
    snap = s.set_index(["bout_id", "fighter_id"]).loc[("espn:901", "fc-fighter:9")]
    assert snap["pro_fights"] == 6
    assert snap["pro_win_pct"] == pytest.approx((5 + 5) / (6 + 10))
    assert not np.isnan(snap["pro_elo"])
    assert snap["ufc_fights"] == 0


def test_linking_an_already_listed_debutant_is_not_a_card_change(con):
    upsert.upsert_history(con, _history())
    upcoming.ingest_upcoming(con, _card(), TODAY)  # first run: no pro history imported yet
    assert _bout_fighters(con) == ("espn:77", "a")
    logged = con.execute("SELECT count(*) FROM card_changes").fetchone()
    seed_pro(con, "fc-fighter:9", ["Dana Debut"], "WWLWWW")
    res = upcoming.ingest_upcoming(con, _card(), TODAY)
    assert _bout_fighters(con) == ("fc-fighter:9", "a")
    assert res.changes == [] and res.unmatched == []
    assert con.execute("SELECT count(*) FROM card_changes").fetchone() == logged


def test_unlinked_namesakes_are_reported_and_an_override_settles_it(con, tmp_path):
    upsert.upsert_history(con, _history())
    seed_pro(con, "fc-fighter:9", ["Dana Debut"], "WWLWWW")
    seed_pro(con, "fc-fighter:8", ["Dana Debut"], "WWWWLW", last=date(2026, 2, 1))
    res = upcoming.ingest_upcoming(con, _card(), TODAY)
    assert _bout_fighters(con) == ("espn:77", "a")
    (line,) = res.unmatched
    assert line.startswith("Dana Debut (UFC Test Night): pro history not linked")
    assert "Dana Debut 5-1, last fight 2026-06-01, fc-fighter:9" in line
    assert "fc-fighter:8" in line

    overrides = tmp_path / "fighter_identity.yaml"
    overrides.write_text('espn:\n  "77": "fc-fighter:9"\n', encoding="utf-8")
    res = upcoming.ingest_upcoming(con, _card(), TODAY, overrides)
    assert res.unmatched == [] and res.changes == []
    assert _bout_fighters(con) == ("fc-fighter:9", "a")
    assert con.execute("SELECT 1 FROM fighters WHERE fighter_id = 'fc-fighter:9'").fetchone()


def test_linked_fighter_with_a_ufc_stats_id_but_no_ufc_bout_gets_a_fighters_row(con):
    # The source can already hold a UFC Stats ID for someone whose first UFC bout is still ahead.
    upsert.upsert_history(con, _history())
    seed_pro(con, "0123abcd", ["Dana Debut"], "WWLWWW", sex="M")
    upcoming.ingest_upcoming(con, _card(), TODAY)
    assert _bout_fighters(con) == ("0123abcd", "a")
    assert con.execute(
        "SELECT name, pro_wins FROM fighters WHERE fighter_id = '0123abcd'"
    ).fetchone() == ("Dana Debut", 5)
