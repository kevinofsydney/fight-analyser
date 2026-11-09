import importlib
import os
import sys
import time
from pathlib import Path

import pytest
from streamlit import file_util


def test_fightdb_modules_outside_app_folder_are_watched_for_reload():
    import fightdb.app  # noqa: F401  (sets PYTHONPATH on import)
    from fightdb.models import ratings

    assert file_util.file_in_pythonpath(str(Path(ratings.__file__).resolve()))


def test_reimporting_app_drops_modules_loaded_before_a_deploy():
    import fightdb.app
    from fightdb.models import ratings

    saved = dict(sys.modules)
    try:
        importlib.reload(fightdb.app)
        assert "fightdb.models.ratings" not in sys.modules
        from fightdb.models import ratings as fresh

        assert fresh is not ratings and hasattr(fresh, "elo_breakdown")
    finally:
        sys.modules.clear()
        sys.modules.update(saved)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="uses the /proc start time")
@pytest.mark.parametrize("deployed", [True, False])
def test_first_app_import_drops_modules_changed_after_process_start(tmp_path, deployed):
    """The hosted app's case: a module loaded before a deploy, then its file replaced."""
    import shutil
    import subprocess

    src = tmp_path / "src"
    shutil.copytree(
        Path(__file__).resolve().parents[2] / "src" / "fightdb",
        src / "fightdb",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    hour_ago = time.time() - 3600
    for path in src.rglob("*.py"):
        os.utime(path, (hour_ago, hour_ago))
    lines = [
        "import os, pathlib, time",
        "import fightdb.models.ratings as old",
        "p = pathlib.Path(old.__file__)",
    ]
    if deployed:
        lines += [
            "p.write_text(p.read_text() + chr(10) + 'DEPLOYED = True' + chr(10))",
            "os.utime(p, (time.time() + 60, time.time() + 60))",
        ]
    lines += ["import fightdb.app", "from fightdb.models import ratings"]
    lines.append(
        "assert ratings is not old and ratings.DEPLOYED" if deployed else "assert ratings is old"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", "\n".join(lines)],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(src)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
