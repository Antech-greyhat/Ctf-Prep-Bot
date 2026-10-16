# Your fundamentals-first category order. It drives the /log buttons and the /stats report.
import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ctfbot.models import clean_text

logger = logging.getLogger(__name__)

DEFAULT_ORDER = ("General", "Web", "Forensics", "Crypto", "OSINT", "Reversing", "Pwn")
DEFAULT_TARGET_SOLVES = 10
MIN_CATEGORIES = 2
MAX_CATEGORIES = 12
# Short names keep the /log buttons readable on a phone.
MAX_NAME_LENGTH = 24
MAX_TARGET_SOLVES = 1000
ALLOWED_KEYS = frozenset({"order", "target_solves"})


class CategoriesError(Exception):
    pass


@dataclass(frozen=True)
class Categories:
    order: tuple[str, ...] = DEFAULT_ORDER
    target_solves: int = DEFAULT_TARGET_SOLVES

    def position(self, name: str) -> int | None:
        # Case-insensitive, so editing "crypto" to "Crypto" keeps old writeups counted.
        wanted = name.casefold()
        for index, category in enumerate(self.order):
            if category.casefold() == wanted:
                return index
        return None


def load_categories(path: Path) -> Categories:
    if not path.exists():
        logger.info("No categories file at %s; using the default order", path)
        return Categories()
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except tomllib.TOMLDecodeError as exc:
        raise CategoriesError(f"{path}: invalid TOML: {exc}") from None
    return parse_categories(data, str(path))


def parse_categories(data: dict[str, object], origin: str) -> Categories:
    unknown = set(data) - ALLOWED_KEYS
    if unknown:
        raise CategoriesError(f"{origin}: unknown key(s) {sorted(unknown)}")
    return Categories(
        order=_order(data.get("order"), origin),
        target_solves=_target(data.get("target_solves", DEFAULT_TARGET_SOLVES), origin),
    )


def _order(raw: object, origin: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not MIN_CATEGORIES <= len(raw) <= MAX_CATEGORIES:
        raise CategoriesError(
            f"{origin}: 'order' must be a list of {MIN_CATEGORIES} to {MAX_CATEGORIES} names"
        )
    names: list[str] = []
    for number, item in enumerate(raw, 1):
        # One extra character so an over-long name is rejected instead of silently cut.
        name = clean_text(item, max_length=MAX_NAME_LENGTH + 1) if isinstance(item, str) else ""
        if not name or len(name) > MAX_NAME_LENGTH:
            raise CategoriesError(
                f"{origin}: category #{number} must be text of 1 to {MAX_NAME_LENGTH} characters"
            )
        if any(name.casefold() == existing.casefold() for existing in names):
            raise CategoriesError(f"{origin}: category {name!r} is listed twice")
        names.append(name)
    return tuple(names)


def _target(raw: object, origin: str) -> int:
    # bool is a subclass of int, so `true` must be rejected explicitly.
    if type(raw) is not int or not 1 <= raw <= MAX_TARGET_SOLVES:
        raise CategoriesError(
            f"{origin}: 'target_solves' must be a whole number from 1 to {MAX_TARGET_SOLVES}"
        )
    return raw
