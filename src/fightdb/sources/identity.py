"""Match fighters from ESPN (upcoming cards) to our fighter IDs.

Order: manual override -> saved alias -> exact normalised name (disambiguated by division and
record) -> pro history (a fighter with no UFC bout, see `_match_pro`) -> fuzzy name (rapidfuzz >=
threshold, checked against the pro record).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import duckdb
import yaml
from rapidfuzz import fuzz, process

from fightdb.sources.models import Fighter

FUZZY_THRESHOLD = 90
# ESPN may be a fight or two ahead of / behind our source. Official records of fighters on
# 2024-26 cards are within 3 wins and 3 losses of the fights we hold for 99% of them.
RECORD_TOLERANCE = 3
# A fighter about to debut has fought recently; a namesake who retired years ago has not.
# Replayed on the 1,376 UFC debutants of 2015-2026: with 3 years, 1,364 linked and none wrongly
# (2 years gave two wrong links).
PRO_ACTIVE_YEARS = 3
PRO_ID_PREFIX = "fc-fighter:"


@dataclass(frozen=True)
class Match:
    fighter_id: str | None
    method: str  # 'alias' | 'override' | 'exact' | 'pro' | 'fuzzy' | 'none'
    score: float = 100.0


@dataclass(frozen=True)
class ProCandidate:
    """A fighter from `pro_fights` who shares a name with a scheduled fighter."""

    fighter_id: str
    name: str
    wins: int
    losses: int
    last_fight: date

    def describe(self) -> str:
        record = f"{self.wins}-{self.losses}"
        return f"{self.name} {record}, last fight {self.last_fight}, {self.fighter_id}"


def normalise_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s)
    s = re.sub(r"[^a-z ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def name_key(name: str) -> str:
    """Normalised name with its tokens sorted, so "Yoo Joo-sang" == "Joo-sang Yoo"."""
    return " ".join(sorted(normalise_name(name).replace("-", " ").split()))


def same_matchup(a1: str, b1: str, a2: str, b2: str) -> bool:
    """Do two listings describe the same bout? Exact names in any order, or one fighter the same
    and the other sharing a surname ("Bia Mesquita" vs "Beatriz Mesquita")."""
    x, y = sorted((name_key(a1), name_key(b1))), sorted((name_key(a2), name_key(b2)))
    if x == y:
        return True
    for i in (0, 1):
        for j in (0, 1):
            if x[i] == y[j] and x[i]:
                other_x, other_y = x[1 - i], y[1 - j]
                surname = normalise_name(other_x).split()[-1:] if other_x else []
                if surname and len(surname[0]) >= 3 and surname[0] in other_y.split():
                    return True
    return False


def is_placeholder(name: str) -> bool:
    """ESPN lists unannounced opponents as 'TBA' / 'Opponent TBA'."""
    return bool(re.fullmatch(r"(opponent\s+)?(tba|tbd)", name.strip().lower()))


def _parse_record(record: str | None) -> tuple[int, int] | None:
    m = re.match(r"(\d+)-(\d+)", record or "")
    return (int(m[1]), int(m[2])) if m else None


def load_overrides(path: Path) -> dict[str, str]:
    """`fighter_identity.yaml`: {espn: {<espn_id>: <fighter_id>}}. The fighter ID may be a
    "fc-fighter:<n>" one from `pro_fights`, for a fighter with no UFC bout yet."""
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): str(v) for k, v in (data.get("espn") or {}).items()}


class FighterMatcher:
    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        overrides: dict[str, str] | None = None,
        today: date | None = None,
    ):
        self.con = con
        self.overrides = overrides or {}
        # fight-canon number -> the ID that fighter has now. A debutant linked as "fc-fighter:<n>"
        # gets a UFC Stats ID once they have fought in the UFC.
        self.canon = dict(
            con.execute(
                "SELECT source_id, fighter_id FROM fighter_aliases WHERE source = 'fight_canon'"
            ).fetchall()
        )
        self.aliases = dict(
            con.execute(
                "SELECT source_id, fighter_id FROM fighter_aliases WHERE source = 'espn'"
            ).fetchall()
        )
        rows = con.execute(
            """SELECT f.fighter_id, f.name, f.pro_wins, f.pro_losses,
                      (SELECT b.division FROM bouts b
                       JOIN events e USING (event_id)
                       WHERE (b.fighter_a_id = f.fighter_id OR b.fighter_b_id = f.fighter_id)
                         AND b.status = 'completed'
                       ORDER BY e.date DESC LIMIT 1) AS last_division
               FROM fighters f
               WHERE NOT starts_with(f.fighter_id, 'espn:')"""
        ).fetchall()
        rows = [r for r in rows if self.current_id(r[0]) == r[0]]  # drop superseded pro IDs
        self.info = {r[0]: r for r in rows}
        self.by_name: dict[str, list[str]] = {}
        for fid, name, *_ in rows:
            self.by_name.setdefault(normalise_name(name), []).append(fid)
        self.names = list(self.by_name.keys())
        self.pro = self._load_pro(today or date.today())

    def current_id(self, fighter_id: str) -> str:
        if fighter_id.startswith(PRO_ID_PREFIX):
            return str(self.canon.get(fighter_id.removeprefix(PRO_ID_PREFIX), fighter_id))
        return fighter_id

    def _load_pro(self, today: date) -> dict[str, dict[str, ProCandidate]]:
        """Recently active fighters from `pro_fights`, by name key (every spelling)."""
        rows = self.con.execute(
            """WITH sides AS (
                   SELECT fighter_a_id AS fighter_id, date, result_a = 'W' AS won,
                          result_a = 'L' AS lost FROM pro_fights
                   UNION ALL
                   SELECT fighter_b_id, date, result_a = 'L', result_a = 'W' FROM pro_fights),
               rec AS (
                   SELECT fighter_id, count(*) FILTER (won) AS wins,
                          count(*) FILTER (lost) AS losses, max(date) AS last_fight
                   FROM sides WHERE date < ? GROUP BY fighter_id HAVING max(date) >= ?)
               SELECT n.fighter_id, n.name, r.wins, r.losses, r.last_fight
               FROM pro_fighters n JOIN rec r USING (fighter_id)
               ORDER BY n.fighter_id, n.name""",
            [today, today - timedelta(days=round(365.25 * PRO_ACTIVE_YEARS))],
        ).fetchall()
        out: dict[str, dict[str, ProCandidate]] = {}
        for fid, name, wins, losses, last in rows:
            key = name_key(name)
            if key:
                out.setdefault(key, {}).setdefault(fid, ProCandidate(fid, name, wins, losses, last))
        return out

    def pro_candidates(self, name: str) -> list[ProCandidate]:
        return list(self.pro.get(name_key(name), {}).values())

    def _match_pro(self, name: str, record: str | None) -> str | None:
        """Link to earlier pro fights: same name, fought recently, record agrees with ESPN's.
        Anything less certain (two plausible namesakes, no record to tell them apart) is left for
        a manual override."""
        cands = self.pro_candidates(name)
        rec = _parse_record(record)
        if rec:
            cands = [
                c
                for c in cands
                if abs(c.wins - rec[0]) <= RECORD_TOLERANCE
                and abs(c.losses - rec[1]) <= RECORD_TOLERANCE
            ]
        return cands[0].fighter_id if len(cands) == 1 else None

    def pro_fighter(self, fighter_id: str, name: str, sex: str | None) -> Fighter | None:
        """A `fighters` row for someone known only from `pro_fights`, under the listed name."""
        row = self.con.execute(
            """SELECT dob, height_cm, reach_cm, stance, sex, nationality FROM pro_fighters
               WHERE fighter_id = ? ORDER BY name LIMIT 1""",
            [fighter_id],
        ).fetchone()
        if row is None:
            return None
        cand = next((c for c in self.pro_candidates(name) if c.fighter_id == fighter_id), None)
        return Fighter(
            fighter_id=fighter_id,
            name=name,
            dob=row[0],
            height_cm=row[1],
            reach_cm=row[2],
            stance=row[3],
            sex=row[4] or sex,  # type: ignore[arg-type]
            nationality=row[5],
            pro_wins=cand.wins if cand else None,
            pro_losses=cand.losses if cand else None,
            source="fight_canon",
        )

    def match(
        self,
        espn_id: str | None,
        name: str,
        record: str | None = None,
        division: str | None = None,
    ) -> Match:
        """`espn_id` is None for sources without athlete IDs: overrides and aliases are skipped."""
        if espn_id is not None and espn_id in self.overrides:
            return Match(self.current_id(self.overrides[espn_id]), "override")
        if espn_id is not None and espn_id in self.aliases:
            return Match(self.current_id(self.aliases[espn_id]), "alias")
        key = normalise_name(name)
        if key in self.by_name:
            return Match(self._pick(self.by_name[key], record, division), "exact")
        pro = self._match_pro(name, record)
        if pro is not None:
            return Match(pro, "pro")
        if not self.names:
            return Match(None, "none", 0.0)
        best = process.extractOne(key, self.names, scorer=fuzz.token_sort_ratio)
        if best and best[1] >= FUZZY_THRESHOLD:
            fid = self._pick(self.by_name[best[0]], record, division)
            if self._record_ok(fid, record):
                return Match(fid, "fuzzy", float(best[1]))
        return Match(None, "none", float(best[1]) if best else 0.0)

    def _pick(self, cands: list[str], record: str | None, division: str | None) -> str:
        if len(cands) == 1:
            return cands[0]
        rec = _parse_record(record)

        def score(fid: str) -> tuple[int, int]:
            _, _, w, losses, last_div = self.info[fid]
            div_ok = int(division is not None and last_div == division)
            rec_gap = abs((w or 0) - rec[0]) + abs((losses or 0) - rec[1]) if rec else 0
            return (-div_ok, rec_gap)

        return sorted(cands, key=score)[0]

    def _record_ok(self, fid: str, record: str | None) -> bool:
        rec = _parse_record(record)
        _, _, w, losses, _ = self.info[fid]
        if not rec or w is None or losses is None:
            return True
        return abs(w - rec[0]) <= RECORD_TOLERANCE and abs(losses - rec[1]) <= RECORD_TOLERANCE
