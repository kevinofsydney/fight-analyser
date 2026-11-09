import os
import sys
import time
from pathlib import Path

import pytest
from streamlit import file_util

from fightdb.app import freshness


def test_fightdb_modules_outside_app_folder_are_watched_for_reload():
    import fightdb.app  # noqa: F401  (sets PYTHONPATH on import)
    from fightdb.models import ratings

    assert file_util.file_in_pythonpath(str(Path(ratings.__file__).resolve()))


@pytest.fixture
def restore_modules():
    saved = dict(sys.modules)
    record = dict(getattr(sys, freshness._RECORD, {}))
    last_check = getattr(sys, freshness._LAST_CHECK, None)
    yield
    sys.modules.clear()
    sys.modules.update(saved)
    setattr(sys, freshness._RECORD, record)
    setattr(sys, freshness._LAST_CHECK, last_check)


def test_changed_module_file_reloads_all_fightdb_code(restore_modules):
    from fightdb.models import ratings

    freshness.reload_changed_modules()
    assert not freshness.reload_changed_modules()  # nothing changed since the last check

    path = Path(ratings.__file__)
    before = path.stat()
    try:
        os.utime(path, (before.st_atime, before.st_mtime + 60))
        assert freshness.reload_changed_modules()
        assert "fightdb.models.ratings" not in sys.modules
        from fightdb.models import ratings as fresh

        assert fresh is not ratings
        assert not freshness.reload_changed_modules()  # new copies recorded, no reload loop
    finally:
        os.utime(path, (before.st_atime, before.st_mtime))


def test_change_between_a_reload_and_the_next_check_is_not_missed(restore_modules):
    from fightdb.models import ratings

    freshness.reload_changed_modules()
    path = Path(ratings.__file__)
    before = path.stat()
    try:
        os.utime(path, (before.st_atime, time.time() + 60))
        assert freshness.reload_changed_modules()
        from fightdb.models import ratings as reloaded  # noqa: F401  (imported by the next page run)

        os.utime(path, (before.st_atime, time.time() + 120))  # another deploy before the next run
        assert freshness.reload_changed_modules()
        assert "fightdb.models.ratings" not in sys.modules
    finally:
        os.utime(path, (before.st_atime, before.st_mtime))


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="uses the /proc start time")
@pytest.mark.parametrize("deployed", [True, False])
def test_first_check_reloads_modules_changed_after_process_start(tmp_path, deployed):
    """The hosted app's case: modules loaded before a deploy, before this check ever ran."""
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
    lines += [
        "from fightdb.app.freshness import reload_changed_modules",
        f"assert reload_changed_modules() is {deployed}",
        "from fightdb.models import ratings",
        "assert ratings is not old and ratings.DEPLOYED" if deployed else "assert ratings is old",
    ]
    result = subprocess.run(
        [sys.executable, "-B", "-c", "\n".join(lines)],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(src)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
