"""Build the small committed test fixtures from the live sources.

    python scripts/make_fixtures.py [--canon path/to/canon.db] [--greco path/to/csv_dir]

Without paths, downloads the latest fight-canon-db release and Greco1899 CSVs into data/sources/.
Writes tests/fixtures/canon_mini.db, tests/fixtures/greco/*.csv and tests/fixtures/espn/*.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fightdb.config import get_settings
from fightdb.pipeline.backfill import fetch_fight_canon, fetch_greco
from fightdb.sources import espn, http

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures"
TABLES = [
    "events",
    "fights",
    "fight_participants",
    "round_stats",
    "fighters",
    "fighter_aliases",
    "fight_aliases",
    "event_aliases",
    "odds",
]


# Fixed events so tests can assert known values: a modern title card, the earliest era
# (missing stats), a card with a draw and a no contest, and a non-UFC event (must be filtered out).
FIXTURE_EVENTS = [
    "UFC 333: Volkanovski vs. Evloev",
    "UFC 2: No Way Out",
    "UFC Fight Night: Usman vs. Buckley",
    "Inka 62 - Sudamerica En Lo Mas Alto",
]


def pick_events(src: sqlite3.Connection) -> list[int]:
    ids = []
    for name in FIXTURE_EVENTS:
        row = src.execute("SELECT event_id FROM events WHERE name = ?", [name]).fetchone()
        if row is None:
            raise SystemExit(f"fixture event not found in canon.db: {name}")
        ids.append(row[0])
    return ids


def build_canon_mini(canon: Path, dest: Path) -> list[str]:
    dest.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{canon}?mode=ro", uri=True)
    ids = pick_events(src)
    names = [
        r[0]
        for r in src.execute(
            f"SELECT name FROM events WHERE event_id IN ({','.join('?' * len(ids))})", ids
        )
    ]
    out = sqlite3.connect(dest)
    for t in TABLES:
        out.execute(src.execute("SELECT sql FROM sqlite_master WHERE name = ?", [t]).fetchone()[0])
    marks = ",".join("?" * len(ids))
    fights = [
        r[0] for r in src.execute(f"SELECT fight_id FROM fights WHERE event_id IN ({marks})", ids)
    ]
    fmarks = ",".join("?" * len(fights))
    fighters = [
        r[0]
        for r in src.execute(
            f"SELECT DISTINCT fighter_id FROM fight_participants WHERE fight_id IN ({fmarks})",
            fights,
        )
    ]
    pmarks = ",".join("?" * len(fighters))
    selects = {
        "events": (f"event_id IN ({marks})", ids),
        "event_aliases": (f"event_id IN ({marks})", ids),
        "fights": (f"fight_id IN ({fmarks})", fights),
        "fight_participants": (f"fight_id IN ({fmarks})", fights),
        "round_stats": (f"fight_id IN ({fmarks})", fights),
        "fight_aliases": (f"fight_id IN ({fmarks})", fights),
        "odds": (f"fight_id IN ({fmarks})", fights),
        "fighters": (f"fighter_id IN ({pmarks})", fighters),
        "fighter_aliases": (f"fighter_id IN ({pmarks})", fighters),
    }
    for t, (where, params) in selects.items():
        rows = src.execute(f"SELECT * FROM {t} WHERE {where}", params).fetchall()
        if rows:
            out.executemany(f"INSERT INTO {t} VALUES ({','.join('?' * len(rows[0]))})", rows)
    out.commit()
    out.execute("VACUUM")
    out.close()
    return names


def build_greco_mini(greco_dir: Path, dest_dir: Path, event_names: list[str]) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    wanted = {n.strip() for n in event_names}
    keep_fighters: set[str] = set()
    for name in (
        "ufc_event_details.csv",
        "ufc_fight_details.csv",
        "ufc_fight_results.csv",
        "ufc_fight_stats.csv",
    ):
        with (greco_dir / name).open(encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            idx = [h.strip() for h in header].index("EVENT")
            rows = [r for r in reader if r[idx].strip() in wanted]
        if name == "ufc_fight_results.csv":
            for r in rows:
                keep_fighters.update(x.strip() for x in r[1].split(" vs. "))
        with (dest_dir / name).open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)
    for name, key in (("ufc_fighter_details.csv", None), ("ufc_fighter_tott.csv", "FIGHTER")):
        with (greco_dir / name).open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            fields = reader.fieldnames or []
            rows = [
                r
                for r in reader
                if (r[key] if key else f"{r['FIRST']} {r['LAST']}").strip() in keep_fighters
            ]
        with (dest_dir / name).open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--canon", type=Path)
    ap.add_argument("--greco", type=Path)
    ap.add_argument("--skip-espn", action="store_true")
    args = ap.parse_args()
    settings = get_settings()
    with http.make_client() as client:
        canon = args.canon or fetch_fight_canon(client, settings).path
        greco_dir = args.greco or fetch_greco(client, settings).path
        names = build_canon_mini(canon, OUT / "canon_mini.db")
        print("canon_mini events:", names)
        build_greco_mini(greco_dir, OUT / "greco", names)
        if not args.skip_espn:
            today = datetime.now(UTC).date()
            payload = espn.fetch_scoreboard(client, today)
            (OUT / "espn").mkdir(parents=True, exist_ok=True)
            (OUT / "espn" / "scoreboard_range.json").write_text(json.dumps(payload))
            print("espn events:", len(payload.get("events", [])))


if __name__ == "__main__":
    main()
