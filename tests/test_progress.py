from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ctfbot.categories import Categories
from ctfbot.formatting import progress_bar, render_report, split_message
from ctfbot.progress import Report, build_report
from tests.fake_telegram import running_bot

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
ORDER = Categories(order=("General", "Web", "Forensics", "Crypto", "Pwn"), target_solves=3)


def report(
    solved: dict[str, int] | None = None,
    tried: dict[str, int] | None = None,
    recent: list[str] | None = None,
    categories: Categories = ORDER,
) -> Report:
    solved_counts = Counter(solved or {})
    attempts = solved_counts + Counter(tried or {})
    return build_report(categories, solved_counts, attempts, recent or [], NOW - timedelta(days=1))


def test_focus_is_first_category_below_target() -> None:
    assert report().focus == "General"
    assert report({"General": 3}).focus == "Web"
    assert report({"General": 3, "Web": 5}).focus == "Forensics"
    # Order matters, not totals: a strong later category doesn't skip an earlier gap.
    assert report({"Web": 3, "Pwn": 9}).focus == "General"


def test_unsolved_attempts_do_not_move_the_focus() -> None:
    assert report({"General": 2}, tried={"General": 10}).focus == "General"


def test_no_focus_when_everything_reached_target() -> None:
    done = dict.fromkeys(ORDER.order, 3)
    assert report(done).focus is None
    assert not report(done, recent=["Pwn"] * 5).jumping


def test_jumping_when_most_recent_logs_are_two_or_more_steps_ahead() -> None:
    # Focus is General; Forensics is 2 steps ahead, Web only 1.
    assert report(recent=["Forensics", "Pwn", "General"]).jumping
    assert not report(recent=["Web", "Web", "General"]).jumping
    result = report(recent=["Pwn", "Crypto", "Pwn", "General"])
    assert result.jumped_to == ("Crypto", "Pwn")
    assert (result.jumped_logs, result.recent_logs) == (3, 4)


def test_needs_at_least_three_logs_before_warning() -> None:
    assert not report(recent=["Pwn", "Pwn"]).jumping
    assert report(recent=["Pwn", "Pwn", "Pwn"]).jumping


def test_exactly_half_is_not_jumping() -> None:
    assert not report(recent=["Pwn", "Pwn", "General", "General"]).jumping


def test_names_match_case_insensitively_and_unknown_ones_are_listed() -> None:
    result = report({"web": 2, "Web": 1, "Misc": 1, "misc": 2}, recent=["misc", "MISC", "misc"])
    web = next(row for row in result.rows if row.name == "Web")
    assert web.solved == 3
    [misc] = result.other
    assert (misc.name.casefold(), misc.solved) == ("misc", 3)
    assert result.recent_logs == 0


def test_progress_bar() -> None:
    assert progress_bar(0, 10) == "░" * 10
    assert progress_bar(4, 10) == "████░░░░░░"
    assert progress_bar(25, 10) == "█" * 10
    assert progress_bar(1, 3) == "███░░░░░░░"


def test_render_marks_focus_and_warns() -> None:
    text = render_report(report({"General": 3}, recent=["Pwn", "Pwn", "Crypto"]), NOW)
    assert "<pre>" in text and "</pre>" in text
    assert "← focus" in text
    assert "Focus: <b>Web</b>, 3 more to solve." in text
    assert "jumping around: 3 of your 3 logs" in text
    assert "Last writeup: yesterday." in text


def test_render_escapes_category_names() -> None:
    categories = Categories(order=("<b>", "Web"), target_solves=1)
    text = render_report(report(categories=categories, solved={"<i>": 1}), NOW)
    assert "&lt;b&gt;" in text
    assert "&lt;i&gt;" in text
    assert "<i>" not in text


def test_many_unknown_categories_still_fit_in_one_block() -> None:
    renamed = {f"old-category-name-{n:03}": 1 for n in range(500)}
    text = render_report(report(renamed), NOW)
    assert "and 480 more" in text
    split_message(text)


async def test_stats_command_end_to_end(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        await bot.chat().send("/stats")
        text = bot.telegram.last_text()
        assert "<pre>" in text
        assert "Focus: <b>General</b>" in text
        assert "No writeups yet" in text
