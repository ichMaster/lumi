"""End-to-end emotion turn (LUMI-016): user_text -> EmotionState, persisted."""

import json

import pytest

from core.agent import Core
from core.emotion import Emotion, EmotionError
from core.llm import MockLLMClient
from core.prompt import EMOTION_INSTRUCTION
from state.local_store import JsonRepository


def _core(tmp_path, llm, path="s.json"):
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / path), canon="Ти — Лілі.", model="m"
    )


def test_turn_returns_emotion_state_and_persists_the_field(tmp_path):
    llm = MockLLMClient(states={"reply": "Радо!", "emotion": "joy", "intensity": 0.8})
    core = _core(tmp_path, llm)
    session = core.start_session()
    state = core.reply("привіт", session)
    assert (state.reply, state.emotion, state.intensity) == ("Радо!", Emotion.JOY, 0.8)
    lili = [m for m in core._repo.load_messages(session.id) if m.role == "lili"][-1]
    assert lili.text == "Радо!" and lili.emotion == "joy" and lili.intensity == 0.8


def test_malformed_state_is_repaired_in_the_turn(tmp_path):
    llm = MockLLMClient(states={"reply": "ок", "emotion": "ecstatic", "intensity": 9})
    core = _core(tmp_path, llm)
    state = core.reply("привіт", core.start_session())
    assert state.emotion is Emotion.CALM and state.intensity == 1.0


def test_missing_reply_surfaces_an_error(tmp_path):
    llm = MockLLMClient(states={"emotion": "joy", "intensity": 0.5})  # no reply
    core = _core(tmp_path, llm)
    with pytest.raises(EmotionError):
        core.reply("привіт", core.start_session())


def test_turn_system_prompt_carries_the_emotion_instruction(tmp_path):
    llm = MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.4})
    core = _core(tmp_path, llm)
    core.reply("привіт", core.start_session())
    assert EMOTION_INSTRUCTION in core.last_prompt["system"]


def test_emotion_field_round_trips_across_reload(tmp_path):
    path = tmp_path / "store.json"
    llm = MockLLMClient(states={"reply": "Сумно.", "emotion": "sad", "intensity": 0.3})
    core = Core(llm=llm, repository=JsonRepository(path), canon="Ти — Лілі.", model="m")
    session = core.start_session()
    core.reply("привіт", session)
    # Reopen the store from disk — the emotion field survives the round-trip.
    lili = [m for m in JsonRepository(path).load_messages(session.id) if m.role == "lili"][-1]
    assert lili.emotion == "sad" and lili.intensity == 0.3


def test_pre_v03_message_without_emotion_still_loads(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps(
            {
                "sessions": {
                    "s1": {"id": "s1", "user_id": "owner", "started_at": "2026-01-01T00:00:00+00:00"}
                },
                "messages": {
                    "s1": [
                        {
                            "session_id": "s1",
                            "user_id": "owner",
                            "role": "lili",
                            "text": "old",
                            "ts": "2026-01-01T00:00:00+00:00",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    m = JsonRepository(path).load_messages("s1")[0]
    assert m.text == "old" and m.emotion is None and m.intensity is None


def test_inline_emotion_tag_is_used_when_the_tool_is_absent(tmp_path):
    # thinking-on case: no structured emotion, just the reply with an <emotion> tag.
    llm = MockLLMClient(states={"reply": "Привіт! <emotion>joy 0.8</emotion>"})
    core = _core(tmp_path, llm)
    state = core.reply("привіт", core.start_session())
    assert state.reply == "Привіт!"  # tag stripped from the reply
    assert state.emotion is Emotion.JOY and state.intensity == 0.8


def test_tool_emotion_takes_precedence_over_the_tag(tmp_path):
    llm = MockLLMClient(
        states={"reply": "ок <emotion>sad 0.2</emotion>", "emotion": "joy", "intensity": 0.9}
    )
    core = _core(tmp_path, llm)
    state = core.reply("привіт", core.start_session())
    assert state.emotion is Emotion.JOY and state.intensity == 0.9
    assert state.reply == "ок"  # tag still stripped


def test_public_thinking_summary_populates_last_thinking(tmp_path):
    # When there is NO provider-native summary, the optional public thinking_summary fills the box.
    llm = MockLLMClient(states={
        "reply": "Привіт!",
        "emotion": "joy",
        "intensity": 0.8,
        "thinking_summary": "Зважила теплоту й відповіла коротко.",
    })
    core = _core(tmp_path, llm)
    state = core.reply("привіт", core.start_session())
    assert state.reply == "Привіт!"
    assert core.last_thinking == "Зважила теплоту й відповіла коротко."


def test_provider_native_thinking_wins_over_public_summary(tmp_path):
    # Opus-safety: a provider's REAL summarized thinking (Opus extended thinking / OpenAI
    # reasoning.summary) takes precedence over the optional public thinking_summary field, so Opus's
    # genuine inner monologue is never shadowed by a self-written one-liner.
    llm = MockLLMClient(
        thinking="REAL provider summary",  # what AnthropicClient sets from its thinking blocks
        states={"reply": "Привіт!", "emotion": "joy", "intensity": 0.8,
                "thinking_summary": "self-written one-liner"},
    )
    core = _core(tmp_path, llm)
    core.reply("привіт", core.start_session())
    assert core.last_thinking == "REAL provider summary"  # native wins, not the public field


def test_history_replays_the_emotion_tag_to_the_model(tmp_path):
    # The fix for "emotion works only at the beginning": Лілі's prior reply is
    # replayed WITH its <emotion> tag (from the stored field), so the model keeps
    # the pattern instead of imitating its own tag-less (stored) history.
    llm = MockLLMClient(
        states=[
            {"reply": "Привіт!", "emotion": "joy", "intensity": 0.8},
            {"reply": "Ще!", "emotion": "calm", "intensity": 0.5},
        ]
    )
    core = _core(tmp_path, llm)
    session = core.start_session()
    core.reply("привіт", session)
    core.reply("ще", session)
    second = llm.calls[1]["messages"]
    assistant = next(m for m in second if m["role"] == "assistant")
    # Timestamped (v0.4) + the reconstructed emotion tag.
    assert assistant["content"].startswith("[")
    assert assistant["content"].endswith("Привіт! <emotion>joy 0.8</emotion>")


def test_echoed_leading_timestamp_is_stripped_from_the_reply(tmp_path):
    llm = MockLLMClient(
        states={"reply": "[2026-06-07 17:06]Ха, привіт!", "emotion": "playful", "intensity": 0.7}
    )
    core = _core(tmp_path, llm)
    state = core.reply("привіт", core.start_session())
    assert state.reply == "Ха, привіт!"  # the leaked timestamp is gone


# --- v1.1: the additive `intent` field -----------------------------------------------


def _cs_core(tmp_path, llm, path="s.json"):
    return Core(
        llm=llm, repository=JsonRepository(tmp_path / path), canon="Ти — Лілі.", model="m",
        intent_enabled=True,
    )


def test_chosen_style_persists_on_lili_message(tmp_path):
    llm = MockLLMClient(
        states={"reply": "А що там далі?", "emotion": "calm", "intensity": 0.5,
                "intent": "deepen"}
    )
    core = _cs_core(tmp_path, llm)
    session = core.start_session()
    core.reply("привіт", session)
    msgs = core._repo.load_messages(session.id)
    lili = [m for m in msgs if m.role == "lili"][-1]
    user = [m for m in msgs if m.role == "user"][-1]
    assert lili.intent == "deepen" and core.last_intent == "deepen"
    assert user.intent is None  # user lines never carry a style


def test_unknown_style_is_dropped_silently(tmp_path):
    llm = MockLLMClient(
        states={"reply": "ок", "emotion": "calm", "intensity": 0.5, "intent": "заглибити"}  # not the EN enum
    )
    core = _cs_core(tmp_path, llm)
    session = core.start_session()
    state = core.reply("привіт", session)
    assert state.reply == "ок"  # the turn is never blocked
    lili = [m for m in core._repo.load_messages(session.id) if m.role == "lili"][-1]
    assert lili.intent is None and core.last_intent is None


def test_off_never_stores_a_value_and_skips_the_instruction(tmp_path):
    from core.prompt import INTENT_INSTRUCTION

    llm = MockLLMClient(
        states={"reply": "ок", "emotion": "calm", "intensity": 0.5, "intent": "deepen"}
    )
    core = _core(tmp_path, llm)  # intent_enabled defaults to False
    session = core.start_session()
    core.reply("привіт", session)
    lili = [m for m in core._repo.load_messages(session.id) if m.role == "lili"][-1]
    assert lili.intent is None and core.last_intent is None
    assert INTENT_INSTRUCTION not in core.last_prompt["system"]


def test_on_prompt_carries_the_instruction(tmp_path):
    from core.prompt import INTENT_INSTRUCTION

    llm = MockLLMClient(states={"reply": "ок", "emotion": "calm", "intensity": 0.5})
    core = _cs_core(tmp_path, llm)
    core.reply("привіт", core.start_session())
    assert INTENT_INSTRUCTION in core.last_prompt["system"]


def test_style_round_trips_across_reload(tmp_path):
    path = tmp_path / "store.json"
    llm = MockLLMClient(
        states={"reply": "Я думаю, ні.", "emotion": "serious", "intensity": 0.6,
                "intent": "position"}
    )
    core = Core(
        llm=llm, repository=JsonRepository(path), canon="Ти — Лілі.", model="m",
        intent_enabled=True,
    )
    session = core.start_session()
    core.reply("привіт", session)
    lili = [m for m in JsonRepository(path).load_messages(session.id) if m.role == "lili"][-1]
    assert lili.intent == "position"


def test_history_replays_the_style_beside_the_message(tmp_path):
    # The next turn's history carries Лілі's line together with its chosen style (from the
    # record's field) — replay-only metadata, the stored text stays clean.
    llm = MockLLMClient(
        states=[
            {"reply": "А що там далі?", "emotion": "calm", "intensity": 0.5,
             "intent": "deepen"},
            {"reply": "ок", "emotion": "calm", "intensity": 0.5},
        ]
    )
    core = _cs_core(tmp_path, llm)
    session = core.start_session()
    core.reply("привіт", session)
    core.reply("ще", session)
    lili_stored = [m for m in core._repo.load_messages(session.id) if m.role == "lili"][0]
    assert "<intent>" not in lili_stored.text  # stored text is clean — no inline tags
    assistant = next(m for m in llm.calls[1]["messages"] if m["role"] == "assistant")
    assert assistant["content"].endswith("<intent>deepen</intent>")  # rides beside the message


def test_unstyled_history_replays_byte_identically(tmp_path):
    llm = MockLLMClient(
        states=[
            {"reply": "Привіт!", "emotion": "joy", "intensity": 0.8},
            {"reply": "ок", "emotion": "calm", "intensity": 0.5},
        ]
    )
    core = _core(tmp_path, llm)  # off
    session = core.start_session()
    core.reply("привіт", session)
    core.reply("ще", session)
    assistant = next(m for m in llm.calls[1]["messages"] if m["role"] == "assistant")
    assert "<intent>" not in assistant["content"]
    assert assistant["content"].endswith("Привіт! <emotion>joy 0.8</emotion>")


def test_stray_style_marker_is_stripped_from_the_reply(tmp_path):
    llm = MockLLMClient(
        states={"reply": "Чекай, чому? <intent>object</intent>", "emotion": "calm", "intensity": 0.5}
    )
    core = _cs_core(tmp_path, llm)
    session = core.start_session()
    state = core.reply("привіт", session)
    assert state.reply == "Чекай, чому?"  # rendered/mirrored text carries no style
    lili = [m for m in core._repo.load_messages(session.id) if m.role == "lili"][-1]
    assert "<intent>" not in lili.text
    assert lili.intent == "object"  # the inline marker is the fallback channel


def test_inline_style_fallback_is_ignored_when_off(tmp_path):
    llm = MockLLMClient(
        states={"reply": "ок <intent>deepen</intent>", "emotion": "calm", "intensity": 0.5}
    )
    core = _core(tmp_path, llm)  # off
    session = core.start_session()
    state = core.reply("привіт", session)
    assert state.reply == "ок"  # still stripped (never leaks)…
    lili = [m for m in core._repo.load_messages(session.id) if m.role == "lili"][-1]
    assert lili.intent is None and core.last_intent is None  # …never stored


def test_pre_v11_message_without_style_still_loads(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps(
            {
                "sessions": {
                    "s1": {"id": "s1", "user_id": "owner", "started_at": "2026-01-01T00:00:00+00:00"}
                },
                "messages": {
                    "s1": [
                        {
                            "session_id": "s1",
                            "user_id": "owner",
                            "role": "lili",
                            "text": "old",
                            "ts": "2026-01-01T00:00:00+00:00",
                            "emotion": "joy",
                            "intensity": 0.8,
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    m = JsonRepository(path).load_messages("s1")[0]
    assert m.text == "old" and m.intent is None  # the v0.2-shim: no migration needed


def test_inline_intent_tag_is_captured_when_the_field_is_absent(tmp_path):
    # The Gemini case: the model fills reply/emotion/intensity but omits the optional
    # `intent` schema field, emitting the intent as an inline <intent> tag instead. It must
    # still be captured (and stripped from the reply).
    llm = MockLLMClient(
        states={"reply": "Розвину цю думку далі. <intent>develop</intent>",
                "emotion": "calm", "intensity": 0.5}
    )
    core = _cs_core(tmp_path, llm)
    session = core.start_session()
    state = core.reply("привіт", session)
    assert state.reply == "Розвину цю думку далі."  # tag stripped from the visible reply
    lili = [m for m in core._repo.load_messages(session.id) if m.role == "lili"][-1]
    assert lili.intent == "develop" and core.last_intent == "develop"


def test_intent_tag_inside_the_reasoning_is_captured(tmp_path):
    # The Gemini/thinking case: the model writes <intent> INSIDE its <think> reasoning (the
    # [арбітр] line), not the reply — split_reasoning lifts it into last_thinking, so the intent
    # must be recovered from there (the reply-side tag + the optional field are both absent).
    llm = MockLLMClient(
        states={"reply": "Розвину цю думку. <think>[арбітр] намір: develop <intent>develop</intent></think>",
                "emotion": "calm", "intensity": 0.5}
    )
    core = _cs_core(tmp_path, llm)
    session = core.start_session()
    state = core.reply("привіт", session)
    assert state.reply == "Розвину цю думку."  # the think block (with the tag) is stripped
    lili = [m for m in core._repo.load_messages(session.id) if m.role == "lili"][-1]
    assert lili.intent == "develop" and core.last_intent == "develop"


def test_legacy_move_field_migrates_to_intent_on_load(tmp_path):
    # A store written by an earlier build (the field was named `move`) must still load —
    # the value carries over to `intent`, and unknown keys are dropped, never a TypeError.
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps(
            {
                "sessions": {
                    "s1": {"id": "s1", "user_id": "owner", "started_at": "2026-01-01T00:00:00+00:00"}
                },
                "messages": {
                    "s1": [
                        {
                            "session_id": "s1", "user_id": "owner", "role": "lili", "text": "hi",
                            "ts": "2026-01-01T00:00:00+00:00", "emotion": "calm", "intensity": 0.5,
                            "move": "deepen",  # the old field name
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    m = JsonRepository(path).load_messages("s1")[0]
    assert m.text == "hi" and m.intent == "deepen"  # migrated, no crash
