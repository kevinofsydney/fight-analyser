from fightdb.pipeline import upsert
from fightdb.sources import identity
from fightdb.sources.models import Fighter, FighterAlias


def _seed(con):
    upsert.upsert_records(
        con,
        "fighters",
        [
            Fighter(fighter_id="a1", name="José Aldo", pro_wins=32, pro_losses=8, source="t"),
            Fighter(fighter_id="b1", name="Bruno Silva", pro_wins=23, pro_losses=7, source="t"),
            Fighter(fighter_id="b2", name="Bruno Silva", pro_wins=13, pro_losses=6, source="t"),
            Fighter(fighter_id="c1", name="Khamzat Chimaev", pro_wins=15, pro_losses=0, source="t"),
        ],
    )
    upsert.upsert_records(
        con, "fighter_aliases", [FighterAlias(source="espn", source_id="999", fighter_id="c1")]
    )


def test_normalise_and_placeholder():
    assert identity.normalise_name("José Aldo Jr.") == "jose aldo"
    assert identity.is_placeholder("Opponent TBA")
    assert identity.is_placeholder("TBA")
    assert not identity.is_placeholder("Tabatha Ricci")


def test_matching_order(con):
    _seed(con)
    m = identity.FighterMatcher(con, overrides={"555": "b2"})
    assert m.match("999", "Whoever").fighter_id == "c1"
    assert m.match("555", "Bruno Silva").method == "override"
    assert m.match("1", "Jose Aldo").fighter_id == "a1"
    # Duplicate names are disambiguated by record
    assert m.match("2", "Bruno Silva", record="13-6-0").fighter_id == "b2"
    assert m.match("3", "Bruno Silva", record="23-7-0").fighter_id == "b1"
    fuzzy = m.match("4", "Khamzat Chimaiev", record="15-0-0")
    assert fuzzy.method in {"fuzzy", "alias"} and fuzzy.fighter_id == "c1"
    assert m.match("5", "Totally New Debutant").fighter_id is None


def test_fuzzy_rejects_wrong_record(con):
    _seed(con)
    m = identity.FighterMatcher(con)
    assert m.match("6", "Jose Aldoo", record="2-0-0").fighter_id is None
