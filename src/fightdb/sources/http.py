"""The only module (with scripts/make_fixtures.py) that talks to the network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from fightdb.config import get_settings

_RETRY = retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
)


def make_client(
    transport: httpx.BaseTransport | None = None, timeout: float = 120.0
) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": get_settings().http_user_agent},
        follow_redirects=True,
        timeout=timeout,
        transport=transport,
    )


@_RETRY
def get_json(client: httpx.Client, url: str, params: dict[str, str] | None = None) -> Any:
    resp = client.get(url, params=params)
    resp.raise_for_status()
    return resp.json()


@_RETRY
def download(client: httpx.Client, url: str, dest: Path) -> dict[str, str | None]:
    """Download `url` to `dest`, skipping the transfer when the server says it is unchanged.

    Returns {"etag", "last_modified", "final_url", "changed"}. A sidecar `<dest>.meta.json`
    remembers the ETag between runs.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    meta_path = dest.with_suffix(dest.suffix + ".meta.json")
    meta: dict[str, str | None] = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    headers = {}
    if dest.exists() and meta.get("etag"):
        headers["If-None-Match"] = str(meta["etag"])

    tmp = dest.with_suffix(dest.suffix + ".part")
    with client.stream("GET", url, headers=headers) as resp:
        if resp.status_code == 304:
            return {**meta, "changed": "false"}
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes(1 << 20):
                fh.write(chunk)
        meta = {
            "etag": resp.headers.get("etag"),
            "last_modified": resp.headers.get("last-modified"),
            "final_url": str(resp.url),
        }
    tmp.replace(dest)
    meta_path.write_text(json.dumps(meta))
    return {**meta, "changed": "true"}


def latest_release_tag(client: httpx.Client, repo: str) -> str | None:
    """Resolve the latest release tag from the redirect target (no API token needed)."""
    resp = client.head(f"https://github.com/{repo}/releases/latest", follow_redirects=False)
    loc = resp.headers.get("location", "")
    return loc.rsplit("/tag/", 1)[-1] if "/tag/" in loc else None


def release_asset_url(repo: str, asset: str, tag: str | None = None) -> str:
    if tag:
        return f"https://github.com/{repo}/releases/download/{tag}/{asset}"
    return f"https://github.com/{repo}/releases/latest/download/{asset}"
