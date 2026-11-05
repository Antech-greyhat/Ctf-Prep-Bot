from datetime import timedelta

from ctfbot.ctftime import CtftimeUnavailable
from ctfbot.models import Event
from ctfbot.sources import EventService, merge_events
from tests.helpers import FIXTURE_NOW, make_event

DAY = timedelta(days=1)


def test_merge_sorts_by_start_time() -> None:
    late = make_event("Late", FIXTURE_NOW + 3 * DAY)
    early = make_event("Early", FIXTURE_NOW + DAY)
    middle = make_event("Middle", FIXTURE_NOW + 2 * DAY, source="manual")
    assert [e.title for e in merge_events([late, early], [middle])] == ["Early", "Middle", "Late"]


def test_duplicates_ignore_case_and_spacing_and_first_source_wins() -> None:
    manual = make_event("Kenya  CTF", FIXTURE_NOW + DAY, source="manual")
    ctftime = make_event("kenya ctf", FIXTURE_NOW + DAY, source="CTFtime")
    merged = merge_events([manual], [ctftime])
    assert merged == [manual]


def test_same_title_different_start_is_not_a_duplicate() -> None:
    first = make_event("Weekly CTF", FIXTURE_NOW + DAY)
    second = make_event("Weekly CTF", FIXTURE_NOW + 8 * DAY)
    assert len(merge_events([first, second])) == 2


class FakeCtftime:
    def __init__(self, events: list[Event] | None) -> None:
        self._events = events

    async def events(self) -> list[Event]:
        if self._events is None:
            raise CtftimeUnavailable("down")
        return self._events


async def test_service_merges_and_applies_window() -> None:
    soon = make_event("Soon", FIXTURE_NOW + DAY)
    far = make_event("Far", FIXTURE_NOW + 30 * DAY)
    manual = make_event("Mine", FIXTURE_NOW + 2 * DAY, source="manual")
    service = EventService(FakeCtftime([soon, far]), [manual], lookahead_days=14)
    result = await service.upcoming(FIXTURE_NOW)
    assert [e.title for e in result.events] == ["Soon", "Mine"]
    assert result.ctftime_ok


async def test_service_still_returns_manual_events_when_ctftime_fails() -> None:
    manual = make_event("Mine", FIXTURE_NOW + 2 * DAY, source="manual")
    service = EventService(FakeCtftime(None), [manual], lookahead_days=14)
    result = await service.upcoming(FIXTURE_NOW)
    assert [e.title for e in result.events] == ["Mine"]
    assert not result.ctftime_ok


async def test_running_uses_start_inclusive_and_finish_exclusive() -> None:
    begins_now = make_event("Begins now", FIXTURE_NOW)
    ends_now = make_event("Ends now", FIXTURE_NOW - DAY, hours=24)
    future = make_event("Future", FIXTURE_NOW + timedelta(seconds=1))
    service = EventService(FakeCtftime([begins_now, ends_now, future]), [], lookahead_days=14)
    result = await service.running(FIXTURE_NOW)
    assert [e.title for e in result.events] == ["Begins now"]


async def test_running_merges_both_sources_soonest_finish_first() -> None:
    long = make_event("Long", FIXTURE_NOW - DAY, hours=72)
    short = make_event("Short", FIXTURE_NOW - DAY, hours=30, source="manual")
    service = EventService(FakeCtftime([long]), [short], lookahead_days=14)
    result = await service.running(FIXTURE_NOW)
    assert [e.title for e in result.events] == ["Short", "Long"]
    assert result.ctftime_ok


async def test_running_keeps_manual_events_when_ctftime_fails() -> None:
    manual = make_event("Mine", FIXTURE_NOW - DAY, hours=48, source="manual")
    service = EventService(FakeCtftime(None), [manual], lookahead_days=14)
    result = await service.running(FIXTURE_NOW)
    assert [e.title for e in result.events] == ["Mine"]
    assert not result.ctftime_ok
