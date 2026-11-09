"""Streamlit app package."""

import os
from pathlib import Path

# Streamlit reloads edited modules only under the app folder or a PYTHONPATH entry, so a deploy
# that changes e.g. fightdb.models would otherwise run new pages against stale imports.
_SRC = str(Path(__file__).resolve().parents[2])
_paths = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
if _SRC not in {str(Path(p).resolve()) for p in _paths}:
    os.environ["PYTHONPATH"] = os.pathsep.join([*_paths, _SRC])
