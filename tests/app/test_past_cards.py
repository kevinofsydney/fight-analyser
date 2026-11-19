import math
from datetime import date

import pandas as pd

from fightdb.app import past_cards


def _bout(bout_id: str, event: str, p: float, won: int, lost: int, **kw: object) -> dict:
    row = {
        "bout_id": bout_id,
        "event_id": event,
        "event_name": f"Event {event}",
        "event_date": date(2026, 11, 14) if event == "e2" else date(2026, 11, 7),
        "card_order": 1,
        "scheduled_rounds": 3,
        "weight_class": "Lightweight",
        "is_title": False,
        "fighter_id": f"{bout_id}a",
        "fighter_name": f"A{bout_id}",
        "opp_id": f"{bout_id}b",
        "opp_name": f"B{bout_id}",
        "won": won,
        "lost": lost,
        "draw": int(not won and not lost),
        "no_contest": 0,
        "method": "KO/TKO",
        "method_group": "ko_tko",
        "end_round": 1,
        "p": p,
        "a_ko": 0.3,
        "a_sub": 0.1,
        "a_dec": 0.2,
        "b_ko": 0.1,
        "b_sub": 0.1,
        "b_dec": 0.2,
        "market_p": None,
    }
    row.update(kw)
    return row


def test_score_marks_picks_and_skips_draws():
    df = pd.DataFrame(
        [
            _bout("1", "e2", 0.7, 1, 0, market_p=0.6),  # both right, KO ending right
            _bout("2", "e2", 0.4, 1, 0, market_p=0.55),  # model wrong, market right
            _bout("3", "e2", 0.6, 0, 0),  # draw: not counted
            _bout("4", "e1", 0.8, 0, 1, method_group="dec"),  # model wrong, no price
        ]
    )
    s = past_cards.score(df)
    assert list(s["model_right"].iloc[[0, 1, 3]]) == [1.0, 0.0, 0.0]
    assert math.isnan(s["model_right"].iloc[2])
    assert list(s["market_right"].iloc[:2]) == [1.0, 1.0]
    assert math.isnan(s["market_right"].iloc[3])
    assert s["winner"].iloc[1] == "A2" and s["winner"].iloc[3] == "B4"
    assert s["p_winner"].iloc[1] == 0.4
    assert abs(s["p_winner"].iloc[3] - 0.2) < 1e-12
    assert s["model_ending"].iloc[0] == "A1 by KO/TKO"
    assert s["ending_right"].iloc[0] == 1.0 and s["ending_right"].iloc[3] == 0.0

    total = past_cards.summary(s)
    assert total["decisive"] == 3 and total["model_right"] == 1
    assert total["with_market"] == 2 and total["market_right"] == 2
    assert abs(total["model_brier"] - ((0.3**2 + 0.6**2 + 0.8**2) / 3)) < 1e-12
    assert abs(total["market_brier"] - ((0.4**2 + 0.45**2) / 2)) < 1e-12

    cards = past_cards.by_card(s)
    assert list(cards["event_id"]) == ["e2", "e1"]
    assert cards.loc[0, "model"] == "1 / 2" and cards.loc[0, "market"] == "2 / 2"
    assert cards.loc[1, "market"] == "—"


def test_score_without_market_column():
    s = past_cards.score(pd.DataFrame([_bout("1", "e1", 0.5, 0, 1)]))
    # 50% counts as a pick for the first-listed fighter.
    assert s["model_pick"].iloc[0] == "A1" and s["model_right"].iloc[0] == 0.0
    assert s["market_pick"].iloc[0] is None
