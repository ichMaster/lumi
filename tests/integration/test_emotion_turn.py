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
