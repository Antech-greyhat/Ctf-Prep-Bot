from datetime import UTC, datetime, timedelta, timezone

import pytest

from ctfbot.models import Event, clean_text, is_http_url, parse_utc, starting_within
from tests.helpers import FIXTURE_NOW, make_event


@pytest.mark.parametrize("url", ["https://ctf.example.org/", "http://example.org/path?q=1"])
def test_http_urls_are_accepted(url: str) -> None:
    assert is_http_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<script>",
        "ftp://example.org/",
        "https://",
        "//example.org",
        "https://exa mple.org",
        "https://example.org/" + "a" * 500,
    ],
)
def test_non_http_or_suspicious_urls_are_rejected(url: str) -> None:
    assert not is_http_url(url)


def test_clean_text_removes_terminal_escapes_and_bidi_controls() -> None:
    assert clean_text("Evil\x1b[2J CTF‮\nNext line") == "Evil[2J CTF Next line"


def test_clean_text_truncates() -> None:
    assert len(clean_text("a" * 500)) == 200


def test_parse_utc_converts_offsets_to_utc() -> None:
    assert parse_utc("2026-10-17T12:00:00+03:00") == datetime(2026, 10, 17, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize("value", ["2026-10-17T12:00:00", "not a date"])
def test_parse_utc_rejects_naive_or_invalid(value: str) -> None:
    with pytest.raises(ValueError):
        parse_utc(value)


def test_parse_utc_error_does_not_echo_input() -> None:
    with pytest.raises(ValueError) as info:
        parse_utc("<script>")
    assert "<script>" not in str(info.value)


def test_event_rejects_naive_and_non_utc_datetimes() -> None:
    with pytest.raises(ValueError):
        Event(title="x", start=datetime(2026, 1, 1), finish=FIXTURE_NOW, url=None, source="t")
    eat = timezone(timedelta(hours=3))
    with pytest.raises(ValueError):
        Event(
            title="x", start=FIXTURE_NOW.astimezone(eat), finish=FIXTURE_NOW, url=None, source="t"
        )


def test_event_rejects_non_http_url() -> None:
    with pytest.raises(ValueError):
        make_event(url="javascript:alert(1)")


def test_starting_within_is_inclusive_and_excludes_past_and_far_events() -> None:
    past = make_event("past", FIXTURE_NOW - timedelta(hours=1))
    now = make_event("now", FIXTURE_NOW)
    edge = make_event("edge", FIXTURE_NOW + timedelta(days=14))
    far = make_event("far", FIXTURE_NOW + timedelta(days=14, seconds=1))
    kept = starting_within([past, now, edge, far], FIXTURE_NOW, 14)
    assert [e.title for e in kept] == ["now", "edge"]
