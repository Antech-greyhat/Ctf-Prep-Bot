import tomllib
from pathlib import Path

import pytest

from ctfbot import __main__ as entry
from ctfbot.categories import (
    DEFAULT_ORDER,
    MAX_CATEGORIES,
    Categories,
    CategoriesError,
    load_categories,
    parse_categories,
)
from ctfbot.writeup_handlers import CATEGORY_DATA, category_keyboard
from tests.helpers import env

EXAMPLE = Path(__file__).parent.parent / "categories.example.toml"
ZERO_WIDTH_SPACE = "\u200b"


def test_missing_file_uses_defaults(tmp_path: Path) -> None:
    assert load_categories(tmp_path / "nope.toml") == Categories()


def test_example_file_matches_built_in_default() -> None:
    assert load_categories(EXAMPLE) == Categories()
    assert Categories().order == DEFAULT_ORDER
    assert DEFAULT_ORDER == ("General", "Web", "Forensics", "Crypto", "OSINT", "Reversing", "Pwn")
    assert Categories().target_solves == 10


def test_valid_file(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text('target_solves = 5\norder = ["Web", " Crypto "]\n', encoding="utf-8")
    assert load_categories(path) == Categories(order=("Web", "Crypto"), target_solves=5)


def test_target_is_optional() -> None:
    assert parse_categories({"order": ["a", "b"]}, "x").target_solves == 10


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"order": "Web"},
        {"order": ["Web"]},
        {"order": [f"c{n}" for n in range(MAX_CATEGORIES + 1)]},
        {"order": ["Web", 3]},
        {"order": ["Web", ""]},
        {"order": ["Web", ZERO_WIDTH_SPACE]},
        {"order": ["Web", "x" * 25]},
        {"order": ["Web", "web"]},
        {"order": ["Web", "Pwn"], "target_solves": 0},
        {"order": ["Web", "Pwn"], "target_solves": True},
        {"order": ["Web", "Pwn"], "target_solves": "10"},
        {"order": ["Web", "Pwn"], "colour": "red"},
    ],
)
def test_invalid_files_are_rejected(data: dict[str, object]) -> None:
    with pytest.raises(CategoriesError):
        parse_categories(data, "categories.toml")


def test_invalid_toml_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text("order = [", encoding="utf-8")
    with pytest.raises(CategoriesError, match="invalid TOML"):
        load_categories(path)


def test_position_ignores_case() -> None:
    categories = Categories(order=("Web", "Crypto"))
    assert categories.position("crypto") == 1
    assert categories.position("Pwn") is None


def test_example_is_valid_toml() -> None:
    with EXAMPLE.open("rb") as file:
        assert "order" in tomllib.load(file)


def test_bad_categories_file_exits_2_before_creating_the_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    categories = tmp_path / "categories.toml"
    categories.write_text('order = ["Web"]\n', encoding="utf-8")
    database = tmp_path / "data" / "bot.db"
    settings = env(
        EVENTS_FILE=str(tmp_path / "none.toml"),
        CATEGORIES_FILE=str(categories),
        DATABASE_PATH=str(database),
    )
    monkeypatch.setattr(entry, "read_environment", lambda: settings)
    monkeypatch.setattr(entry, "configure_logging", lambda secret: None)
    assert entry.main([]) == 2
    assert "Categories file error" in capsys.readouterr().err
    assert not database.exists()


def test_every_allowed_category_fits_the_button_pattern() -> None:
    # The /log buttons carry the category's index; raising MAX_CATEGORIES past what the
    # anchored pattern accepts would make the last buttons look like tampered data.
    largest = Categories(order=tuple(f"c{n}" for n in range(MAX_CATEGORIES)))
    buttons = [button for row in category_keyboard(largest).inline_keyboard for button in row]
    assert len(buttons) == MAX_CATEGORIES
    assert all(CATEGORY_DATA.match(str(button.callback_data)) for button in buttons)
