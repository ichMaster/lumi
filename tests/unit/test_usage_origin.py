"""v5.1 (LUMI-340): the origin and the channel every usage record carries (core.usage_origin, the cache-log record).

Pure pieces only: the client id's prefix → the channel, the housekeeping rule, the context set and restored around the
work, and the model-call record's round trip. No model, no network.
"""

import json
import threading
from types import SimpleNamespace

import pytest

from core import cache_log, usage_origin
from core.agent import Core
from core.llm import MockLLMClient
from state.local_store import JsonRepository


@pytest.mark.parametrize(("client_id", "channel"), [
    ("desktop-1a2b3c4d", "window"),
    ("tui-0123456789ab", "tui"),
    ("cli-0123456789ab", "cli"),
    ("0123456789ab", "api"),  # a pre-v5.1 terminal client: a bare hex id
    ("", "api"),
    (None, "api"),
    ("  desktop-9f  ", "window"),
    ("telegram-1", "api"),  # only the known client prefixes name a channel
])
def test_channel_of_reads_the_client_ids_prefix(client_id, channel):
    assert usage_origin.channel_of(client_id) == channel


def test_the_origin_helpers():
    assert usage_origin.directive("catchup") == "directive:catchup"
    assert usage_origin.schedule("muse-silent") == "schedule:muse-silent"
    assert usage_origin.housekeeping("room") == "housekeeping:room"


def test_no_entry_point_means_no_origin():
    assert usage_origin.current() == usage_origin.UsageOrigin("", "")
    assert usage_origin.for_call("reply") == usage_origin.UsageOrigin("", "")


@pytest.mark.parametrize("kind", sorted(usage_origin.HOUSEKEEPING_KINDS))
def test_a_housekeeping_call_is_housekeeping_whatever_started_the_work(kind):
    with usage_origin.usage_origin("turn", "window"):
        assert usage_origin.for_call(kind) == usage_origin.UsageOrigin(f"housekeeping:{kind}", "window")
    assert usage_origin.for_call(kind).origin == f"housekeeping:{kind}"  # outside any work too


@pytest.mark.parametrize("kind", ["reply", "tool", "think", "agora"])
def test_other_calls_carry_the_works_origin(kind):
    with usage_origin.usage_origin("schedule:catchup"):
        assert usage_origin.for_call(kind) == usage_origin.UsageOrigin("schedule:catchup", "")


def test_the_context_is_restored_after_the_work_nested_or_failing():
    with usage_origin.usage_origin("turn", "telegram"):
        with usage_origin.usage_origin("idle"):
            assert usage_origin.current().origin == "idle"
        assert usage_origin.current() == usage_origin.UsageOrigin("turn", "telegram")
        with pytest.raises(RuntimeError), usage_origin.usage_origin("schedule:x"):
            raise RuntimeError("the work failed")
        assert usage_origin.current().origin == "turn"
    assert usage_origin.current() == usage_origin.UsageOrigin()


def test_a_thread_started_inside_the_work_has_no_origin():
    seen = []
    with usage_origin.usage_origin("turn", "window"):
        t = threading.Thread(target=lambda: seen.append(usage_origin.current()))
        t.start()
        t.join()
    assert seen == [usage_origin.UsageOrigin()]


# --- the model-call record (cache-log.jsonl) --------------------------------------------------------

def _event(**kw) -> cache_log.CacheEvent:
    return cache_log.CacheEvent(ts="2026-11-21T10:00:00", kind="reply", cache_read=1, cache_write=0, input=2,
                                output=3, gap_s=None, cause="none", **kw)


def test_a_model_call_record_round_trips_with_its_origin_and_channel(tmp_path):
    path = tmp_path / "cache-log.jsonl"
    cache_log.append_event(path, _event(origin="turn", channel="window"))
    (got,) = cache_log.load_events(path)
    assert (got.origin, got.channel) == ("turn", "window")


def test_a_legacy_record_loads_without_them(tmp_path):
    path = tmp_path / "cache-log.jsonl"
    legacy = {k: v for k, v in json.loads(json.dumps(_event().__dict__)).items() if k not in ("origin", "channel")}
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    (got,) = cache_log.load_events(path)
    assert (got.origin, got.channel) == ("", "")


# --- the core's recorder ------------------------------------------------------------------------------

def _core(tmp_path) -> Core:
    return Core(llm=MockLLMClient(states={"reply": "Тут.", "emotion": "calm", "intensity": 0.4}),
                repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.", model="m",
                mood_enabled=False, cache_monitor=True, cache_log_path=tmp_path / "cache-log.jsonl")


def _stats():
    return SimpleNamespace(model="m", cache_read_tokens=0, cache_write_tokens=0, input_tokens=1, output_tokens=1,
                           latency_ms=5)


def test_the_cores_records_say_what_started_them(tmp_path):
    core = _core(tmp_path)
    session = core.start_session()
    with usage_origin.usage_origin("turn", "window"):
        core.reply("привіт", session)
        core._log_one_cache_event("mood", _stats())  # a mood call inside the turn
    records = cache_log.load_events(tmp_path / "cache-log.jsonl")
    replies = [r for r in records if r.kind == "reply"]
    assert replies and all((r.origin, r.channel) == ("turn", "window") for r in replies)
    (mood,) = [r for r in records if r.kind == "mood"]
    assert (mood.origin, mood.channel) == ("housekeeping:mood", "window")


def test_both_messages_of_a_turn_carry_its_channel_and_her_own_turn_none(tmp_path):
    core = _core(tmp_path)
    session = core.start_session()
    with usage_origin.usage_origin("turn", "telegram"):
        core.reply("привіт", session)
    with usage_origin.usage_origin("idle"):  # a spoken turn of hers: no channel
        core.reply("seed", session)
    msgs = core._repo.load_messages(session.id)
    assert [m.channel for m in msgs] == ["telegram", "telegram", None, None]


def test_a_failing_recorder_never_breaks_the_turn(tmp_path, monkeypatch):
    core = _core(tmp_path)
    session = core.start_session()

    def boom(*a, **k):
        raise OSError("the disk is full")

    monkeypatch.setattr(cache_log, "append_event", boom)
    with usage_origin.usage_origin("turn", "window"):
        state = core.reply("привіт", session)
    assert state.reply == "Тут."
