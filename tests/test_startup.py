from pathlib import Path

import pytest

from ctfbot import __main__ as entry
from tests.helpers import env


@pytest.mark.parametrize(
    ("setting", "label"),
    [("EVENTS_FILE", "Events file error"), ("CATEGORIES_FILE", "Categories file error")],
)
@pytest.mark.parametrize("kind", ["directory", "invalid_utf8", "permission_denied"])
def test_unreadable_optional_file_exits_before_creating_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    setting: str,
    label: str,
    kind: str,
) -> None:
    path = tmp_path / "settings.toml"
    if kind == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"\xff" if kind == "invalid_utf8" else b"")
    database = tmp_path / "data" / "bot.db"
    settings = env(
        EVENTS_FILE=str(tmp_path / "missing-events.toml"),
        CATEGORIES_FILE=str(tmp_path / "missing-categories.toml"),
        DATABASE_PATH=str(database),
    )
    settings[setting] = str(path)
    monkeypatch.setattr(entry, "read_environment", lambda: settings)
    monkeypatch.setattr(entry, "configure_logging", lambda secret: None)
    if kind == "permission_denied":
        original_open = Path.open

        def denied(file: Path, *args: object, **kwargs: object) -> object:
            if file == path:
                raise PermissionError("permission denied")
            return original_open(file, *args, **kwargs)

        monkeypatch.setattr(Path, "open", denied)
    assert entry.main([]) == 2
    assert label in capsys.readouterr().err
    assert not database.exists()
