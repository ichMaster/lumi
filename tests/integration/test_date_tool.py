"""v0.31 — the by-date message tools (`messages_on` / `messages_between`): raw verbatim messages by
date, read straight from the store (no embedding, no meaning search). Trusted (her own transcript),
per-user, bounded (call cap + char budget + range-span cap). No paid calls."""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.embedder import MockEmbedder
from core.llm import MockLLMClient, is_trusted_text
from state.local_store import JsonRepository


class _Clock:
    """A mutable injected clock so messages land on chosen dates/times."""
    def __init__(self, dt: datetime) -> None:
        self.dt = dt

    def __call__(self) -> datetime:
        return self.dt


def _core(tmp_path, clock, *, date_tool=True, user="owner", repo=None, max_days=14, max_chars=4000):
    return Core(
        llm=MockLLMClient("ок"),
        repository=repo or JsonRepository(tmp_path / "store.json"),
        canon="C", model="m", user_id=user, clock=clock,
        embedder=MockEmbedder(), recall_enabled=True, embed_model="m@x",
        date_tool_enabled=date_tool, date_tool_max_days=max_days, date_tool_max_chars=max_chars,
    )


def _seed(core, clock, day, text=None):
    clock.dt = datetime.fromisoformat(day + "T14:30:00+00:00")
    core.reply(text or f"повідомлення за {day}", core.start_session())


def test_messages_on_returns_that_days_transcript(tmp_path):
    clock = _Clock(datetime(2026, 6, 11, 10, 0, tzinfo=UTC))
    core = _core(tmp_path, clock)
    _seed(core, clock, "2026-06-11")
    _seed(core, clock, "2026-06-13")
    tools, execute = core._date_tool_args()
    assert {t["name"] for t in tools} == {"messages_on", "messages_between"}
    out = execute("messages_on", {"date": "2026-06-13"})
    assert is_trusted_text(out)                          # her own transcript → trusted
    body = out["text"]
    assert "2026-06-13" in body and "повідомлення за 2026-06-13" in body
    assert "2026-06-11" not in body                      # only that day
    assert "14:30" in body                               # the message time is shown


def test_messages_between_returns_the_range(tmp_path):
    clock = _Clock(datetime(2026, 6, 11, 10, 0, tzinfo=UTC))
    core = _core(tmp_path, clock)
    for d in ("2026-06-11", "2026-06-12", "2026-06-13"):
        _seed(core, clock, d)
    out = core._date_tool_args()[1]("messages_between", {"start": "2026-06-11", "end": "2026-06-12"})
    body = out["text"]
    assert "2026-06-11" in body and "2026-06-12" in body
    assert "2026-06-13" not in body                      # outside the range (end is inclusive)


def test_messages_between_swaps_and_caps_the_span(tmp_path):
    clock = _Clock(datetime(2026, 6, 1, 10, 0, tzinfo=UTC))
    core = _core(tmp_path, clock, max_days=3)
    _seed(core, clock, "2026-06-01")
    out = core._date_tool_args()[1]("messages_between", {"start": "2026-06-30", "end": "2026-06-01"})
    assert not is_trusted_text(out) and "завеликий" in out   # span > max_days → notice (even reversed)


def test_messages_on_invalid_date(tmp_path):
    clock = _Clock(datetime(2026, 6, 11, 10, 0, tzinfo=UTC))
    core = _core(tmp_path, clock)
    out = core._date_tool_args()[1]("messages_on", {"date": "not-a-date"})
    assert not is_trusted_text(out) and "РРРР-ММ-ДД" in out


def test_messages_on_no_messages(tmp_path):
    clock = _Clock(datetime(2026, 6, 11, 10, 0, tzinfo=UTC))
    core = _core(tmp_path, clock)
    _seed(core, clock, "2026-06-11")
    out = core._date_tool_args()[1]("messages_on", {"date": "2020-01-01"})
    assert not is_trusted_text(out) and "немає повідомлень" in out


def test_date_tool_char_cap_truncates(tmp_path):
    clock = _Clock(datetime(2026, 6, 11, 14, 30, tzinfo=UTC))
    core = _core(tmp_path, clock, max_chars=80)
    s = core.start_session()
    for i in range(20):
        core.reply(f"довге повідомлення номер {i} з купою зайвого тексту", s)
    out = core._date_tool_args()[1]("messages_on", {"date": "2026-06-11"})
    assert is_trusted_text(out) and "обрізано" in out["text"]


def test_date_tool_per_turn_cap(tmp_path):
    clock = _Clock(datetime(2026, 6, 11, 10, 0, tzinfo=UTC))
    core = _core(tmp_path, clock)
    _seed(core, clock, "2026-06-11")
    _, execute = core._date_tool_args()
    for _ in range(3):
        execute("messages_on", {"date": "2026-06-11"})       # 3 allowed (default cap)
    assert "limit reached" in execute("messages_on", {"date": "2026-06-11"})   # 4th → capped


def test_date_tool_absent_when_off(tmp_path):
    clock = _Clock(datetime(2026, 6, 11, 10, 0, tzinfo=UTC))
    core = _core(tmp_path, clock, date_tool=False)
    assert core._date_tool_args() == (None, None)
    tools, _ = core._turn_tools()
    assert "messages_on" not in {t["name"] for t in (tools or [])}


def test_date_tool_is_single_user(tmp_path):
    clock = _Clock(datetime(2026, 6, 11, 14, 30, tzinfo=UTC))
    repo = JsonRepository(tmp_path / "shared.json")
    alice = _core(tmp_path, clock, user="alice", repo=repo)
    bob = _core(tmp_path, clock, user="bob", repo=repo)
    alice.reply("секрет Аліси на цю дату", alice.start_session())
    bob.reply("секрет Боба на цю дату", bob.start_session())
    out = bob._date_tool_args()[1]("messages_on", {"date": "2026-06-11"})
    body = out["text"] if is_trusted_text(out) else str(out)
    assert "Боба" in body and "Аліси" not in body        # B's date tool never returns A's messages
