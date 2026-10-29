"""Optional card-change notifications. Only active when DISCORD_WEBHOOK_URL is set."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)
DISCORD_LIMIT = 1900


def notify_card_changes(webhook_url: str, changes: list[str]) -> bool:
    if not webhook_url or not changes:
        return False
    lines = ["**UFC card changes**"] + [f"• {c}" for c in changes]
    text = "\n".join(lines)
    if len(text) > DISCORD_LIMIT:
        text = text[:DISCORD_LIMIT] + "\n…"
    try:
        httpx.post(webhook_url, json={"content": text}, timeout=20).raise_for_status()
        return True
    except httpx.HTTPError as exc:
        log.warning("notification failed: %s", exc)
        return False
