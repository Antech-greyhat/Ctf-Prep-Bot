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
        self.events = events

    async def upcoming(self) -> list[Event]:
        if self.events is None:
            raise CtftimeUnavailable("down")
        return self.events


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
