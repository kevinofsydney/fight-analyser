"""Streamlit app package."""

import os
import sys
from pathlib import Path
from types import ModuleType

# Streamlit reloads edited modules only under the app folder or a PYTHONPATH entry, so a deploy
# that changes e.g. fightdb.models would otherwise run new pages against stale imports.
_SRC = str(Path(__file__).resolve().parents[2])
_paths = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
if _SRC not in {str(Path(p).resolve()) for p in _paths}:
    os.environ["PYTHONPATH"] = os.pathsep.join([*_paths, _SRC])


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


def _changed_since(module: ModuleType, started: float) -> bool:
    origin = module.__spec__.origin if module.__spec__ else None
    try:
        # The margin absorbs boot-time rounding in /proc; deploys land long after startup.
        return bool(origin) and Path(str(origin)).stat().st_mtime > started + 5
    except OSError:
        return False


# Streamlit re-imports this package after a deploy changes an app file. Drop the rest of fightdb
# too when it may predate the deploy, so pages never mix new app code with stale modules.
_loaded = [
    n
    for n in sys.modules
    if n.startswith("fightdb.") and not (n == "fightdb.app" or n.startswith("fightdb.app."))
]
_started = _process_started()
if getattr(sys, "_fightdb_app_imported", False) or (
    _started is not None and any(_changed_since(sys.modules[n], _started) for n in _loaded)
):
    for _name in _loaded:
        del sys.modules[_name]
sys._fightdb_app_imported = True  # type: ignore[attr-defined]
