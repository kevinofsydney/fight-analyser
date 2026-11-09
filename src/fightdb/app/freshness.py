"""Reload fightdb code that changed on disk since it was imported (e.g. after a deploy).

Streamlit only reloads edited modules for browser sessions that were open when the file
changed, so a deploy that lands while nobody has the app open leaves every later visitor on the
old modules, while page scripts (re-read on every run) are new. Each page calls
`reload_changed_modules()` before its other imports. Keep this module small and stable: a
running process keeps whichever version it imported first.
"""

import os
import sys
import time
from pathlib import Path
from types import ModuleType

_PREFIX = "fightdb."
# Kept on `sys` so they survive this module itself being evicted and re-imported.
_RECORD = "_fightdb_module_mtimes"
_LAST_CHECK = "_fightdb_last_module_check"


def _mtime(module: ModuleType) -> float | None:
    origin = module.__spec__.origin if module.__spec__ else None
    try:
        return Path(str(origin)).stat().st_mtime if origin else None
    except OSError:
        return None


def _process_started() -> float | None:
    """Unix time this process started (Linux only, which covers the hosted app and Docker)."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        stat = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
        boot = next(
            float(line.split()[1])
            for line in Path("/proc/stat").read_text().splitlines()
            if line.startswith("btime ")
        )
        return boot + int(stat[19]) / os.sysconf("SC_CLK_TCK")
    except OSError, ValueError, IndexError, StopIteration, AttributeError:
        return None


def reload_changed_modules() -> bool:
    """Drop every loaded fightdb module if any of their files changed since import, so the page's
    imports load the current code. Returns True if modules were dropped."""
    now = time.time()
    seen: dict[str, float] = sys.__dict__.setdefault(_RECORD, {})
    # A module seen here for the first time was imported after the previous check (or, on the
    # first check, after the process started), so a file edited since then may be newer than the
    # copy in memory: reload to be safe. The margin absorbs /proc boot-time rounding.
    since = getattr(sys, _LAST_CHECK, None)
    if since is None:
        started = _process_started()
        since = None if started is None else started + 5
    stale = False
    for name, module in list(sys.modules.items()):
        if not name.startswith(_PREFIX):
            continue
        mtime = _mtime(module)
        if mtime is None:
            continue
        if name not in seen:
            seen[name] = mtime
            stale = stale or (since is not None and mtime > since)
        elif mtime != seen[name]:
            stale = True
    if stale:
        for name in [n for n in sys.modules if n.startswith(_PREFIX)]:
            del sys.modules[name]
            seen.pop(name, None)
    setattr(sys, _LAST_CHECK, now)
    return stale
