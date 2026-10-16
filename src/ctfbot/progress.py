# The /stats weakness report. Pure functions: no Telegram, no database, no clock.
#
# The rule, in plain words:
#   * Your focus is the first category in your order with fewer than `target_solves` solves.
#     Unsolved attempts don't count towards it.
#   * You are "jumping around" when, over the last 7 days (at least 3 logs), more than half of
#     your logs are two or more categories past your focus.
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from ctfbot.categories import Categories

RECENT_WINDOW = timedelta(days=7)
JUMP_DISTANCE = 2
MIN_RECENT_LOGS = 3


@dataclass(frozen=True)
class CategoryProgress:
    name: str
    solved: int
    attempts: int


@dataclass(frozen=True)
class Report:
    rows: tuple[CategoryProgress, ...]
    # Categories in the database but not in your order (renamed or removed later).
    other: tuple[CategoryProgress, ...]
    target: int
    focus: str | None
    recent_logs: int
    jumped_to: tuple[str, ...]
    jumped_logs: int
    last_log: datetime | None

    @property
    def jumping(self) -> bool:
        return (
            self.focus is not None
            and self.recent_logs >= MIN_RECENT_LOGS
            and self.jumped_logs * 2 > self.recent_logs
        )


def build_report(
    categories: Categories,
    solved: Mapping[str, int],
    attempts: Mapping[str, int],
    recent: Sequence[str],
    last_log: datetime | None,
) -> Report:
    order = categories.order
    solved_at = [0] * len(order)
    attempts_at = [0] * len(order)
    # Keyed by casefolded name so "misc" and "Misc" add up here too; shows the first spelling.
    other: dict[str, CategoryProgress] = {}
    for name, count in attempts.items():
        position = categories.position(name)
        if position is None:
            key = name.casefold()
            previous = other.get(key, CategoryProgress(name, 0, 0))
            other[key] = CategoryProgress(
                previous.name, previous.solved + solved.get(name, 0), previous.attempts + count
            )
        else:
            attempts_at[position] += count
            solved_at[position] += solved.get(name, 0)

    rows = tuple(
        CategoryProgress(name, solved_at[i], attempts_at[i]) for i, name in enumerate(order)
    )
    focus_index = next(
        (i for i, row in enumerate(rows) if row.solved < categories.target_solves), None
    )

    # Logs in categories outside your order have no position, so they can't be "past" the
    # focus; they are left out of both counts.
    recent_positions = [p for p in map(categories.position, recent) if p is not None]
    jumped = (
        [p for p in recent_positions if p - focus_index >= JUMP_DISTANCE]
        if focus_index is not None
        else []
    )
    return Report(
        rows=rows,
        other=tuple(sorted(other.values(), key=lambda row: row.name.casefold())),
        target=categories.target_solves,
        focus=order[focus_index] if focus_index is not None else None,
        recent_logs=len(recent_positions),
        jumped_to=tuple(order[p] for p in sorted(set(jumped))),
        jumped_logs=len(jumped),
        last_log=last_log,
    )
