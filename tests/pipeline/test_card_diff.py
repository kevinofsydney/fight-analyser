from datetime import date

from fightdb.pipeline.card_diff import CardBout, CardEvent, diff_cards

TODAY = date(2026, 10, 29)
E1 = CardEvent("e1", "UFC 400", date(2026, 11, 7))
E2 = CardEvent("e2", "UFC 401", date(2026, 11, 14))


def bout(bid, a, b, event="e1", wc="Lightweight", order=1):
    return CardBout(bid, event, a, b, a.upper(), b.upper(), wc, order)


BASE = [bout("b1", "x", "y", order=0), bout("b2", "p", "q")]


def types(res):
    return sorted(c.change_type for c in res.changes)


def test_no_change():
    res = diff_cards([E1], BASE, [E1], BASE, TODAY)
    assert res.changes == [] and res.cancelled_bout_ids == []


def test_added_and_cancelled():
    res = diff_cards([E1], BASE, [E1], [BASE[0], bout("b3", "m", "n")], TODAY)
    assert types(res) == ["bout_added", "bout_cancelled"]
    assert res.cancelled_bout_ids == ["b2"]


def test_replacement():
    res = diff_cards([E1], BASE, [E1], [BASE[0], bout("b9", "p", "z")], TODAY)
    assert types(res) == ["fighter_replaced"]
    assert res.replaces == {"b9": "b2"}
    assert "Q" in res.changes[0].summary and "Z" in res.changes[0].summary


def test_moved_between_events():
    res = diff_cards([E1, E2], BASE, [E1, E2], [BASE[0], bout("b7", "q", "p", event="e2")], TODAY)
    assert types(res) == ["bout_moved"]


def test_weight_class_and_main_event():
    new = [bout("b1", "x", "y", order=1), bout("b2", "p", "q", wc="Catch Weight", order=0)]
    res = diff_cards([E1], BASE, [E1], new, TODAY)
    assert types(res) == ["card_order_changed", "main_event_changed", "weight_class_changed"]


def test_event_changes():
    moved = CardEvent("e1", "UFC 400", date(2026, 11, 8))
    res = diff_cards([E1], BASE, [moved, E2], BASE, TODAY)
    assert types(res) == ["event_added", "event_date_changed"]
    res = diff_cards([E1], BASE, [], [], TODAY)
    assert types(res) == ["event_cancelled"]
    assert res.cancelled_event_ids == ["e1"]


def test_past_event_disappearing_is_not_a_cancellation():
    past = CardEvent("e0", "UFC 399", date(2026, 10, 1))
    res = diff_cards([past], [bout("b0", "x", "y", event="e0")], [], [], TODAY)
    assert res.changes == []
