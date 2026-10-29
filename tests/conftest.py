from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest

from fightdb.db.conn import init_db
from fightdb.pipeline import upsert
from fightdb.sources.models import Bout, Event, Fighter, HistoryBundle, RoundStats

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_error_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep tests from writing into the real data/logs error log."""
    import logging

    from fightdb.config import get_settings
    from fightdb.errorlog import JsonErrorHandler

    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    get_settings.cache_clear()
    yield
    for lg in (logging.getLogger(), logging.getLogger("streamlit.error_util")):
        for h in [h for h in lg.handlers if isinstance(h, JsonErrorHandler)]:
            lg.removeHandler(h)
            h.close()
    get_settings.cache_clear()


@pytest.fixture
def con() -> Iterator[duckdb.DuckDBPyConnection]:
    c = duckdb.connect(":memory:")
    init_db(c)
    yield c
    c.close()


def make_bout(
    bout_id: str,
    event_id: str,
    a: str,
    b: str,
    result_a: str = "W",
    method_group: str = "dec",
    division: str = "lightweight",
    seconds: int = 900,
) -> Bout:
    return Bout(
        bout_id=bout_id,
        event_id=event_id,
        weight_class="Lightweight Bout",
        division=division,
        sex="M",
        scheduled_rounds=3,
        fighter_a_id=a,
        fighter_b_id=b,
        status="completed",
        result_a=result_a,  # type: ignore[arg-type]
        winner_id=a if result_a == "W" else b if result_a == "L" else None,
        method={"dec": "Decision - Unanimous", "ko_tko": "KO/TKO", "sub": "Submission"}.get(
            method_group
        ),
        method_group=method_group,
        end_round=max(1, seconds // 300 + (1 if seconds % 300 else 0)),
        end_time_s=seconds % 300 or 300,
        fight_seconds=seconds,
        time_format="3 Rnd (5-5-5)",
        source="test",
    )


def make_round(bout_id: str, fighter_id: str, rnd: int = 1, **stats: int) -> RoundStats:
    base = dict(
        kd=0, sig_l=10, sig_a=20, tot_l=12, tot_a=24, td_l=1, td_a=2, sub_att=0, rev=0, ctrl_s=30
    )
    base.update(stats)
    return RoundStats(bout_id=bout_id, fighter_id=fighter_id, round=rnd, **base)


def synthetic_history(
    n_bouts: int = 700, seed: int = 7, older_win_prob: float = 0.7
) -> HistoryBundle:
    """Random lightweight bouts where the older fighter wins `older_win_prob` of the time."""
    rng = random.Random(seed)
    fighters = [
        Fighter(
            fighter_id=f"f{i}",
            name=f"Fighter {i}",
            dob=date(1980 + rng.randint(0, 15), 1 + rng.randint(0, 11), 1),
            height_cm=170 + rng.random() * 15,
            reach_cm=170 + rng.random() * 20,
            stance="Orthodox",
            sex="M",
            source="test",
        )
        for i in range(120)
    ]
    dobs = {f.fighter_id: f.dob for f in fighters}
    events, bouts, rounds = [], [], []
    d0 = date(2011, 1, 1)
    for i in range(n_bouts):
        day = d0 + timedelta(days=7 * (i // 6))
        eid = f"e{i // 6}"
        if i % 6 == 0:
            events.append(
                Event(
                    event_id=eid,
                    name=f"Event {i // 6}",
                    date=day,
                    status="completed",
                    source="test",
                )
            )
        a, b = rng.sample(fighters, 2)
        older_is_a = dobs[a.fighter_id] < dobs[b.fighter_id]  # type: ignore[operator]
        older_wins = rng.random() < older_win_prob
        result = "W" if older_is_a == older_wins else "L"
        bid = f"b{i}"
        bouts.append(
            make_bout(
                bid, eid, a.fighter_id, b.fighter_id, result, rng.choice(["dec", "ko_tko", "sub"])
            )
        )
        for fid in (a.fighter_id, b.fighter_id):
            for r in (1, 2, 3):
                rounds.append(
                    make_round(
                        bid,
                        fid,
                        r,
                        sig_l=rng.randint(5, 25),
                        sig_a=40,
                        td_l=rng.randint(0, 2),
                        td_a=3,
                        sub_att=rng.randint(0, 1),
                        kd=int(rng.random() < 0.05),
                    )
                )
    return HistoryBundle(
        source="test", events=events, fighters=fighters, bouts=bouts, rounds=rounds
    )


@pytest.fixture
def synthetic_db(con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    from fightdb.pipeline.build import build_all

    upsert.upsert_history(con, synthetic_history())
    build_all(con, train_models=False)
    return con
