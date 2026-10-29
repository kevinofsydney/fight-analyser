"""Structured error log for humans and coding agents.

Every WARNING-or-worse log record (including `log.exception` and refresh warnings), every uncaught CLI exception and every
uncaught Streamlit page exception is appended as one JSON object per line to
`$LOG_DIR/errors.<process>.jsonl`. Each process type writes its own file, so the app and refresher
containers never rotate a shared file underneath each other.

Read it with `fightdb errors` (grouped summary) or `fightdb errors --json` (raw entries).
Known secrets are redacted before anything is written.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import logging.handlers
import os
import platform
import re
import sys
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fightdb.config import get_settings

MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 3
_PKG_DIR = str(Path(__file__).resolve().parent)

_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "fightdb_error_context", default=None
)

_SECRET_PATTERNS = [
    re.compile(r"https://(?:\w+\.)?discord(?:app)?\.com/api/webhooks/\S+"),
    re.compile(r"(?i)((?:api[_-]?key|token|password|secret)=)[^&\s'\"]+"),
]


@contextmanager
def context(**fields: Any) -> Iterator[None]:
    """Attach fields (run_id, command, page...) to any error logged inside the block."""
    token = _context.set({**(_context.get() or {}), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def mark_logged(exc: BaseException) -> None:
    """Flag an exception already written with full context, so the CLI doesn't log it twice."""
    exc._fightdb_logged = True  # type: ignore[attr-defined]


def was_logged(exc: BaseException) -> bool:
    return getattr(exc, "_fightdb_logged", False)


def bind(**fields: Any) -> None:
    """Like `context`, without a block: for Streamlit pages, which rerun top to bottom."""
    _context.set(fields)


def _secret_values() -> list[str]:
    s = get_settings()
    values = [s.app_password, s.discord_webhook_url]
    values += [os.environ.get(k, "") for k in ("CLOUDFLARE_TUNNEL_TOKEN", "ODDS_API_KEY")]
    return [v for v in values if v and len(v) >= 4]


def redact(text: str) -> str:
    for value in _secret_values():
        text = text.replace(value, "[REDACTED]")
    for pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m: (m.group(1) if m.groups() else "") + "[REDACTED]", text)
    return text


def _origin(tb: list[traceback.FrameSummary]) -> str | None:
    """Innermost frame in our own code: usually where the fix goes."""
    for frame in reversed(tb):
        if frame.filename.startswith(_PKG_DIR):
            rel = os.path.relpath(frame.filename, Path(_PKG_DIR).parents[1])
            return f"{rel}:{frame.lineno} in {frame.name}"
    return None


def build_entry(record: logging.LogRecord, process: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="seconds"),
        "level": record.levelname,
        "process": process,
        "logger": record.name,
        "message": redact(record.getMessage()),
        "context": _context.get() or {},
        "version": os.environ.get("GIT_SHA", "dev"),
        "python": platform.python_version(),
        "argv": redact(" ".join(sys.argv[1:])) if process == "cli" else None,
    }
    exc = record.exc_info[1] if record.exc_info else None
    if exc is not None:
        frames = traceback.extract_tb(exc.__traceback__)
        origin = _origin(frames)
        entry.update(
            exc_type=f"{type(exc).__module__}.{type(exc).__qualname__}".removeprefix("builtins."),
            exc_message=redact(str(exc)),
            origin=origin,
            traceback=redact("".join(traceback.format_exception(exc))),
        )
        key = f"{entry['exc_type']}|{origin}"
    else:
        key = f"{record.name}|{record.pathname}:{record.lineno}"
        entry["origin"] = f"{os.path.relpath(record.pathname)}:{record.lineno}"
    entry["fingerprint"] = hashlib.sha1(key.encode()).hexdigest()[:10]
    return entry


class JsonErrorHandler(logging.handlers.RotatingFileHandler):
    def __init__(self, path: Path, process: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(
            path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8", delay=True
        )
        self.process = process
        self.setLevel(logging.WARNING)

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(build_entry(record, self.process), default=str)

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 - logging API
        # Never let a broken log directory take the app or the refresh down.
        pass


def log_path(process: str) -> Path:
    return Path(get_settings().log_dir) / f"errors.{process}.jsonl"


def install(process: str, *loggers: str) -> JsonErrorHandler | None:
    """Attach the JSON error handler to the root logger (and any non-propagating loggers named).
    Safe to call repeatedly: Streamlit reruns page scripts on every interaction."""
    targets = [logging.getLogger(), *(logging.getLogger(n) for n in loggers)]
    path = log_path(process)
    handler = next(
        (
            h
            for h in targets[0].handlers
            if isinstance(h, JsonErrorHandler) and Path(h.baseFilename) == path.resolve()
        ),
        None,
    )
    if handler is None:
        try:
            handler = JsonErrorHandler(path, process)
        except OSError:
            return None
        # A changed LOG_DIR replaces the old handler rather than writing to both.
        for lg in targets:
            for old in [h for h in lg.handlers if isinstance(h, JsonErrorHandler)]:
                lg.removeHandler(old)
                old.close()
    for lg in targets:
        if handler not in lg.handlers:
            lg.addHandler(handler)
    return handler


def read_entries(log_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(log_dir.glob("errors.*.jsonl*")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return sorted(entries, key=lambda e: e.get("ts", ""))


def summarise(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group by fingerprint, newest problem first, keeping the latest full entry of each."""
    groups: dict[str, dict[str, Any]] = {}
    for e in entries:
        g = groups.setdefault(
            e["fingerprint"], {"fingerprint": e["fingerprint"], "count": 0, "first_seen": e["ts"]}
        )
        g["count"] += 1
        g["last_seen"] = e["ts"]
        g["latest"] = e
    return sorted(groups.values(), key=lambda g: g["last_seen"], reverse=True)
