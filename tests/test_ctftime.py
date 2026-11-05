import json
import logging
from datetime import UTC, datetime

import httpx
import pytest

from ctfbot import ctftime
from ctfbot.ctftime import CtftimeClient, CtftimeUnavailable, parse_entry, parse_events
from ctfbot.sources import EventService
from tests.helpers import FIXTURE_NOW


def test_fixture_keeps_only_online_open_jeopardy(
    ctftime_payload: list[dict[str, object]],
) -> None:
    events = parse_events(ctftime_payload)
    expected = {
        e["title"]
        for e in ctftime_payload
        if e["onsite"] is False and e["format"] == "Jeopardy" and e["restrictions"] == "Open"
    }
    assert {e.title for e in events} == expected
    assert len(events) == 6
    assert "SAS CTF 2026 Finals" not in expected


@pytest.mark.parametrize(
    ("onsite", "event_format", "restrictions", "wanted"),
    [
        (False, "Jeopardy", "Open", True),
        (True, "Jeopardy", "Open", False),
        (False, "Attack-Defense", "Open", False),
        (False, "Jeopardy", "Academic", False),
        (False, "Jeopardy", "Prequalified", False),
    ],
)
def test_passes_filters(onsite: bool, event_format: str, restrictions: str, wanted: bool) -> None:
    assert ctftime.passes_filters(onsite, event_format, restrictions) is wanted


def test_fixture_times_are_utc(ctftime_payload: list[dict[str, object]]) -> None:
    for event in parse_events(ctftime_payload):
        assert event.start.utcoffset() is not None
        assert event.start.utcoffset().total_seconds() == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", None),
        ("title", 123),
        ("title", "\x1b​  "),
        ("start", 1760000000),
        ("start", "2026-10-20T10:00:00"),
        ("start", "soon"),
        ("finish", "2026-10-01T10:00:00+00:00"),
        ("onsite", "false"),
        ("onsite", 0),
        ("format", None),
        ("restrictions", ["Open"]),
    ],
)
def test_malformed_entries_are_skipped(
    good_entry: dict[str, object], field: str, value: object
) -> None:
    good_entry[field] = value
    assert parse_entry(good_entry) is None


def test_missing_field_is_skipped(good_entry: dict[str, object]) -> None:
    del good_entry["start"]
    assert parse_entry(good_entry) is None


def test_non_object_entries_are_skipped(good_entry: dict[str, object]) -> None:
    events = parse_events(["junk", 42, None, good_entry])
    assert [e.title for e in events] == ["Example CTF"]


def test_non_list_payload_is_rejected() -> None:
    with pytest.raises(CtftimeUnavailable):
        parse_events({"error": "nope"})


def test_malformed_warning_logs_id_but_not_values(
    good_entry: dict[str, object], caplog: pytest.LogCaptureFixture
) -> None:
    good_entry["start"] = "<b>injected</b>\nFAKE LOG LINE"
    with caplog.at_level(logging.WARNING):
        parse_entry(good_entry)
    assert "id=1" in caplog.text
    assert "injected" not in caplog.text


def test_unsafe_url_falls_back_to_ctftime_page(good_entry: dict[str, object]) -> None:
    good_entry["url"] = "javascript:alert(1)"
    event = parse_entry(good_entry)
    assert event is not None
    assert event.url == "https://ctftime.org/event/1/"


def test_no_safe_url_gives_none(good_entry: dict[str, object]) -> None:
    good_entry["url"] = ""
    good_entry["ctftime_url"] = "data:text/html,x"
    event = parse_entry(good_entry)
    assert event is not None
    assert event.url is None


class FakeApi:
    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self.responses = responses
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class Clock:
    def __init__(self) -> None:
        self.now = FIXTURE_NOW

    def __call__(self) -> datetime:
        return self.now


async def no_sleep(seconds: float) -> None:
    return None


def make_client(api: FakeApi, clock: Clock | None = None) -> CtftimeClient:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(api), headers={"User-Agent": ctftime.USER_AGENT}
    )
    return CtftimeClient(http, sleep=no_sleep, clock=clock or Clock())


def ok(payload: object) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(payload).encode())


def test_shared_client_has_timeout_and_user_agent() -> None:
    http = ctftime.make_http_client()
    assert http.timeout.read == 10.0
    assert http.headers["User-Agent"].startswith("ctf-prep-bot/")


async def test_results_are_cached_for_an_hour(good_entry: dict[str, object]) -> None:
    api = FakeApi([ok([good_entry]), ok([])])
    clock = Clock()
    client = make_client(api, clock)
    first = await client.events()
    clock.now = FIXTURE_NOW + ctftime.CACHE_TTL / 2
    assert await client.events() == first
    assert len(api.requests) == 1
    clock.now = FIXTURE_NOW + ctftime.CACHE_TTL
    assert await client.events() == []
    assert len(api.requests) == 2


async def test_request_sends_user_agent_and_no_finish_param() -> None:
    api = FakeApi([ok([])])
    await make_client(api).events()
    request = api.requests[0]
    assert request.headers["User-Agent"].startswith("ctf-prep-bot/")
    window_start = FIXTURE_NOW - ctftime.FETCH_BACK
    assert request.url.params["start"] == str(int(window_start.timestamp()))
    assert "finish" not in request.url.params


async def test_one_retry_after_server_error(good_entry: dict[str, object]) -> None:
    api = FakeApi([httpx.Response(503), ok([good_entry])])
    events = await make_client(api).events()
    assert len(events) == 1
    assert len(api.requests) == 2


async def test_gives_up_after_one_retry() -> None:
    api = FakeApi([httpx.ConnectTimeout("t"), httpx.Response(500), ok([])])
    with pytest.raises(CtftimeUnavailable):
        await make_client(api).events()
    assert len(api.requests) == 2


async def test_client_errors_are_not_retried() -> None:
    api = FakeApi([httpx.Response(404), ok([])])
    with pytest.raises(CtftimeUnavailable):
        await make_client(api).events()
    assert len(api.requests) == 1


async def test_invalid_json_is_unavailable() -> None:
    api = FakeApi([httpx.Response(200, content=b"<html>")])
    with pytest.raises(CtftimeUnavailable):
        await make_client(api).events()


async def test_failures_are_not_cached(good_entry: dict[str, object]) -> None:
    api = FakeApi([httpx.Response(500), httpx.Response(500), ok([good_entry])])
    client = make_client(api)
    with pytest.raises(CtftimeUnavailable):
        await client.events()
    assert len(await client.events()) == 1


# Running events. Confirmed against the real API: `start` keeps only events that began at or
# after it, so an event queried from its own midpoint is not returned. This fake applies the
# same rule, which means a request starting at `now` would find nothing running.
class StartFilteringApi:
    def __init__(self, payload: list[dict[str, object]]) -> None:
        self.payload = payload

    def __call__(self, request: httpx.Request) -> httpx.Response:
        since = datetime.fromtimestamp(int(request.url.params["start"]), UTC)
        return ok([e for e in self.payload if datetime.fromisoformat(e["start"]) >= since])


# Saturday of the fixture's busiest weekend: HITCON started the day before.
DURING_HITCON = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
RUNNING_THEN = {
    "HITCON CTF 2026",
    "RSTCON 2026 CTF",
    "Hack.lu CTF 2026",
    "cruXipher 2026 - ATMoS '26, BITS Hyderabad",
}


def fixture_service(payload: list[dict[str, object]], now: datetime) -> EventService:
    clock = Clock()
    clock.now = now
    return EventService(make_client(StartFilteringApi(payload), clock), [], lookahead_days=14)


async def test_events_that_already_started_are_found(
    ctftime_payload: list[dict[str, object]],
) -> None:
    result = await fixture_service(ctftime_payload, DURING_HITCON).running(DURING_HITCON)
    assert {e.title for e in result.events} == RUNNING_THEN


async def test_nothing_is_running_between_events(
    ctftime_payload: list[dict[str, object]],
) -> None:
    result = await fixture_service(ctftime_payload, FIXTURE_NOW).running(FIXTURE_NOW)
    assert result.events == ()


async def test_wider_window_does_not_leak_into_upcoming(
    ctftime_payload: list[dict[str, object]],
) -> None:
    upcoming = await fixture_service(ctftime_payload, DURING_HITCON).upcoming(DURING_HITCON)
    assert RUNNING_THEN.isdisjoint(e.title for e in upcoming.events)
    assert all(e.start >= DURING_HITCON for e in upcoming.events)


async def test_running_and_upcoming_share_one_cached_fetch(
    good_entry: dict[str, object],
) -> None:
    api = FakeApi([ok([good_entry])])
    service = EventService(make_client(api), [], lookahead_days=14)
    await service.running(FIXTURE_NOW)
    await service.upcoming(FIXTURE_NOW)
    assert len(api.requests) == 1


async def test_event_starting_after_the_fetch_counts_as_running_from_the_cache(
    good_entry: dict[str, object],
) -> None:
    good_entry["start"] = "2026-10-14T12:10:00+00:00"
    clock = Clock()
    service = EventService(make_client(FakeApi([ok([good_entry])]), clock), [], lookahead_days=14)
    assert (await service.running(clock.now)).events == ()
    clock.now = datetime(2026, 10, 14, 12, 20, tzinfo=UTC)
    assert [e.title for e in (await service.running(clock.now)).events] == ["Example CTF"]
