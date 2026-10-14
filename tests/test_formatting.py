from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ctfbot.formatting import (
    format_duration,
    render_event,
    render_event_list,
    split_message,
    telegram_length,
)
from ctfbot.sources import Upcoming
from tests.helpers import make_event

NAIROBI = ZoneInfo("Africa/Nairobi")
BERLIN = ZoneInfo("Europe/Berlin")


def test_utc_is_converted_to_owner_timezone() -> None:
    event = make_event(start=datetime(2026, 10, 17, 9, 0, tzinfo=UTC), hours=36)
    text = render_event(event, NAIROBI)
    assert "Sat 17 Oct 12:00 → Mon 19 Oct 00:00 EAT (36h)" in text


def test_daylight_saving_is_respected() -> None:
    summer = make_event(start=datetime(2026, 7, 1, 10, 0, tzinfo=UTC))
    winter = make_event(start=datetime(2026, 12, 1, 10, 0, tzinfo=UTC))
    assert "12:00" in render_event(summer, BERLIN)
    assert "11:00" in render_event(winter, BERLIN)


@pytest.mark.parametrize(
    ("hours", "expected"), [(6, "6h"), (47, "47h"), (48, "2d"), (60, "2d 12h")]
)
def test_duration(hours: int, expected: str) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    assert format_duration(start, start + timedelta(hours=hours)) == expected


def test_title_is_html_escaped() -> None:
    text = render_event(make_event('<script>alert("x")</script> & <b>'), NAIROBI)
    assert "<script>" not in text
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &lt;b&gt;" in text


def test_url_cannot_break_out_of_href() -> None:
    event = make_event(url='https://evil.example/"><b>x</b>')
    text = render_event(event, NAIROBI)
    assert 'href="https://evil.example/&quot;&gt;&lt;b&gt;x&lt;/b&gt;"' in text


def test_event_without_url_has_no_link() -> None:
    assert "<a " not in render_event(make_event(url=None), NAIROBI)


def test_source_is_escaped() -> None:
    assert "&lt;i&gt;" in render_event(make_event(source="<i>"), NAIROBI)


def test_unavailable_notice_is_shown() -> None:
    upcoming = Upcoming(events=(make_event(),), ctftime_ok=False)
    assert "CTFtime is unavailable" in render_event_list(upcoming, NAIROBI, 14, "Upcoming")


def test_notice_absent_when_ctftime_ok() -> None:
    upcoming = Upcoming(events=(make_event(),), ctftime_ok=True)
    assert "unavailable" not in render_event_list(upcoming, NAIROBI, 14, "Upcoming")


def test_empty_list_says_so() -> None:
    upcoming = Upcoming(events=(), ctftime_ok=True)
    assert "No matching CTFs found." in render_event_list(upcoming, NAIROBI, 14, "Upcoming")


def test_short_message_is_one_chunk() -> None:
    assert split_message("hello\n\nworld") == ["hello\n\nworld"]


def test_long_list_is_split_at_block_boundaries() -> None:
    events = tuple(
        make_event(f"Event {i} " + "x" * 150, url=f"https://ctf{i}.example.org/" + "p" * 200)
        for i in range(60)
    )
    text = render_event_list(Upcoming(events, True), NAIROBI, 14, "Upcoming")
    chunks = split_message(text)
    assert len(chunks) > 1
    for chunk in chunks:
        assert telegram_length(chunk) <= 4096
        assert chunk.count("<b>") == chunk.count("</b>")
        assert chunk.count("<a ") == chunk.count("</a>")
    assert "\n\n".join(chunks) == text


def test_emoji_count_double_towards_the_limit() -> None:
    assert telegram_length("🚩") == 2
    assert split_message("🚩" * 3 + "\n\n" + "🚩" * 3, limit=6) == ["🚩🚩🚩", "🚩🚩🚩"]


def test_oversized_single_block_is_rejected_instead_of_cut() -> None:
    with pytest.raises(ValueError):
        split_message("x" * 5000)
