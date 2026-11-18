"""v4.3 LUMI-310 — one page of her thought stream for the window (``core/thoughts.py`` ``thought_page``,
``Core.thoughts_page``).

Pinned: newest first, back to the first thought, every thought exactly once (thoughts sharing a ``when`` too); a
thought written between two requests never shifts the older pages; the kind (several at once) / spoken / date
filters, alone and together; the totals and the day counts over the filtered stream; the refusals; and the surfacing
filter — a thought sparked with another user never shows, and no item carries ``seeds`` or ``user_id``."""

from __future__ import annotations

import base64

import pytest

from core.repository import Thought
from core.thoughts import (
    THOUGHT_PAGE_TEXT,
    decode_thought_cursor,
    encode_thought_cursor,
    thought_page,
)

KINDS = ("think", "wonder", "learn")


def _stream(n: int = 25, *, per_minute: int = 2) -> list[Thought]:
    """``n`` thoughts, oldest first, ``per_minute`` sharing each ``when``, across three days."""
    out = []
    for i in range(n):
        minute = i // per_minute
        day = 16 + minute // 5
        when = f"2026-11-{day:02d}T10:{minute % 5:02d}"
        out.append(Thought(when=when, kind=KINDS[i % 3], text=f"думка {i}", emotion="calm",
                           seeds=("mood",), user_id="owner", spoken=i % 4 == 0))
    return out


def _texts(page) -> list[str]:
    return [item["text"] for item in page["items"]]


def _all_pages(stream, **filters) -> list[str]:
    seen, cursor = [], None
    for _ in range(100):
        page = thought_page(stream, before=cursor, **filters)
        seen += _texts(page)
        cursor = page["next"]
        if cursor is None:
            return seen
    raise AssertionError("the paging never ended")


# --- paging ------------------------------------------------------------------------------------------------------
def test_newest_first_back_to_the_first_thought_each_once():
    stream = _stream(25)
    assert _all_pages(stream, limit=4) == [f"думка {i}" for i in reversed(range(25))]
    assert _all_pages(stream, limit=100) == [f"думка {i}" for i in reversed(range(25))]


def test_an_item_carries_only_what_the_window_shows():
    page = thought_page(_stream(3))
    assert page["items"][0] == {"when": "2026-11-16T10:01", "kind": "learn", "text": "думка 2", "emotion": "calm",
                                "spoken": False}
    assert all(set(item) == {"when", "kind", "text", "emotion", "spoken"} for item in page["items"])
    assert page["next"] is None


def test_a_thought_written_between_two_pages_never_shifts_the_older_ones():
    stream = _stream(12)
    first = thought_page(stream, limit=5)
    stream.append(Thought(when="2026-11-18T23:59", kind="think", text="нова", emotion="calm", seeds=(),
                          user_id="owner"))
    stream.append(Thought(when=first["items"][-1]["when"], kind="think", text="в ту ж хвилину", emotion="calm",
                          seeds=(), user_id="owner"))  # the same `when` as the cursor's thought, written later
    second = thought_page(stream, before=first["next"], limit=5)
    assert _texts(first) == [f"думка {i}" for i in (11, 10, 9, 8, 7)]
    assert _texts(second) == [f"думка {i}" for i in (6, 5, 4, 3, 2)]


def test_the_input_list_is_never_changed():
    stream = _stream(10)
    before = list(stream)
    thought_page(list(reversed(stream)), limit=3)
    assert stream == before


# --- the filters -------------------------------------------------------------------------------------------------
def test_several_kinds_at_once():
    stream = _stream(25)
    got = _all_pages(stream, kinds=["wonder", "learn"], limit=3)
    assert got == [f"думка {i}" for i in reversed(range(25)) if i % 3 != 0]


def test_spoken_and_silent():
    stream = _stream(25)
    assert _all_pages(stream, spoken=True) == [f"думка {i}" for i in reversed(range(25)) if i % 4 == 0]
    assert _all_pages(stream, spoken=False) == [f"думка {i}" for i in reversed(range(25)) if i % 4 != 0]


def test_the_date_jump_starts_at_that_days_newest_thought():
    stream = _stream(25)  # 10 a day: 16th = 0..9, 17th = 10..19, 18th = 20..24
    page = thought_page(stream, date="2026-11-17", limit=3)
    assert _texts(page) == ["думка 19", "думка 18", "думка 17"]
    assert _all_pages(stream, date="2026-11-17", limit=3)[-1] == "думка 0"  # and on back to the first
    assert thought_page(stream, date="2026-11-15")["items"] == []
    assert _texts(thought_page(stream, date="2026-12-01", limit=1)) == ["думка 24"]


def test_the_filters_together():
    stream = _stream(25)
    got = _all_pages(stream, kinds=["think"], spoken=True, date="2026-11-17", limit=2)
    assert got == [f"думка {i}" for i in reversed(range(20)) if i % 3 == 0 and i % 4 == 0]


def test_a_cursor_wins_over_the_date():
    stream = _stream(25)
    first = thought_page(stream, limit=2)
    assert _texts(thought_page(stream, before=first["next"], date="2026-11-16", limit=2)) == ["думка 22", "думка 21"]


# --- the totals --------------------------------------------------------------------------------------------------
def test_the_totals_and_the_day_counts_follow_the_filters_not_the_page():
    stream = _stream(25)  # three days
    page = thought_page(stream, limit=2)
    assert page["stats"] == {"total": 25, "per_day": round(25 / 3, 1), "spoken_share": round(7 / 25, 4)}
    assert page["days"] == {"2026-11-18": 5}
    wonder = thought_page(stream, kinds=["wonder"], limit=100)
    assert wonder["stats"]["total"] == 8
    assert wonder["days"] == {"2026-11-18": 1, "2026-11-17": 4, "2026-11-16": 3}
    assert thought_page(stream, kinds=["think"], spoken=True)["stats"]["spoken_share"] == 1.0


def test_the_kinds_are_every_kind_in_the_stream_most_frequent_first():
    stream = _stream(25)
    assert thought_page(stream, kinds=["learn"])["kinds"] == ["think", "learn", "wonder"]  # 9, 8, 8: ties by name


def test_an_empty_stream():
    page = thought_page([])
    assert page == {"items": [], "next": None, "stats": {"total": 0, "per_day": 0.0, "spoken_share": 0.0},
                    "days": {}, "kinds": []}


def test_a_very_long_thought_is_cut():
    long = Thought(when="2026-11-18T10:00", kind="prompt", text="я" * (THOUGHT_PAGE_TEXT + 50), emotion="calm",
                   seeds=(), user_id="owner")
    text = thought_page([long])["items"][0]["text"]
    assert len(text) == THOUGHT_PAGE_TEXT + 1 and text.endswith("…")


# --- the refusals ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("cursor", ["%%%", "bm90IGEgY3Vyc29y",  # "not a cursor"
                                    base64.urlsafe_b64encode(b"123:art/x.png").decode().rstrip("="),  # a gallery one
                                    base64.urlsafe_b64encode(b"t1|2026-11-16T10:00|x").decode().rstrip("=")])
def test_a_malformed_or_foreign_cursor_is_refused(cursor):
    with pytest.raises(ValueError):
        thought_page(_stream(5), before=cursor)


@pytest.mark.parametrize("limit", [0, 101, -1, True, "5"])
def test_a_bad_limit_is_refused(limit):
    with pytest.raises(ValueError):
        thought_page(_stream(5), limit=limit)


@pytest.mark.parametrize("date", ["2026-13-01", "2026-02-30", "18.11.2026", "2026-11"])
def test_a_bad_date_is_refused(date):
    with pytest.raises(ValueError):
        thought_page(_stream(5), date=date)


def test_the_cursor_round_trips():
    assert decode_thought_cursor(encode_thought_cursor("2026-11-16T10:00", 3)) == ("2026-11-16T10:00", 3)


# --- the surfacing filter ----------------------------------------------------------------------------------------
def test_the_core_pages_only_its_own_users_thoughts(tmp_path):
    from core.agent import Core
    from core.llm import MockLLMClient
    from state.local_store import JsonRepository

    repo = JsonRepository(tmp_path / "s.json")
    for i, user in enumerate(["owner", "ada", "owner", "ada", "owner"]):
        repo.add_thought(Thought(when=f"2026-11-18T10:0{i}", kind="think", text=f"{user} {i}", emotion="calm",
                                 seeds=("closeness",), user_id=user))

    def core(user_id):
        return Core(llm=MockLLMClient(), repository=repo, canon="C", model="m", user_id=user_id)

    owner = core("owner").thoughts_page()
    assert _texts(owner) == ["owner 4", "owner 2", "owner 0"]
    assert owner["stats"]["total"] == 3
    assert _texts(core("ada").thoughts_page()) == ["ada 3", "ada 1"]
    assert all("seeds" not in item and "user_id" not in item for item in owner["items"])
    assert len(repo.thoughts_since("")) == 5  # nothing removed or written
