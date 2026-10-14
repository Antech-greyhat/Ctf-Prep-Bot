import logging
import sys

import pytest

from ctfbot.logging_setup import RedactingFormatter, configure_logging
from tests.helpers import FAKE_TOKEN


def make_record(message: str, exc_info: object = None) -> logging.LogRecord:
    return logging.LogRecord("t", logging.ERROR, __file__, 1, message, None, exc_info)


def test_secret_is_redacted_from_message() -> None:
    formatter = RedactingFormatter("%(message)s", FAKE_TOKEN)
    text = formatter.format(make_record(f"https://api.telegram.org/bot{FAKE_TOKEN}/getMe"))
    assert FAKE_TOKEN not in text
    assert "[REDACTED]" in text


def test_secret_is_redacted_from_traceback() -> None:
    formatter = RedactingFormatter("%(message)s", FAKE_TOKEN)
    try:
        raise RuntimeError(f"The token `{FAKE_TOKEN}` was rejected")
    except RuntimeError:
        text = formatter.format(make_record("boom", sys.exc_info()))
    assert "was rejected" in text
    assert FAKE_TOKEN not in text


@pytest.mark.parametrize("name", ["httpx", "httpcore"])
def test_http_loggers_are_quiet(name: str) -> None:
    configure_logging(FAKE_TOKEN)
    assert logging.getLogger(name).getEffectiveLevel() == logging.WARNING
