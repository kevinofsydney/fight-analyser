from pathlib import Path

from streamlit import file_util


def test_fightdb_modules_outside_app_folder_are_watched_for_reload():
    import fightdb.app  # noqa: F401  (sets PYTHONPATH on import)
    from fightdb.models import ratings

    assert file_util.file_in_pythonpath(str(Path(ratings.__file__).resolve()))
