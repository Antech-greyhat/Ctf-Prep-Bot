import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ctfbot.manual_events import ManualEventsError, load_manual_events, parse_manual_events
from ctfbot.models import Event

REPO_ROOT = Path(__file__).parent.parent


def parse(text: str) -> list[Event]:
    return parse_manual_events(tomllib.loads(text), "events.toml")


VALID = """
[[event]]
title = "Local CTF"
start = 2026-11-07T09:00:00+03:00
finish = 2026-11-07T17:00:00+03:00
url = "https://ctf.example.org/"
"""


def test_valid_entry_is_parsed_and_converted_to_utc() -> None:
    [event] = parse(VALID)
    assert event.title == "Local CTF"
    assert event.start == datetime(2026, 11, 7, 6, 0, tzinfo=UTC)
    assert event.source == "manual"


def test_shipped_example_file_is_valid_and_uses_placeholder_dates() -> None:
    events = load_manual_events(REPO_ROOT / "events.example.toml")
    assert {e.title for e in events} == {"CyberGame Kenya", "Safaricom CTF"}
    assert all(e.start.year == 2099 for e in events)


def test_missing_file_means_no_manual_events(tmp_path: Path) -> None:
    assert load_manual_events(tmp_path / "nope.toml") == []


def test_invalid_toml_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "events.toml"
    path.write_text("[[event]\ntitle = ", encoding="utf-8")
    with pytest.raises(ManualEventsError, match="events.toml"):
        load_manual_events(path)


@pytest.mark.parametrize(
    ("replace", "replacement", "message"),
    [
        ("start = 2026-11-07T09:00:00+03:00", "start = 2026-11-07T09:00:00", "'start' must be"),
        ("start = 2026-11-07T09:00:00+03:00", "start = 2026-11-07", "'start' must be"),
        ("start = 2026-11-07T09:00:00+03:00", 'start = "2026-11-07T09:00"', "'start' must be"),
        ("finish = 2026-11-07T17:00:00+03:00", "finish = 2026-11-06T17:00:00+03:00", "before"),
        ('url = "https://ctf.example.org/"', 'url = "javascript:alert(1)"', "'url'"),
        ('url = "https://ctf.example.org/"', 'link = "https://x.org"', "unknown key"),
        ('title = "Local CTF"', 'title = ""', "'title' is required"),
        ('title = "Local CTF"', "title = 5", "'title' is required"),
    ],
)
def test_bad_entries_point_to_the_entry(replace: str, replacement: str, message: str) -> None:
    with pytest.raises(ManualEventsError, match=message) as info:
        parse(VALID + VALID.replace(replace, replacement))
    assert "[[event]] #2" in str(info.value)


def test_error_includes_title_for_orientation() -> None:
    with pytest.raises(ManualEventsError, match="'Local CTF'"):
        parse(VALID.replace("+03:00\nfinish", "\nfinish"))


def test_unknown_top_level_key_is_rejected() -> None:
    with pytest.raises(ManualEventsError, match="top-level"):
        parse('title = "oops"\n' + VALID)


def test_event_must_be_a_list_of_tables() -> None:
    with pytest.raises(ManualEventsError, match="list"):
        parse('event = "x"')
