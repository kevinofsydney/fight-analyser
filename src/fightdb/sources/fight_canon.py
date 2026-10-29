"""fight-canon-db (`canon.db`, SQLite) -> source-neutral records. UFC events only.

https://github.com/Jadevit/fight-canon-db. Heights and reaches are stored in inches there.
`fight_participants.corner` is the order UFC Stats lists the fighters (0 = red from 2010-03-21);
we keep it only as A/B order and never use it as a feature.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fightdb.sources import units
from fightdb.sources.models import (
    Bout,
    Event,
    Fighter,
    FighterAlias,
    HistoryBundle,
    OddsLine,
    RoundStats,
)

SOURCE = "fight_canon"

_ROUND_COLS = {
    "kd": "knockdowns",
    "sig_l": "sig_str_land",
    "sig_a": "sig_str_att",
    "tot_l": "total_str_land",
    "tot_a": "total_str_att",
    "td_l": "td_land",
    "td_a": "td_att",
    "sub_att": "sub_att",
    "rev": "reversals",
    "ctrl_s": "ctrl_sec",
    "head_l": "head_land",
    "head_a": "head_att",
    "body_l": "body_land",
    "body_a": "body_att",
    "leg_l": "leg_land",
    "leg_a": "leg_att",
    "dist_l": "dist_land",
    "dist_a": "dist_att",
    "clinch_l": "clinch_land",
    "clinch_a": "clinch_att",
    "ground_l": "ground_land",
    "ground_a": "ground_att",
}


def _id_map(con: sqlite3.Connection, table: str, key: str, prefix: str) -> dict[int, str]:
    rows = con.execute(
        f"SELECT {key}, MIN(source_id) FROM {table} WHERE source = 'ufcstats' GROUP BY {key}"
    ).fetchall()
    out = {int(k): str(v) for k, v in rows}
    return _DefaultIds(out, prefix)


class _DefaultIds(dict[int, str]):
    def __init__(self, data: dict[int, str], prefix: str):
        super().__init__(data)
        self.prefix = prefix

    def __missing__(self, key: int) -> str:
        return f"{self.prefix}:{key}"


def load(path: Path, source_version: str | None = None, since: str | None = None) -> HistoryBundle:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return _load(con, source_version, since)
    finally:
        con.close()


def _load(con: sqlite3.Connection, source_version: str | None, since: str | None) -> HistoryBundle:
    ev_ids = _id_map(con, "event_aliases", "event_id", "fc-event")
    fight_ids = _id_map(con, "fight_aliases", "fight_id", "fc-bout")
    fighter_ids = _id_map(con, "fighter_aliases", "fighter_id", "fc-fighter")

    since_sql = "AND e.date >= ?" if since else ""
    params: list[str] = [since] if since else []

    con.execute("DROP TABLE IF EXISTS temp.ufc_fights")
    con.execute(
        f"""CREATE TEMP TABLE ufc_fights AS
            SELECT f.fight_id FROM fights f JOIN events e USING (event_id)
            WHERE e.promotion = 'UFC' {since_sql}""",
        params,
    )

    events = [
        Event(
            event_id=ev_ids[eid],
            name=name,
            date=units.parse_date(d),
            location=loc,
            status="completed",
            source=SOURCE,
        )
        for eid, name, d, loc in con.execute(
            f"""SELECT event_id, name, date, location FROM events e
                WHERE promotion = 'UFC' {since_sql}""",
            params,
        )
    ]

    parts: dict[int, dict[int, tuple[int, str | None]]] = {}
    for fight_id, corner, fighter_id, result in con.execute(
        """SELECT fight_id, corner, fighter_id, result FROM fight_participants
           WHERE fight_id IN (SELECT fight_id FROM temp.ufc_fights)"""
    ):
        parts.setdefault(fight_id, {})[corner] = (fighter_id, result)

    bouts: list[Bout] = []
    for row in con.execute(
        """SELECT fight_id, event_id, weight_class, title_fight, scheduled_rounds, method,
                  end_round, end_time, time_format, referee, details, no_contest
           FROM fights WHERE fight_id IN (SELECT fight_id FROM temp.ufc_fights)"""
    ):
        (
            fight_id,
            event_id,
            wc,
            _title,
            sched,
            method,
            end_round,
            end_time,
            tf,
            ref,
            details,
            nc,
        ) = row
        p = parts.get(fight_id, {})
        if 0 not in p or 1 not in p:
            continue
        a_id, a_res = p[0]
        b_id = p[1][0]
        division, sex = units.division_from_weight_class(wc)
        end_time_s = units.parse_mmss(end_time)
        result_a = "NC" if nc else (a_res if a_res in ("W", "L", "D", "NC") else None)
        winner = None
        if result_a == "W":
            winner = fighter_ids[a_id]
        elif result_a == "L":
            winner = fighter_ids[b_id]
        mg = "nc" if result_a == "NC" else units.method_group(method)
        bouts.append(
            Bout(
                bout_id=fight_ids[fight_id],
                event_id=ev_ids[event_id],
                weight_class=wc,
                division=division,
                sex=sex,  # type: ignore[arg-type]
                is_title=units.is_title_bout(wc),
                scheduled_rounds=sched or units.scheduled_rounds(tf),
                fighter_a_id=fighter_ids[a_id],
                fighter_b_id=fighter_ids[b_id],
                status="completed",
                result_a=result_a,  # type: ignore[arg-type]
                winner_id=winner,
                method=units.null_if_dash(method),
                method_group=mg,
                details=units.null_if_dash(details),
                end_round=end_round,
                end_time_s=end_time_s,
                fight_seconds=units.fight_seconds(end_round, end_time_s, tf),
                time_format=units.null_if_dash(tf),
                referee=units.null_if_dash(ref),
                source=SOURCE,
            )
        )

    fighter_set = {fid for p in parts.values() for fid, _ in p.values()}
    fighters: list[Fighter] = []
    for row in con.execute(
        """SELECT fighter_id, name, nickname, dob, height_in, reach_in, stance, sex,
                  wins, losses, draws, nationality FROM fighters"""
    ):
        fid = row[0]
        if fid not in fighter_set:
            continue
        sex = row[7] if row[7] in ("M", "F") else None
        fighters.append(
            Fighter(
                fighter_id=fighter_ids[fid],
                name=row[1],
                nickname=units.null_if_dash(row[2]),
                dob=units.parse_date(row[3]),
                height_cm=units.inches_to_cm(row[4]),
                reach_cm=units.inches_to_cm(row[5]),
                stance=units.null_if_dash(row[6]),
                sex=sex,
                pro_wins=row[8],
                pro_losses=row[9],
                pro_draws=row[10],
                nationality=units.null_if_dash(row[11]),
                source=SOURCE,
            )
        )

    aliases = [
        FighterAlias(source="fight_canon", source_id=str(fid), fighter_id=fighter_ids[fid])
        for fid in sorted(fighter_set)
    ]
    aliases += [
        FighterAlias(source="ufcstats", source_id=fighter_ids[fid], fighter_id=fighter_ids[fid])
        for fid in sorted(fighter_set)
        if not fighter_ids[fid].startswith("fc-")
    ]

    cols = ", ".join(_ROUND_COLS.values())
    rounds = [
        RoundStats(
            bout_id=fight_ids[r[0]],
            fighter_id=fighter_ids[r[1]],
            round=r[2],
            **dict(zip(_ROUND_COLS.keys(), r[3:], strict=True)),
        )
        for r in con.execute(
            f"""SELECT fight_id, fighter_id, round, {cols} FROM round_stats
                WHERE source = 'ufcstats' AND fight_id IN (SELECT fight_id FROM temp.ufc_fights)"""
        )
    ]

    odds = _odds(con, fight_ids, fighter_ids)
    return HistoryBundle(
        source=SOURCE,
        source_version=source_version,
        events=events,
        fighters=fighters,
        aliases=aliases,
        bouts=bouts,
        rounds=rounds,
        odds=odds,
    )


def _odds(
    con: sqlite3.Connection, fight_ids: dict[int, str], fighter_ids: dict[int, str]
) -> list[OddsLine]:
    raw: dict[int, list[tuple[int, int | None, int | None, int | None]]] = {}
    for fight_id, fighter_id, open_, lo, hi in con.execute(
        """SELECT fight_id, fighter_id, open, close_low, close_high FROM odds
           WHERE book = 'all' AND fight_id IN (SELECT fight_id FROM temp.ufc_fights)"""
    ):
        raw.setdefault(fight_id, []).append((fighter_id, open_, lo, hi))
    out: list[OddsLine] = []
    for fight_id, sides in raw.items():
        probs = []
        for _, _, lo, hi in sides:
            ps = [
                p for p in (units.american_to_prob(lo), units.american_to_prob(hi)) if p is not None
            ]
            probs.append(sum(ps) / len(ps) if ps else None)
        total = (
            sum(p for p in probs if p is not None) if all(p is not None for p in probs) else None
        )
        for (fighter_id, open_, lo, hi), p in zip(sides, probs, strict=True):
            vig_free = p / total if (p is not None and total and len(sides) == 2) else None
            out.append(
                OddsLine(
                    bout_id=fight_ids[fight_id],
                    fighter_id=fighter_ids[fighter_id],
                    open_american=open_,
                    close_low=lo,
                    close_high=hi,
                    implied_prob_close=round(vig_free, 4) if vig_free is not None else None,
                )
            )
    return out
