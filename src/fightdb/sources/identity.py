"""Match fighters from ESPN (upcoming cards) to our fighter IDs.

Order: saved alias -> manual override -> exact normalised name (disambiguated by division and
record) -> fuzzy name (rapidfuzz >= threshold, checked against the pro record).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import duckdb
import yaml
from rapidfuzz import fuzz, process

FUZZY_THRESHOLD = 90


@dataclass(frozen=True)
class Match:
    fighter_id: str | None
    method: str  # 'alias' | 'override' | 'exact' | 'fuzzy' | 'none'
    score: float = 100.0


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
    """`fighter_identity.yaml`: {espn: {<espn_id>: <fighter_id>}}."""
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): str(v) for k, v in (data.get("espn") or {}).items()}


class FighterMatcher:
    def __init__(self, con: duckdb.DuckDBPyConnection, overrides: dict[str, str] | None = None):
        self.overrides = overrides or {}
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
        self.info = {r[0]: r for r in rows}
        self.by_name: dict[str, list[str]] = {}
        for fid, name, *_ in rows:
            self.by_name.setdefault(normalise_name(name), []).append(fid)
        self.names = list(self.by_name.keys())

    def match(
        self,
        espn_id: str | None,
        name: str,
        record: str | None = None,
        division: str | None = None,
    ) -> Match:
        """`espn_id` is None for sources without athlete IDs: overrides and aliases are skipped."""
        if espn_id is not None and espn_id in self.overrides:
            return Match(self.overrides[espn_id], "override")
        if espn_id is not None and espn_id in self.aliases:
            return Match(self.aliases[espn_id], "alias")
        key = normalise_name(name)
        if key in self.by_name:
            return Match(self._pick(self.by_name[key], record, division), "exact")
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
        # ESPN may be a fight or two ahead of / behind our source.
        return abs(w - rec[0]) <= 3 and abs(losses - rec[1]) <= 3
