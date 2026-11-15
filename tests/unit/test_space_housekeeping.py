"""v3.2 LUMI-284 — the room's housekeeping: the running digest and the day gist, by the room's own client."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core.clock import fixed_clock
from core.embedder import MockEmbedder
from core.llm import LLMError, ResponseStats
from core.space import AgoraSpace, ObservedLine, SpaceDigest, cut_words
from server.__main__ import start_space_housekeeping
from state.space_store import SqliteSpaceStore, _cosine, _scores

KYIV = ZoneInfo("Europe/Kyiv")
DAY = datetime(2026, 11, 16, 20, 0, tzinfo=KYIV)


class FakeHousekeeper:
    """The room's housekeeping client: records each call, answers with a canned text, never the network."""

    def __init__(self, text: str = "Ада питала про туман; Лілі відповіла, що він м'який.", error: Exception | None = None):
        self.text, self.error = text, error
        self.calls: list[tuple[str, str]] = []
        self.last_stats: ResponseStats | None = None

    def reply(self, system, messages, model, cache_prefix=None, **_):
        self.calls.append((system, messages[0]["content"]))
        if self.error is not None:
            raise self.error
        self.last_stats = ResponseStats(model=model, latency_ms=300, input_tokens=900, output_tokens=60)
        return self.text


class NoTurns:
    def reply_structured(self, *a, **k):  # the turn client is never used for housekeeping
        raise AssertionError("housekeeping must use its own client")


def _space(tmp_path, hk, now=DAY, **kw) -> AgoraSpace:
    return AgoraSpace(SqliteSpaceStore(tmp_path / "spaces.sqlite"), NoTurns(), model="gemini-2.5-flash", canon="C",
                      clock=fixed_clock(now), embedder=MockEmbedder(), housekeeping_llm=hk, today_lines=20,
                      today_digest_words=150, gist_words=40, **kw)


def _lines(n: int, day: str = "2026-11-16", start: int = 0) -> list[ObservedLine]:
    return [ObservedLine(f"${day}-{i}", "Ада" if i % 2 else "Бруно", f"репліка {i}",
                         f"{day}T{8 + i // 60:02d}:{i % 60:02d}:00+02:00") for i in range(start, start + n)]


def test_the_digest_is_due_at_the_batch_boundary(tmp_path):
    hk = FakeHousekeeper()
    space = _space(tmp_path, hk)
    space.observe(_lines(39))  # 19 lines past the last 20 → not yet
    assert space.housekeep(DAY) == [] and hk.calls == []
    space.observe(_lines(1, start=39))  # 20 scrolled out of the last 20 → due
    assert space.housekeep(DAY) == ["digest agora-2026-11-16"]
    digest = space.store.get_digest("agora", "agora-2026-11-16")
    assert digest == SpaceDigest("agora", "agora-2026-11-16", hk.text, 20)
    assert space.housekeep(DAY) == []  # nothing new scrolled out


def test_the_next_digest_folds_the_previous_one_in(tmp_path):
    hk = FakeHousekeeper()
    space = _space(tmp_path, hk)
    space.observe(_lines(40))
    space.housekeep(DAY)
    space.observe(_lines(20, start=40))
    assert space.housekeep(DAY) == ["digest agora-2026-11-16"]
    _, user = hk.calls[-1]
    assert user.startswith("Попередній конспект:\n" + hk.text)
    assert "репліка 20" in user and "репліка 39" in user and "репліка 19" not in user  # only the new part
    assert "репліка 40" not in user  # the last 20 stay live, not digested
    assert space.store.get_digest("agora", "agora-2026-11-16").upto == 40


def test_the_digest_is_cut_to_its_words(tmp_path):
    hk = FakeHousekeeper(text="слово " * 400)
    space = _space(tmp_path, hk)
    space.observe(_lines(40))
    space.housekeep(DAY)
    assert len(space.store.get_digest("agora", "agora-2026-11-16").text.split()) == 150
    assert cut_words("один два три", 2) == "один два…"


def test_yesterdays_gist_at_the_first_tick_after_her_midnight(tmp_path):
    hk = FakeHousekeeper(text="В Агорі говорили про туман і каву.")
    space = _space(tmp_path, hk)
    space.observe(_lines(5, day="2026-11-16"))
    assert space.housekeep(DAY) == []  # today's day is not gisted
    after_midnight = datetime(2026, 11, 17, 0, 1, tzinfo=KYIV)
    assert space.housekeep(after_midnight) == ["gist 2026-11-16"]
    assert [g.text for g in space.store.gists("agora")] == ["В Агорі говорили про туман і каву."]
    assert space.housekeep(after_midnight) == []  # never twice
    system, user = hk.calls[-1]
    assert "40 слів" in system and "репліка 0" in user and "Лілі (ти)" not in user


def test_a_day_with_no_lines_has_no_gist_and_old_days_are_left(tmp_path):
    hk = FakeHousekeeper()
    space = _space(tmp_path, hk)
    space.observe(_lines(3, day="2026-11-01"))  # past the 7-day reach
    assert space.housekeep(datetime(2026, 11, 17, 9, 0, tzinfo=KYIV)) == []
    assert hk.calls == []


def test_the_gist_folds_the_days_digest(tmp_path):
    hk = FakeHousekeeper()
    space = _space(tmp_path, hk)
    space.observe(_lines(45))
    space.housekeep(DAY)  # the digest of the first 25
    space.housekeep(datetime(2026, 11, 17, 0, 5, tzinfo=KYIV))
    _, user = hk.calls[-1]
    assert user.startswith("Конспект частини дня:\n" + hk.text) and "репліка 25" in user and "репліка 3\n" not in user


def test_a_model_error_writes_nothing_and_the_next_tick_retries(tmp_path):
    hk = FakeHousekeeper(error=LLMError("down"))
    space = _space(tmp_path, hk)
    space.observe(_lines(40))
    assert space.housekeep(DAY) == []
    assert space.store.get_digest("agora", "agora-2026-11-16") is None
    hk.error = None
    assert space.housekeep(DAY) == ["digest agora-2026-11-16"]


def test_a_dst_night_rolls_the_gist_on_her_day(tmp_path):
    # 2026-10-25: Kyiv falls back +03:00 → +02:00; lines on both sides belong to the 25th
    hk = FakeHousekeeper()
    now = datetime(2026, 10, 25, 23, 0, tzinfo=KYIV)
    space = _space(tmp_path, hk, now=now)
    space.observe([ObservedLine("$a", "Ада", "до", "2026-10-25T02:30:00+03:00"),
                   ObservedLine("$b", "Ада", "після", "2026-10-25T23:30:00+02:00")])
    assert space.housekeep(now) == []
    assert space.housekeep(now + timedelta(hours=1, minutes=1)) == ["gist 2026-10-25"]


def test_housekeeping_calls_go_to_the_rooms_ledger(tmp_path):
    hk = FakeHousekeeper()
    log = tmp_path / "cache-log.jsonl"
    space = _space(tmp_path, hk, cache_log_path=log)
    space.observe(_lines(40))
    space.housekeep(DAY)
    [row] = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert row["kind"] == "agora" and row["input"] == 900 and row["session_id"] == "agora-2026-11-16"


def test_the_timer_never_needs_the_servers_lock(tmp_path):
    hk = FakeHousekeeper()
    space = _space(tmp_path, hk)
    space.observe(_lines(40))
    his_lock = threading.Lock()
    his_lock.acquire()  # his turn holds the server's lock the whole time
    try:
        stop = start_space_housekeeping(space, fixed_clock(DAY), every_s=0.01)
        for _ in range(200):
            if space.store.get_digest("agora", "agora-2026-11-16") is not None:
                break
            threading.Event().wait(0.01)
        stop()
    finally:
        his_lock.release()
    assert space.store.get_digest("agora", "agora-2026-11-16") is not None
    assert start_space_housekeeping(None, fixed_clock(DAY))() is None  # the space off → nothing runs


def test_numpy_and_pure_python_scores_agree():
    vectors = [[1.0, 0.0, 0.0], [0.6, 0.8, 0.0], [0.0, 0.0, 1.0], [1.0, 1.0]]
    query = [1.0, 0.0, 0.0]
    fast = _scores(query, vectors)
    slow = [_cosine(query, v) for v in vectors]
    assert [round(a, 6) for a in fast] == [round(b, 6) for b in slow] and fast[3] == -1.0


def test_the_digest_round_trip(tmp_path):
    store = SqliteSpaceStore(tmp_path / "spaces.sqlite")
    store.set_digest(SpaceDigest("agora", "agora-2026-11-16", "перше", 20))
    store.set_digest(SpaceDigest("agora", "agora-2026-11-16", "друге", 40))  # overwritten as the session grows
    assert store.get_digest("agora", "agora-2026-11-16") == SpaceDigest("agora", "agora-2026-11-16", "друге", 40)
    assert store.get_digest("інший", "agora-2026-11-16") is None
