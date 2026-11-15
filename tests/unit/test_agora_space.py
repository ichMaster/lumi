"""v3.1 LUMI-280 — the space turn: observe without a model call, the room's prompt, its own client, one at a time."""

from __future__ import annotations

import copy
import json
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core.agent import Core
from core.clock import fixed_clock
from core.embedder import MockEmbedder
from core.llm import LLMError, MockLLMClient, ResponseStats, TurnDeadlineExceeded
from core.space import (
    AgoraSpace,
    ObservedLine,
    SpaceBusy,
    SpaceTimeout,
    speaker_problem,
)
from state.local_store import JsonRepository
from state.space_store import SqliteSpaceStore

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 11, 16, 8, 10, tzinfo=KYIV)


class FakeRoomModel:
    """The room's client in tests: records the prompts, returns a canned state, never the network."""

    def __init__(self, state=None, *, error: Exception | None = None, hold: threading.Event | None = None):
        self.state = state if state is not None else {"reply": "Туман — м'якість міста.", "emotion": "thoughtful",
                                                      "intensity": 0.5}
        self.error = error
        self.hold = hold
        self.calls: list[tuple[str, list, str]] = []
        self.entered = threading.Event()
        self.last_stats: ResponseStats | None = None

    def reply_structured(self, system, messages, model, cache_prefix=None, **_):
        self.calls.append((system, messages, model))
        self.entered.set()
        if self.hold is not None:
            self.hold.wait(5)
        if self.error is not None:
            raise self.error
        self.last_stats = ResponseStats(model=model, latency_ms=1840, input_tokens=6420, output_tokens=38)
        return dict(self.state)


def _space(tmp_path, llm=None, **kw) -> AgoraSpace:
    kw.setdefault("clock", fixed_clock(NOW))
    return AgoraSpace(SqliteSpaceStore(tmp_path / "spaces.sqlite"), llm or FakeRoomModel(), model="gemini-2.5-flash",
                      canon="CANON-ЛІЛІ", embedder=kw.pop("embedder", MockEmbedder()), **kw)


def _lines(*pairs) -> list[ObservedLine]:
    return [ObservedLine(f"${i}", who, text, f"2026-11-16T08:0{i}:00+02:00") for i, (who, text) in enumerate(pairs)]


def test_observe_is_idempotent_and_calls_no_model(tmp_path):
    llm = FakeRoomModel()
    emb = MockEmbedder()
    space = _space(tmp_path, llm, embedder=emb)
    lines = _lines(("Ада", "Доброго ранку всім!"), ("Ich", "Я бачив туман."))
    assert space.observe(lines) == (2, 0)
    assert space.observe(lines) == (0, 2)
    assert llm.calls == []  # hearing never calls the model
    assert emb.calls == [["Доброго ранку всім!", "Я бачив туман."]]  # embedded once, the new lines only
    assert len(space.store.search_vectors("agora", emb.embed(["туман"])[0], 5)) == 2


def test_a_turn_answers_from_the_window_with_the_trigger_last(tmp_path):
    llm = FakeRoomModel()
    space = _space(tmp_path, llm)
    space.observe(_lines(("Ада", "Хто бачив туман?"), ("Ich", "Я бачив.")))
    space.observe([ObservedLine("$late", "Мія", "Я теж.", "2026-11-16T08:00:30+02:00")])  # late, placed by ts
    out = space.turn("Бруно", "Лілі, а ти що думаєш?", "$trig")
    assert out.as_dict() == {"outcome": "reply", "reply": "Туман — м'якість міста.", "emotion": "thoughtful",
                             "intensity": 0.5, "turn_id": "$trig",
                             "stats": {"model": "gemini-2.5-flash", "input_tokens": 6420, "output_tokens": 38,
                                       "latency_ms": 1840}}
    [(system, messages, model)] = llm.calls
    assert model == "gemini-2.5-flash" and system.startswith("CANON-ЛІЛІ") and "Агорі" in system
    transcript = messages[0]["content"]
    assert transcript.index("Ада: Хто бачив туман?") < transcript.index("Мія: Я теж.") < transcript.index("Ich: Я бачив.")
    assert transcript.rstrip("»\n").endswith("Бруно: Лілі, а ти що думаєш?")  # the trigger is the last line
    # both lines stored in the room's session — the trigger under its turn_id, her reply under the same id
    assert space.store.get("agora", "$trig", "user").speaker == "Бруно"
    mine = space.store.get("agora", "$trig", "lili")
    assert mine.text == "Туман — м'якість міста." and mine.emotion == "thoughtful"


def test_an_observed_trigger_is_never_stored_twice(tmp_path):
    space = _space(tmp_path)
    space.observe([ObservedLine("$trig", "Бруно", "Лілі?", "2026-11-16T08:05:00+02:00")])
    space.turn("Бруно", "Лілі?", "$trig")
    lines = space.store.window("agora", 10)
    assert [(m.role, m.source_id) for m in lines] == [("user", "$trig"), ("lili", "$trig")]


def test_her_own_lines_are_marked_in_the_next_prompt(tmp_path):
    llm = FakeRoomModel()
    space = _space(tmp_path, llm)
    space.turn("Ада", "Лілі, привіт!", "$1")
    space.turn("Ада", "Як ти?", "$2")
    transcript = llm.calls[1][1][0]["content"]
    assert "Лілі (ти): Туман — м'якість міста." in transcript


def test_a_retry_returns_the_stored_outcome_without_a_second_call(tmp_path):
    llm = FakeRoomModel()
    space = _space(tmp_path, llm)
    first = space.turn("Бруно", "Лілі?", "$trig")
    assert space.turn("Бруно", "Лілі?", "$trig") == first and len(llm.calls) == 1
    # after a restart the stored reply still answers (R1: never a second answer)
    again = AgoraSpace(SqliteSpaceStore(tmp_path / "spaces.sqlite"), llm, model="gemini-2.5-flash", canon="C",
                       clock=fixed_clock(NOW))
    assert again.turn("Бруно", "Лілі?", "$trig").reply == first.reply and len(llm.calls) == 1


def test_one_room_turn_at_a_time(tmp_path):
    hold = threading.Event()
    llm = FakeRoomModel(hold=hold)
    space = _space(tmp_path, llm)
    runner = threading.Thread(target=space.turn, args=("Ада", "Лілі?", "$1"))
    runner.start()
    assert llm.entered.wait(5)
    with pytest.raises(SpaceBusy):
        space.turn("Бруно", "Лілі!", "$2")
    hold.set()
    runner.join(5)
    assert space.turn("Бруно", "Лілі!", "$2").outcome == "reply"  # free again


def test_the_clients_deadline_is_a_timeout_and_stores_no_reply(tmp_path):
    space = _space(tmp_path, FakeRoomModel(error=TurnDeadlineExceeded()))
    with pytest.raises(SpaceTimeout):
        space.turn("Ада", "Лілі?", "$1")
    assert space.store.get("agora", "$1", "lili") is None
    assert space.store.get("agora", "$1", "user") is not None  # the room's own line was said — it stays


def test_a_turn_past_its_budget_drops_its_reply(tmp_path):
    ticks = iter([0.0, 25.0])  # the call took 25 s against a 20 s budget
    space = _space(tmp_path, monotonic=lambda: next(ticks), timeout_s=20)
    with pytest.raises(SpaceTimeout):
        space.turn("Ада", "Лілі?", "$1")
    assert space.store.get("agora", "$1", "lili") is None


def test_an_abandoned_turn_drops_its_reply_when_it_ends(tmp_path):
    hold = threading.Event()
    space = _space(tmp_path, FakeRoomModel(hold=hold))
    errors: list[Exception] = []

    def run():
        try:
            space.turn("Ада", "Лілі?", "$1")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    runner = threading.Thread(target=run)
    runner.start()
    assert space._llm.entered.wait(5)
    space.abandon("$1")  # the server's backstop gave up waiting
    hold.set()
    runner.join(5)
    assert isinstance(errors[0], SpaceTimeout) and space.store.get("agora", "$1", "lili") is None


def test_model_and_gate_errors_pass_through_and_store_no_reply(tmp_path):
    space = _space(tmp_path, FakeRoomModel(error=LLMError("down")))
    with pytest.raises(LLMError):
        space.turn("Ада", "Лілі?", "$1")
    space = _space(tmp_path / "b", FakeRoomModel({"reply": "  "}))
    assert space.turn("Ада", "Лілі?", "$2").outcome == "pass"  # v3.3: an empty reply is silence, not an error
    assert space.store.get("agora", "$2", "lili") is None


def test_the_gate_repairs_a_malformed_state(tmp_path):
    space = _space(tmp_path, FakeRoomModel({"reply": "Так.", "emotion": "furious", "intensity": 7}))
    out = space.turn("Ада", "Лілі?", "$1")
    assert (out.emotion, out.intensity) == ("calm", 1.0)


def test_the_usage_line_is_the_rooms(tmp_path):
    log = tmp_path / "cache-log.jsonl"
    space = _space(tmp_path, cache_log_path=log)
    space.turn("Ада", "Лілі?", "$1")
    [row] = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert row["kind"] == "agora" and row["model"] == "gemini-2.5-flash" and row["input"] == 6420
    assert row["session_id"] == "agora-2026-11-16"


def test_a_core_beside_the_room_keeps_its_state(tmp_path):
    core = Core(llm=MockLLMClient(states={"reply": "так", "emotion": "joy", "intensity": 0.9}),
                repository=JsonRepository(tmp_path / "store.json"), canon="C", model="m")
    session = core.start_session()
    core.reply("привіт", session)
    before = (core.last_emotion, core.last_stats, copy.deepcopy(core.totals))
    space = _space(tmp_path)
    space.observe(_lines(("Ада", "Привіт")))
    space.turn("Ада", "Лілі?", "$1")
    assert (core.last_emotion, core.last_stats, core.totals) == before
    assert [m.text for m in core._repo.load_messages(session.id)] == ["привіт", "так"]


@pytest.mark.parametrize("speaker, ok", [
    ("Ада", True), ("Ich", True), ("x" * 64, True), ("", False), ("   ", False), ("x" * 65, False),
    ("Ада: Бруно", False), ("Ада\nБруно", False), (None, False),
])
def test_the_speaker_rule(speaker, ok):
    assert (speaker_problem(speaker) is None) is ok


def test_abandon_after_the_reply_was_stored_removes_it(tmp_path):
    # code review #1: the server's backstop fired (504), but the turn stored its reply in the instant before
    # abandon ran — the room saw nothing, so she must not remember saying it
    space = _space(tmp_path)
    space.turn("Ада", "Лілі?", "$1")
    assert space.store.get("agora", "$1", "lili") is not None
    space.abandon("$1")
    assert space.store.get("agora", "$1", "lili") is None and space.known_outcome("$1") is None
    assert space.store.get("agora", "$1", "user") is not None  # the room's own line stays — it was said
    assert space._abandoned == set()  # nothing kept for a turn that isn't running


def test_abandon_between_the_model_and_the_store_drops_the_reply(tmp_path):
    # the other interleaving, driven exactly: abandon lands while the turn is past the model, before its store step
    space = _space(tmp_path)
    real_validate_point = space._stats

    def abandon_then_stats(elapsed_ms):
        space.abandon("$1")
        return real_validate_point(elapsed_ms)

    space._stats = abandon_then_stats  # runs after the model, before the store-or-drop step
    with pytest.raises(SpaceTimeout):
        space.turn("Ада", "Лілі?", "$1")
    assert space.store.get("agora", "$1", "lili") is None and space._abandoned == set()


def test_a_big_observe_embeds_in_small_chunks(tmp_path):
    # code review #2: a 200-line backlog never holds the shared embedder for the whole batch — his turn's recall
    # waits at most one chunk
    from core.space import EMBED_CHUNK

    emb = MockEmbedder()
    space = _space(tmp_path, embedder=emb)
    lines = [ObservedLine(f"${i}", "Ада", f"рядок {i}", f"2026-11-16T08:{i // 60:02d}:{i % 60:02d}+02:00")
             for i in range(200)]
    assert space.observe(lines) == (200, 0)
    assert max(len(call) for call in emb.calls) <= EMBED_CHUNK == 16
    assert sum(len(call) for call in emb.calls) == 200
    assert len(space.store.search_vectors("agora", emb.embed(["рядок"])[0], 500)) == 200


def test_a_long_room_line_is_cut_in_the_prompt_but_kept_whole(tmp_path):
    # code review #3: an article pasted into the room (Matrix allows ~65 KB) is cut in every later room prompt
    from core.space import PROMPT_LINE_MAX

    llm = FakeRoomModel()
    space = _space(tmp_path, llm)
    article = "слово " * 10_000
    space.observe([ObservedLine("$long", "Ich", article, "2026-11-16T08:00:00+02:00")])
    space.turn("Ада", "Лілі, що скажеш?", "$1")
    transcript = llm.calls[0][1][0]["content"]
    assert len(transcript) < PROMPT_LINE_MAX + 500 and "слово…" in transcript
    assert space.store.get("agora", "$long", "user").text == article  # the store keeps the line whole


def test_the_stop_waits_for_a_room_turn_in_flight(tmp_path):
    # code review #4: closing the store under a running turn failed it on a closed database (a 500)
    hold = threading.Event()
    space = _space(tmp_path, FakeRoomModel(hold=hold))
    results: list = []
    runner = threading.Thread(target=lambda: results.append(space.turn("Ада", "Лілі?", "$1")))
    runner.start()
    assert space._llm.entered.wait(5)
    closer = threading.Thread(target=space.close)
    closer.start()
    closer.join(0.2)
    assert closer.is_alive()  # the close waits for the turn
    hold.set()
    runner.join(5)
    closer.join(5)
    assert results and results[0].outcome == "reply"
    reopened = SqliteSpaceStore(tmp_path / "spaces.sqlite")
    assert reopened.get("agora", "$1", "lili") is not None  # her reply landed before the store closed


def test_the_stop_never_hangs_on_a_stuck_turn(tmp_path):
    hold = threading.Event()
    space = _space(tmp_path, FakeRoomModel(hold=hold))
    runner = threading.Thread(target=lambda: _swallow(space.turn, "Ада", "Лілі?", "$1"))
    runner.start()
    assert space._llm.entered.wait(5)
    space.close(wait_s=0.05)  # returns: the wait is bounded
    hold.set()
    runner.join(5)


def _swallow(fn, *args):
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 — the store is closed under it; the stop already went on
        pass


def test_a_kept_outcome_evicted_mid_read_falls_back_to_the_store(tmp_path):
    # code review #6: `in` then `[]` raced the turn thread's eviction → KeyError → a 500 for a retry
    from collections import OrderedDict

    class EvictingDict(OrderedDict):
        def __contains__(self, key):  # the eviction lands right after the membership check
            found = super().__contains__(key)
            self.pop(key, None)
            return found

    space = _space(tmp_path)
    first = space.turn("Ада", "Лілі?", "$1")
    space._outcomes = EvictingDict(space._outcomes)
    again = space.known_outcome("$1")
    assert again is not None and again.reply == first.reply


# --- v3.2 LUMI-286: the name guard ------------------------------------------------------------------------------


@pytest.mark.parametrize("reply", ["Віталій сьогодні щасливий.", "Я говорила з віталієм.", "ВІТАЛІЮ привіт",
                                   "Vitalii is fine"])
def test_a_reply_naming_him_is_a_pass_and_never_stored(tmp_path, reply, caplog):
    llm = FakeRoomModel({"reply": reply, "emotion": "joy", "intensity": 0.8})
    space = _space(tmp_path, llm, private_names=["Віталій", "Vitalii"])
    with caplog.at_level("INFO", logger="lumi.space"):
        out = space.turn("Ада", "Лілі, з ким ти говориш?", "$1")
    assert out.as_dict() == {"outcome": "pass", "reply": "", "turn_id": "$1", "stats": out.stats}
    assert space.store.get("agora", "$1", "lili") is None  # her line never stored
    assert space.store.get("agora", "$1", "user") is not None  # the room's line was said — it stays
    assert "names the owner" in caplog.text and reply not in caplog.text  # why, never the reply itself
    assert space.turn("Ада", "Лілі, з ким ти говориш?", "$1").outcome == "pass" and len(llm.calls) == 1


def test_a_reply_without_his_name_is_untouched_and_no_names_means_no_guard(tmp_path):
    space = _space(tmp_path, FakeRoomModel({"reply": "Ich сьогодні тут.", "emotion": "calm", "intensity": 0.4}),
                   private_names=["Віталій"])
    assert space.turn("Ада", "Хто тут?", "$1").outcome == "reply"
    space = _space(tmp_path / "b", FakeRoomModel({"reply": "Віталій тут.", "emotion": "calm", "intensity": 0.4}))
    assert space.turn("Ада", "Хто тут?", "$1").outcome == "reply"  # no private_names → the guard is off



# --- v3.3 LUMI-289: passing ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("state", [{"reply": ""}, {"reply": "   "}, {}, {"reply": "…", "emotion": "calm"},
                                   {"reply": "...", "emotion": "calm", "intensity": 0.3}, {"reply": " … "}])
def test_an_empty_or_blocked_reply_is_a_pass(tmp_path, state, caplog):
    llm = FakeRoomModel(state)
    space = _space(tmp_path, llm)
    with caplog.at_level("INFO", logger="lumi.space"):
        out = space.turn("Ада", "Лілі?", "$1")
    assert out.as_dict() == {"outcome": "pass", "reply": "", "turn_id": "$1", "stats": out.stats}
    assert space.store.get("agora", "$1", "lili") is None
    assert "room reply passed" in caplog.text
    assert space.turn("Ада", "Лілі?", "$1").outcome == "pass" and len(llm.calls) == 1  # a retry: no second call


def test_a_real_reply_with_an_ellipsis_inside_is_not_a_pass(tmp_path):
    space = _space(tmp_path, FakeRoomModel({"reply": "Хм… туман.", "emotion": "thoughtful", "intensity": 0.4}))
    assert space.turn("Ада", "Лілі?", "$1").outcome == "reply"


def test_his_channel_still_errs_on_an_empty_reply(tmp_path):
    import pytest as _pytest

    from core.emotion import EmotionError, validate

    with _pytest.raises(EmotionError):
        validate({"reply": ""})  # the gate itself is unchanged — only the room maps it to a pass


# --- v3.3 LUMI-290: retracting -------------------------------------------------------------------------------------


def test_a_retract_removes_the_reply_and_its_vector_but_keeps_the_trigger(tmp_path):
    emb = MockEmbedder()
    space = _space(tmp_path, embedder=emb)
    space.turn("Ада", "Лілі, що скажеш про туман?", "$1")
    assert space.retract("$1") is True
    assert space.store.get("agora", "$1", "lili") is None
    assert space.store.get("agora", "$1", "user") is not None  # the room said it
    hits = space.store.search_vectors("agora", emb.embed(["Туман — м'якість міста."])[0], 10)
    assert all(m.role != "lili" for _, m in hits)  # recall never finds the retracted reply
    assert space.retract("$1") is False  # idempotent


def test_a_retract_of_nothing_stored_is_false(tmp_path):
    space = _space(tmp_path, FakeRoomModel({"reply": ""}))
    space.turn("Ада", "Лілі?", "$pass")
    assert space.retract("$pass") is False  # a pass stored nothing
    assert space.retract("$never-received") is False


def test_a_retract_during_a_running_turn_drops_its_reply_at_the_end(tmp_path):
    hold = threading.Event()
    space = _space(tmp_path, FakeRoomModel(hold=hold))
    errors: list = []
    runner = threading.Thread(target=lambda: _record(errors, space.turn, "Ада", "Лілі?", "$1"))
    runner.start()
    assert space._llm.entered.wait(5)
    assert space.retract("$1") is True
    hold.set()
    runner.join(5)
    assert errors and isinstance(errors[0], SpaceTimeout)  # its reply was dropped, nothing kept
    assert space.store.get("agora", "$1", "lili") is None


def test_a_retried_turn_after_a_retract_is_a_pass(tmp_path):
    llm = FakeRoomModel()
    space = _space(tmp_path, llm)
    space.turn("Ада", "Лілі?", "$1")
    space.retract("$1")
    assert space.turn("Ада", "Лілі?", "$1").outcome == "pass" and len(llm.calls) == 1  # never a second answer


def _record(errors, fn, *args):
    try:
        fn(*args)
    except Exception as exc:  # noqa: BLE001
        errors.append(exc)
