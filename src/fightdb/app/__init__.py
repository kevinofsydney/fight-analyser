"""Streamlit app package."""

import os
from pathlib import Path

# Let Streamlit's watcher reload edited fightdb modules outside the app folder for open sessions.
# Visitors who arrive after a change are covered by freshness.reload_changed_modules().
_SRC = str(Path(__file__).resolve().parents[2])
_paths = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
if _SRC not in {str(Path(p).resolve()) for p in _paths}:
    os.environ["PYTHONPATH"] = os.pathsep.join([*_paths, _SRC])
