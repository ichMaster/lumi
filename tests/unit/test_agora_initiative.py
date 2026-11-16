"""v3.4 — her initiative in Agora: the poll (never a model call), the background compose and its guards, freshness and
staleness (LUMI-297); the ack (LUMI-298). Fake room clients, an injected clock and monotonic — no network, no paid
calls."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.embedder import MockEmbedder
from core.space import (
    INITIATIVE_COMPOSE_GAP_S,
    INITIATIVE_FRESH_S,
    INITIATIVE_KEPT_S,
    QUIET_ROOM,
    AgoraSpace,
    ObservedLine,
    SpaceCapped,
)
from state.space_store import SqliteSpaceStore
from tests._agora_world import FakeRoomModel

KYIV = ZoneInfo("Europe/Kyiv")
OFFER = {"reply": "Сьогодні вітер уже пахне першим снігом. У вас теж так?", "emotion": "calm", "intensity": 0.4}


class Clock:
    """A wall clock the test moves (the space reads its tz and the offers' age from it)."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Mono:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _space(tmp_path, model=None, *, initiative=True, clock=None, mono=None, **kw) -> AgoraSpace:
    return AgoraSpace(SqliteSpaceStore(tmp_path / "spaces.sqlite"), FakeRoomModel(), model="gemini-2.5-flash",
                      canon="Ти — Лілі.", clock=clock or Clock(datetime(2026, 11, 16, 12, 0, tzinfo=KYIV)),
                      embedder=MockEmbedder(), monotonic=mono or Mono(), initiative=initiative,
                      initiative_llm=model if model is not None else FakeRoomModel(dict(OFFER)), **kw)


def _room(space: AgoraSpace, *lines: tuple[str, str, str]) -> None:
    space.observe([ObservedLine(i, who, text, f"2026-11-16T11:{n:02d}:00+02:00")
                   for n, (i, who, text) in enumerate(lines)])


def _settle(space: AgoraSpace) -> None:
    if space._ini_thread is not None:
        space._ini_thread.join(5)
        assert not space._ini_thread.is_alive()


def test_a_poll_never_calls_the_model_and_a_later_poll_gets_the_offer(tmp_path):
    model = FakeRoomModel(dict(OFFER))
    space = _space(tmp_path, model)
    _room(space, ("$a", "Ада", "Хто бачив туман?"), ("$b", "Бруно", "Я бачив, як він сідав на дахи."))
    assert space.poll_initiative() is None  # the first poll only starts a compose, in the background
    _settle(space)
    assert model.calls == 1
    offers = [space.poll_initiative() for _ in range(5)]
    assert model.calls == 1  # five polls, not one more model call
    assert {o.initiative_id for o in offers} == {offers[0].initiative_id}
    say = offers[0].as_say()
    assert say["outcome"] == "say" and say["text"] == OFFER["reply"] and say["initiative_id"].startswith("ini-")
    assert set(say) == {"outcome", "initiative_id", "text", "stats"} and "emotion" not in say  # never her state


def test_off_is_always_none_and_never_composes(tmp_path):
    model = FakeRoomModel(dict(OFFER))
    space = _space(tmp_path, model, initiative=False)
    _room(space, ("$a", "Ада", "Хто бачив туман?"))
    assert [space.poll_initiative() for _ in range(3)] == [None, None, None]
    assert space._ini_thread is None and model.calls == 0


def test_one_compose_at_a_time(tmp_path):
    hold = threading.Event()
    model = FakeRoomModel(dict(OFFER), hold=hold)
    mono = Mono()
    space = _space(tmp_path, model, mono=mono)
    _room(space, ("$a", "Ада", "Хто бачив туман?"))
    assert space.poll_initiative() is None
    assert model.entered.wait(5)
    mono.t += INITIATIVE_COMPOSE_GAP_S + 1  # even past the gap, a compose in flight is the only one
    assert space.poll_initiative() is None and space.poll_initiative() is None
    hold.set()
    _settle(space)
    assert model.calls == 1
    assert space.poll_initiative() is not None


def test_the_gap_holds_the_next_attempt_even_after_an_empty_answer(tmp_path):
    model = FakeRoomModel({"reply": "", "emotion": "calm", "intensity": 0.2})  # she doesn't want to
    mono = Mono()
    space = _space(tmp_path, model, mono=mono)
    _room(space, ("$a", "Ада", "Хто бачив туман?"))
    assert space.poll_initiative() is None
    _settle(space)
    assert space.store.latest_offer("agora") is None  # an empty answer is no offer
    mono.t += INITIATIVE_COMPOSE_GAP_S - 1
    assert space.poll_initiative() is None
    _settle(space)
    assert model.calls == 1  # the gap holds
    mono.t += 2
    space.poll_initiative()
    _settle(space)
    assert model.calls == 2  # past the gap, a new attempt


def test_never_twice_in_a_row(tmp_path):
    model = FakeRoomModel(dict(OFFER))
    space = _space(tmp_path, model)
    _room(space, ("$a", "Ада", "Лілі, привіт!"))
    space.turn("Ада", "Лілі, привіт!", "$a")  # her reply is the room's last line now
    assert space.poll_initiative() is None
    _settle(space)
    assert model.calls == 0 and space._ini_thread is None


def test_a_full_cap_composes_nothing_and_counts_no_attempt(tmp_path):
    model = FakeRoomModel(dict(OFFER))
    mono = Mono()
    space = _space(tmp_path, model, mono=mono, max_turns_per_hour=1)
    _room(space, ("$a", "Ада", "Лілі, привіт!"))
    space.turn("Ада", "Лілі, привіт!", "$a")  # the hour's one call
    space.observe([ObservedLine("$b", "Бруно", "А я сьогодні пік хліб.", "2026-11-16T12:01:00+02:00")])  # after hers
    mono.t += 60
    assert space.poll_initiative() is None and space._ini_thread is None and model.calls == 0
    mono.t += 3600  # the hour rolled: the cap has room, and no attempt was counted, so no gap holds
    space.poll_initiative()
    _settle(space)
    assert model.calls == 1


def test_a_compose_counts_in_the_hourly_cap(tmp_path):
    space = _space(tmp_path, FakeRoomModel(dict(OFFER)), max_turns_per_hour=1)
    _room(space, ("$a", "Ада", "Хто бачив туман?"))
    space.poll_initiative()
    _settle(space)
    _room(space, ("$t", "Бруно", "Лілі, а ти?"))
    with pytest.raises(SpaceCapped):
        space.turn("Бруно", "Лілі, а ти?", "$t")


@pytest.mark.parametrize(("reply", "why"), [
    ("Віталій сьогодні розповідав мені про туман.", "names the owner"),
    ("Цікаво, що скаже Ich про туман.", "carries «Ich»"),
    ("…", "blocked"),
    ("", "empty"),
])
def test_an_initiative_about_him_or_empty_is_no_offer(tmp_path, caplog, reply, why):
    space = _space(tmp_path, FakeRoomModel({"reply": reply, "emotion": "calm", "intensity": 0.3}),
                   private_names=["Віталій"])
    _room(space, ("$a", "Ада", "Хто бачив туман?"))
    with caplog.at_level(logging.INFO, logger="lumi.space"):
        space.poll_initiative()
        _settle(space)
    assert space.store.latest_offer("agora") is None and space.poll_initiative() is None
    assert f"initiative held: {why}" in caplog.text


def test_an_offer_goes_stale_after_its_half_hour(tmp_path):
    model = FakeRoomModel(dict(OFFER))
    clock, mono = Clock(datetime(2026, 11, 16, 12, 0, tzinfo=KYIV)), Mono()
    space = _space(tmp_path, model, clock=clock, mono=mono)
    _room(space, ("$a", "Ада", "Хто бачив туман?"))
    space.poll_initiative()
    _settle(space)
    clock.advance(INITIATIVE_FRESH_S - 60)
    assert space.poll_initiative() is not None
    clock.advance(120)
    mono.t += INITIATIVE_COMPOSE_GAP_S + 1
    assert space.poll_initiative() is None  # past its half-hour: none, and a new compose starts
    _settle(space)
    assert model.calls == 2


def test_a_new_room_line_makes_the_offer_stale_at_once(tmp_path):
    model = FakeRoomModel(dict(OFFER))
    space = _space(tmp_path, model)
    _room(space, ("$a", "Ада", "Хто бачив туман?"))
    space.poll_initiative()
    _settle(space)
    assert space.poll_initiative() is not None
    space.observe([ObservedLine("$b", "Бруно", "Я бачив!", "2026-11-16T12:01:00+02:00")])
    assert space.poll_initiative() is None  # its context moved
    _settle(space)
    assert model.calls == 1  # and the gap holds a recompose


def test_a_retract_never_makes_an_offer_stale(tmp_path):
    space = _space(tmp_path, FakeRoomModel(dict(OFFER)))
    _room(space, ("$a", "Ада", "Лілі, привіт!"))
    space.turn("Ада", "Лілі, привіт!", "$a")
    space.observe([ObservedLine("$b", "Бруно", "А я сьогодні пік хліб.", "2026-11-16T12:01:00+02:00")])
    space.poll_initiative()
    _settle(space)
    space.retract("$a")  # a line leaves; nothing new arrived
    assert space.poll_initiative() is not None


def test_offers_older_than_a_day_are_pruned(tmp_path):
    clock = Clock(datetime(2026, 11, 16, 12, 0, tzinfo=KYIV))
    space = _space(tmp_path, FakeRoomModel(dict(OFFER)), clock=clock)
    _room(space, ("$a", "Ада", "Хто бачив туман?"))
    space.poll_initiative()
    _settle(space)
    offer = space.store.latest_offer("agora")
    clock.advance(INITIATIVE_KEPT_S + 1)
    space.poll_initiative()
    _settle(space)
    assert space.store.get_offer("agora", offer.initiative_id) is None


class RecordingModel(FakeRoomModel):
    def __init__(self, state):
        super().__init__(state)
        self.prompts: list[tuple[str, list]] = []

    def reply_structured(self, system, messages, model, cache_prefix=None, **_):
        self.prompts.append((system, messages))
        return super().reply_structured(system, messages, model)


def test_the_compose_prompt_is_the_space_recipe_and_the_initiative_instruction(tmp_path):
    from core.space import load_room_rule

    model = RecordingModel(dict(OFFER))
    space = _space(tmp_path, model, private_names=["Віталій"], mood=lambda: "Сьогодні їй легко.")
    _room(space, ("$a", "Ада", "Хто бачив туман?"), ("$b", "Ich", "Віталій бачив, як він сідав на дахи."))
    space.poll_initiative()
    _settle(space)
    [(system, messages)] = model.prompts
    assert system.startswith("Ти — Лілі.")
    assert load_room_rule() in system and "Сьогодні їй легко." in system
    assert "Зараз тебе ніхто не кликав" in system and "#" not in system.split("Зараз тебе ніхто не кликав")[1]
    assert "Ада: Хто бачив туман?" in messages[0]["content"]
    assert "Віталій" not in system + messages[0]["content"]  # his real names read «Ich» (R3)


def test_an_empty_room_still_has_a_transcript_line(tmp_path):
    model = RecordingModel(dict(OFFER))
    space = _space(tmp_path, model)
    space.poll_initiative()
    _settle(space)
    [(_, messages)] = model.prompts
    assert messages == [{"role": "user", "content": QUIET_ROOM}]


def test_a_compose_runs_on_its_own_client_never_the_turns(tmp_path):
    turn_model, ini_model = FakeRoomModel(), FakeRoomModel(dict(OFFER))
    space = AgoraSpace(SqliteSpaceStore(tmp_path / "spaces.sqlite"), turn_model, model="gemini-2.5-flash", canon="C",
                       clock=Clock(datetime(2026, 11, 16, 12, 0, tzinfo=KYIV)), initiative=True,
                       initiative_llm=ini_model)
    _room(space, ("$a", "Ада", "Хто бачив туман?"))
    space.poll_initiative()
    _settle(space)
    assert ini_model.calls == 1 and turn_model.calls == 0


def test_a_room_turn_is_never_refused_while_she_composes(tmp_path):
    hold = threading.Event()
    space = _space(tmp_path, FakeRoomModel(dict(OFFER), hold=hold))
    _room(space, ("$a", "Ада", "Хто бачив туман?"), ("$t", "Бруно", "Лілі, а ти що думаєш?"))
    space.poll_initiative()
    assert space._ini_llm.entered.wait(5)
    try:
        assert space.turn("Бруно", "Лілі, а ти що думаєш?", "$t").outcome == "reply"  # never a 409
    finally:
        hold.set()
        _settle(space)


def test_a_failed_compose_leaves_no_offer_and_never_raises(tmp_path, caplog):
    from core.llm import LLMError

    space = _space(tmp_path, FakeRoomModel(error=LLMError("down")))
    _room(space, ("$a", "Ада", "Хто бачив туман?"))
    with caplog.at_level(logging.WARNING, logger="lumi.space"):
        assert space.poll_initiative() is None
        _settle(space)
    assert space.store.latest_offer("agora") is None and "composing an initiative failed" in caplog.text


def test_the_compose_is_logged_in_the_ledger_as_the_rooms(tmp_path):
    import json

    ledger = tmp_path / "cache-log.jsonl"
    space = _space(tmp_path, FakeRoomModel(dict(OFFER)), cache_log_path=ledger)
    _room(space, ("$a", "Ада", "Хто бачив туман?"))
    space.poll_initiative()
    _settle(space)
    [event] = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines()]
    assert event["kind"] == "agora" and event["output"] == 38


def test_close_waits_for_a_compose_in_flight_and_then_no_compose_starts(tmp_path):
    hold = threading.Event()
    model = FakeRoomModel(dict(OFFER), hold=hold)
    space = _space(tmp_path, model)
    _room(space, ("$a", "Ада", "Хто бачив туман?"))
    space.poll_initiative()
    assert model.entered.wait(5)
    closer = threading.Thread(target=space.close)
    closer.start()
    closer.join(0.2)
    assert closer.is_alive()  # waiting for the compose
    hold.set()
    closer.join(5)
    assert not closer.is_alive()
    assert space.poll_initiative() is None and model.calls == 1


# --- v3.4 LUMI-298: the ack — an offer the room saw becomes her line --------------------------------------------------

def _offered(tmp_path, **kw) -> tuple[AgoraSpace, str]:
    space = _space(tmp_path, FakeRoomModel(dict(OFFER)), **kw)
    _room(space, ("$a", "Ада", "Хто бачив туман?"))
    space.poll_initiative()
    _settle(space)
    return space, space.poll_initiative().initiative_id


def test_the_ack_stores_her_line_once(tmp_path):
    space, ini = _offered(tmp_path)
    assert space.initiative_posted(ini, "$posted") is True
    line = space.store.get("agora", "$posted", "lili")
    assert line.text == OFFER["reply"] and line.speaker == "Лілі" and line.session_id == "agora-2026-11-16"
    assert (line.emotion, line.intensity) == ("calm", 0.4)
    hits = space.store.search_vectors("agora", space._embedder.embed([OFFER["reply"]])[0], 5)
    assert ("lili", "$posted") in {(m.role, m.source_id) for _, m in hits}  # embedded for recall
    assert space.initiative_posted(ini, "$posted") is False  # idempotent
    assert space.initiative_posted(ini, "$another") is False  # one offer, one line
    assert [m.source_id for m in space.store.window("agora", 10) if m.role == "lili"] == ["$posted"]


def test_an_unknown_id_is_nothing_stored(tmp_path):
    space, _ = _offered(tmp_path)
    assert space.initiative_posted("ini-000000000000", "$posted") is False
    assert space.store.get("agora", "$posted", "lili") is None


def test_an_offer_never_acked_is_never_remembered(tmp_path):
    space, _ = _offered(tmp_path)
    assert [m for m in space.store.window("agora", 10) if m.role == "lili"] == []
    hits = space.store.search_vectors("agora", space._embedder.embed([OFFER["reply"]])[0], 10)
    assert all(m.role != "lili" for _, m in hits)


def test_a_late_ack_is_honoured(tmp_path):
    clock = Clock(datetime(2026, 11, 16, 12, 0, tzinfo=KYIV))
    space, ini = _offered(tmp_path, clock=clock)
    space.observe([ObservedLine("$b", "Бруно", "Я бачив!", "2026-11-16T12:01:00+02:00")])  # stale for polling
    clock.advance(INITIATIVE_FRESH_S + 60)  # and past its half-hour
    assert space.poll_initiative() is None
    assert space.initiative_posted(ini, "$posted") is True  # the room saw it


def test_a_pruned_offer_is_nothing_stored(tmp_path):
    clock = Clock(datetime(2026, 11, 16, 12, 0, tzinfo=KYIV))
    space, ini = _offered(tmp_path, clock=clock)
    clock.advance(INITIATIVE_KEPT_S + 1)
    space.poll_initiative()  # prunes
    assert space.initiative_posted(ini, "$posted") is False


def test_after_the_ack_she_never_speaks_twice_in_a_row(tmp_path):
    mono = Mono()
    space, ini = _offered(tmp_path, mono=mono)
    model = space._ini_llm
    space.initiative_posted(ini, "$posted")
    mono.t += INITIATIVE_COMPOSE_GAP_S + 1  # the gap passed — still nothing: her line is the room's last
    assert space.poll_initiative() is None and model.calls == 1


def test_her_posted_line_is_in_the_next_room_turns_window(tmp_path):
    space, ini = _offered(tmp_path)
    space.initiative_posted(ini, "$posted")
    space.observe([ObservedLine("$t", "Бруно", "Лілі, і в мене так!", "2026-11-16T12:05:00+02:00")])
    _, messages = space.build_prompt(space.store.get("agora", "$t", "user"))
    assert f"Лілі (ти): {OFFER['reply']}" in messages[0]["content"]


def test_an_ack_with_the_switch_off_is_still_honoured(tmp_path):
    space, ini = _offered(tmp_path)
    space._initiative = False  # the owner turned it off after the offer went out
    assert space.initiative_posted(ini, "$posted") is True
