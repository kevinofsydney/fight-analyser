"""Greco1899/scrape_ufc_stats CSVs -> source-neutral records (fallback history source).

https://github.com/Greco1899/scrape_ufc_stats (GPL-3.0 code; we only read its data files).
IDs come from the UFC Stats URLs, so they match fight-canon-db's `ufcstats` aliases.
`ufc_fight_stats.csv` identifies fighters by name, so names are resolved within each bout.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from fightdb.sources import units
from fightdb.sources.models import (
    Bout,
    Event,
    Fighter,
    FighterAlias,
    HistoryBundle,
    RoundStats,
)

SOURCE = "greco"


def _url_id(url: str | None) -> str | None:
    m = re.search(r"/(?:event|fight|fighter)-details/([0-9a-f]+)", url or "")
    return m[1] if m else None


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


def _rows(directory: Path, name: str) -> list[dict[str, str]]:
    with (directory / name).open(newline="", encoding="utf-8") as fh:
        return [
            {k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)
        ]


def _split_bout(bout: str) -> tuple[str, str]:
    a, _, b = bout.partition(" vs. ")
    return a.strip(), b.strip()


def load(
    directory: Path, source_version: str | None = None, since: str | None = None
) -> HistoryBundle:
    events_raw = _rows(directory, "ufc_event_details.csv")
    details = _rows(directory, "ufc_fight_details.csv")
    results = _rows(directory, "ufc_fight_results.csv")
    stats = _rows(directory, "ufc_fight_stats.csv")
    fighter_details = _rows(directory, "ufc_fighter_details.csv")
    tott = _rows(directory, "ufc_fighter_tott.csv")

    since_d = units.parse_date(since) if since else None
    events: dict[str, Event] = {}
    event_by_name: dict[str, str] = {}
    for r in events_raw:
        eid = _url_id(r["URL"])
        d = units.parse_date(r["DATE"])
        if not eid or (since_d and d and d < since_d):
            continue
        events[eid] = Event(
            event_id=eid,
            name=r["EVENT"],
            date=d,
            location=r["LOCATION"] or None,
            status="completed",
            source=SOURCE,
        )
        event_by_name[r["EVENT"]] = eid

    # Fighter identity: name -> candidate IDs (names are not unique on UFC Stats).
    by_name: dict[str, list[str]] = defaultdict(list)
    names: dict[str, str] = {}
    nicknames: dict[str, str | None] = {}
    for r in fighter_details:
        fid = _url_id(r["URL"])
        if not fid:
            continue
        full = f"{r['FIRST']} {r['LAST']}".strip()
        names[fid] = full
        nicknames[fid] = r["NICKNAME"] or None
        by_name[_norm(full)].append(fid)
    tott_by_id = {_url_id(r["URL"]): r for r in tott}
    for fid, r in tott_by_id.items():
        if fid and r.get("FIGHTER") and fid not in by_name[_norm(r["FIGHTER"])]:
            by_name[_norm(r["FIGHTER"])].append(fid)
            names.setdefault(fid, r["FIGHTER"])

    bout_url: dict[tuple[str, str], str] = {}
    for r in details:
        bid = _url_id(r["URL"])
        if bid:
            bout_url[(r["EVENT"], r["BOUT"])] = bid

    # Fighter IDs per bout, resolved once from the stats rows (which carry names only).
    used_ids: set[str] = set()

    def resolve(name: str, weight_hint: str | None) -> str:
        cands = by_name.get(_norm(name), [])
        if len(cands) == 1:
            return cands[0]
        if len(cands) > 1 and weight_hint:
            target = _weight_lbs(weight_hint)
            if target:
                scored = sorted(
                    cands,
                    key=lambda c: abs(
                        (_weight_lbs(tott_by_id.get(c, {}).get("WEIGHT")) or 999) - target
                    ),
                )
                return scored[0]
        if cands:
            return cands[0]
        return f"greco-fighter:{_norm(name)}"

    bouts: list[Bout] = []
    bout_fighters: dict[str, tuple[str, str]] = {}
    for r in results:
        eid = event_by_name.get(r["EVENT"])
        bid = _url_id(r["URL"]) or bout_url.get((r["EVENT"], r["BOUT"]))
        if not eid or not bid:
            continue
        a_name, b_name = _split_bout(r["BOUT"])
        wc = r["WEIGHTCLASS"]
        a_id, b_id = resolve(a_name, wc), resolve(b_name, wc)
        used_ids.update((a_id, b_id))
        bout_fighters[bid] = (a_id, b_id)
        outcome = r["OUTCOME"].split("/")[0].strip().upper()
        result_a = {"W": "W", "L": "L", "D": "D", "NC": "NC"}.get(outcome)
        division, sex = units.division_from_weight_class(wc)
        end_round = units.parse_int(r["ROUND"])
        end_time_s = units.parse_mmss(r["TIME"])
        tf = r["TIME FORMAT"] or None
        method = units.null_if_dash(r["METHOD"])
        bouts.append(
            Bout(
                bout_id=bid,
                event_id=eid,
                weight_class=wc or None,
                division=division,
                sex=sex,  # type: ignore[arg-type]
                is_title=units.is_title_bout(wc),
                scheduled_rounds=units.scheduled_rounds(tf),
                fighter_a_id=a_id,
                fighter_b_id=b_id,
                status="completed",
                result_a=result_a,  # type: ignore[arg-type]
                winner_id=a_id if result_a == "W" else b_id if result_a == "L" else None,
                method=method,
                method_group="nc" if result_a == "NC" else units.method_group(method),
                details=units.null_if_dash(r["DETAILS"]),
                end_round=end_round,
                end_time_s=end_time_s,
                fight_seconds=units.fight_seconds(end_round, end_time_s, tf),
                time_format=tf,
                referee=units.null_if_dash(r["REFEREE"]),
                source=SOURCE,
            )
        )

    rounds: list[RoundStats] = []
    for r in stats:
        bid = bout_url.get((r["EVENT"], r["BOUT"]))
        if not bid or bid not in bout_fighters:
            continue
        rnd = units.parse_int(r["ROUND"].replace("Round", ""))
        if rnd is None:
            continue
        a_id, b_id = bout_fighters[bid]
        a_name, b_name = _split_bout(r["BOUT"])
        n = _norm(r["FIGHTER"])
        fid = a_id if n == _norm(a_name) else b_id if n == _norm(b_name) else None
        if fid is None:
            continue
        sig = units.parse_x_of_y(r["SIG.STR."])
        tot = units.parse_x_of_y(r["TOTAL STR."])
        td = units.parse_x_of_y(r["TD"])
        head = units.parse_x_of_y(r["HEAD"])
        body = units.parse_x_of_y(r["BODY"])
        leg = units.parse_x_of_y(r["LEG"])
        dist = units.parse_x_of_y(r["DISTANCE"])
        clinch = units.parse_x_of_y(r["CLINCH"])
        ground = units.parse_x_of_y(r["GROUND"])
        rounds.append(
            RoundStats(
                bout_id=bid,
                fighter_id=fid,
                round=rnd,
                kd=units.parse_int(r["KD"]),
                sig_l=sig[0],
                sig_a=sig[1],
                tot_l=tot[0],
                tot_a=tot[1],
                td_l=td[0],
                td_a=td[1],
                sub_att=units.parse_int(r["SUB.ATT"]),
                rev=units.parse_int(r["REV."]),
                ctrl_s=units.parse_mmss(r["CTRL"]),
                head_l=head[0],
                head_a=head[1],
                body_l=body[0],
                body_a=body[1],
                leg_l=leg[0],
                leg_a=leg[1],
                dist_l=dist[0],
                dist_a=dist[1],
                clinch_l=clinch[0],
                clinch_a=clinch[1],
                ground_l=ground[0],
                ground_a=ground[1],
            )
        )

    fighters: list[Fighter] = []
    for fid in sorted(used_ids):
        t = tott_by_id.get(fid, {})
        fighters.append(
            Fighter(
                fighter_id=fid,
                name=names.get(fid) or t.get("FIGHTER") or fid,
                nickname=nicknames.get(fid),
                dob=units.parse_date(t.get("DOB")),
                height_cm=units.parse_height(t.get("HEIGHT")),
                reach_cm=units.parse_reach(t.get("REACH")),
                stance=units.null_if_dash(t.get("STANCE")),
                source=SOURCE,
            )
        )
    aliases = [
        FighterAlias(source="ufcstats", source_id=fid, fighter_id=fid)
        for fid in sorted(used_ids)
        if not fid.startswith("greco-")
    ]
    return HistoryBundle(
        source=SOURCE,
        source_version=source_version,
        events=list(events.values()),
        fighters=fighters,
        aliases=aliases,
        bouts=bouts,
        rounds=rounds,
    )


def _weight_lbs(value: str | None) -> int | None:
    """'155 lbs.' -> 155; 'Lightweight Bout' -> 155."""
    if not value:
        return None
    m = re.match(r"(\d+)\s*lbs", value)
    if m:
        return int(m[1])
    division, _ = units.division_from_weight_class(value)
    return _DIVISION_LBS.get((division or "").removeprefix("w_"))


_DIVISION_LBS = {
    "strawweight": 115,
    "flyweight": 125,
    "bantamweight": 135,
    "featherweight": 145,
    "lightweight": 155,
    "welterweight": 170,
    "middleweight": 185,
    "light_heavyweight": 205,
    "heavyweight": 265,
}
