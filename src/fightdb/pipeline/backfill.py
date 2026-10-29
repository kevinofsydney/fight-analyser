"""Download a history source and import it."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import httpx

from fightdb.config import (
    FIGHT_CANON_ASSET,
    FIGHT_CANON_REPO,
    GRECO_FILES,
    GRECO_RAW_BASE,
    Settings,
)
from fightdb.pipeline import upsert
from fightdb.sources import fight_canon, greco, http
from fightdb.sources.models import HistoryBundle

log = logging.getLogger(__name__)


@dataclass
class Fetched:
    source: str
    path: Path
    version: str | None
    published: date | None


def fetch_fight_canon(client: httpx.Client, settings: Settings) -> Fetched:
    tag = http.latest_release_tag(client, FIGHT_CANON_REPO)
    dest = settings.download_dir / FIGHT_CANON_ASSET
    log.info("downloading fight-canon-db %s", tag or "latest")
    http.download(client, http.release_asset_url(FIGHT_CANON_REPO, FIGHT_CANON_ASSET, tag), dest)
    published = None
    m = re.match(r"db-(\d{4})-(\d{2})-(\d{2})", tag or "")
    if m:
        published = date(int(m[1]), int(m[2]), int(m[3]))
    return Fetched("fight_canon", dest, tag, published)


def fetch_greco(client: httpx.Client, settings: Settings) -> Fetched:
    dest = settings.download_dir / "greco"
    version, published = None, None
    try:
        info = http.get_json(
            client, "https://api.github.com/repos/Greco1899/scrape_ufc_stats/commits/main"
        )
        version = info["sha"][:12]
        published = datetime.fromisoformat(
            info["commit"]["committer"]["date"].replace("Z", "+00:00")
        ).date()
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        log.warning("could not read Greco1899 commit info: %s", exc)
    for name in GRECO_FILES:
        log.info("downloading %s", name)
        http.download(client, f"{GRECO_RAW_BASE}/{name}", dest / name)
    return Fetched("greco", dest, version, published)


def load_bundle(fetched: Fetched, since: str | None = None) -> HistoryBundle:
    if fetched.source == "fight_canon":
        return fight_canon.load(fetched.path, fetched.version, since)
    return greco.load(fetched.path, fetched.version, since)


def is_stale(fetched: Fetched, settings: Settings, today: date | None = None) -> bool:
    today = today or datetime.now(UTC).date()
    return fetched.published is not None and fetched.published < today - timedelta(
        days=settings.history_stale_days
    )


def import_history(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    client: httpx.Client | None = None,
    source: str | None = None,
    file: Path | None = None,
    since: str | None = None,
) -> tuple[HistoryBundle, dict[str, int], list[str]]:
    """Import history from the chosen source, failing over to the other one.

    Returns (bundle, row counts, warnings). Raises if no source could be imported.
    """
    primary = source or settings.history_source
    order = [primary] + [s for s in ("fight_canon", "greco") if s != primary]
    warnings: list[str] = []
    if file is not None:
        fetched = Fetched(primary, file, f"file:{file.name}", None)
        bundle = load_bundle(fetched, since)
        return bundle, upsert.upsert_history(con, bundle), warnings

    own_client = client is None
    client = client or http.make_client()
    try:
        for i, name in enumerate(order):
            try:
                fetched = (
                    fetch_fight_canon(client, settings)
                    if name == "fight_canon"
                    else fetch_greco(client, settings)
                )
                if is_stale(fetched, settings) and i < len(order) - 1:
                    warnings.append(
                        f"{name} is stale (published {fetched.published}); trying fallback"
                    )
                    continue
                bundle = load_bundle(fetched, since)
                if not bundle.bouts:
                    raise ValueError(f"{name} returned no bouts")
                counts = upsert.upsert_history(con, bundle)
                if i > 0:
                    warnings.append(f"used fallback history source {name}")
                return bundle, counts, warnings
            except Exception as exc:  # noqa: BLE001 - any failure moves to the next source
                log.exception("history source %s failed", name)
                warnings.append(f"{name} failed: {exc}")
        raise RuntimeError("; ".join(warnings) or "no history source available")
    finally:
        if own_client:
            client.close()
