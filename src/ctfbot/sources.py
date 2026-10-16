import logging
from dataclasses import dataclass
from datetime import datetime
from itertools import chain

from ctfbot.ctftime import CtftimeClient, CtftimeUnavailable
from ctfbot.models import Event, starting_within

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Upcoming:
    events: tuple[Event, ...]
    ctftime_ok: bool


def dedupe_key(event: Event) -> tuple[str, datetime]:
    return " ".join(event.title.casefold().split()), event.start


def merge_events(*sources: list[Event]) -> list[Event]:
    # On duplicates the earlier source wins.
    unique: dict[tuple[str, datetime], Event] = {}
    for event in chain(*sources):
        unique.setdefault(dedupe_key(event), event)
    return sorted(unique.values(), key=lambda event: (event.start, event.title.casefold()))


class EventService:
    def __init__(
        self, ctftime: CtftimeClient, manual_events: list[Event], lookahead_days: int
    ) -> None:
        self._ctftime = ctftime
        self._manual_events = manual_events
        self._lookahead_days = lookahead_days

    async def upcoming(self, now: datetime, days: float | None = None) -> Upcoming:
        # `days` overrides the configured lookahead, e.g. for the reminder window.
        try:
            ctftime_events = await self._ctftime.upcoming()
            ctftime_ok = True
        except CtftimeUnavailable as exc:
            logger.warning("CTFtime unavailable: %s", exc)
            ctftime_events, ctftime_ok = [], False
        merged = merge_events(self._manual_events, ctftime_events)
        window = self._lookahead_days if days is None else days
        return Upcoming(tuple(starting_within(merged, now, window)), ctftime_ok)
