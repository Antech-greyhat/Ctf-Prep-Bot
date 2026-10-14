import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def ctftime_payload() -> list[dict[str, object]]:
    return json.loads((FIXTURES / "ctftime_sample.json").read_text(encoding="utf-8"))


@pytest.fixture
def good_entry() -> dict[str, object]:
    return {
        "id": 1,
        "title": "Example CTF",
        "url": "https://ctf.example.org/",
        "ctftime_url": "https://ctftime.org/event/1/",
        "start": "2026-10-20T10:00:00+00:00",
        "finish": "2026-10-21T10:00:00+00:00",
        "onsite": False,
        "format": "Jeopardy",
        "restrictions": "Open",
    }
