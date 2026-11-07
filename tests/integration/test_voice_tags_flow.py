"""v2.7.1 — her voice tags end to end: the core splits them off, the bus carries them to the voices only.

The model writes `[laughs]`-style tags (her palette, `core/voice_tags.md`); everything a reader sees or the
store keeps has none; the outbox record's `speech` carries them to the Telegram voice note and the voicer; a
model that reads tags aloud never gets one. Off (a voice model that doesn't perform tags, or no voice at all)
→ nothing changes. Mock model, mock TTS, fake SDK; no network, no paid API.
"""

import sys
import types

from fastapi.testclient import TestClient

from core.agent import Core, build_core
from core.config import DEFAULT_VOICE_TAGS_PATH, Config
from core.llm import MockLLMClient
from server.app import Outbox, create_app
from state import fifo
from state.bus import mirror_reply
from state.local_store import JsonRepository
from telegram.outbound import voice_to_telegram
from voice.tts import ElevenLabsTTS, MockTTS
from voice.voicer import voice_pending

SPEECH = "[starts laughing] Ага, звісно. [laughs] Я так і думала."
TEXT = "Ага, звісно. Я так і думала."
PALETTE = DEFAULT_VOICE_TAGS_PATH.read_text(encoding="utf-8").strip()


def _core(tmp_path, reply=SPEECH, *, tags=True, **kw):
    llm = MockLLMClient(states={"reply": reply, "emotion": "joy", "intensity": 0.8}, stream_chunk=3)
    core = Core(llm=llm, repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.", model="m",
                voice_tags=PALETTE if tags else None, **kw)
    return core, llm


def test_the_reply_and_the_store_never_show_a_tag_the_speech_keeps_them(tmp_path):
    core, llm = _core(tmp_path)
    session = core.start_session()
    state = core.reply("розкажи щось смішне", session)
    assert state.reply == TEXT and core.last_speech == SPEECH
    stored = [m.text for m in core._repo.load_messages(session.id)]
    assert stored[-1] == TEXT and all("[" not in t for t in stored)
    assert "[starts laughing]" in llm.calls[-1]["system"]  # her palette is in her prompt


def test_off_nothing_changes(tmp_path):
    core, llm = _core(tmp_path, tags=False)
    state = core.reply("привіт", core.start_session())
    assert state.reply == SPEECH and core.last_speech is None  # the brackets stay, as before v2.7.1
    assert "[starts laughing]" not in llm.calls[-1]["system"]


def test_a_reply_without_tags_has_no_separate_speech(tmp_path):
    core, _ = _core(tmp_path, reply="Просто слова.")
    assert core.reply("?", core.start_session()).reply == "Просто слова." and core.last_speech is None


def test_a_reply_of_tags_alone_still_has_words(tmp_path):
    core, _ = _core(tmp_path, reply="[sighs]")
    assert core.reply("?", core.start_session()).reply == "…" and core.last_speech == "[sighs]"


def test_a_streamed_reply_never_shows_a_tag(tmp_path):
    core, _ = _core(tmp_path, stream=True)
    shown: list[str] = []
    state = core.reply("?", core.start_session(), on_delta=shown.append)
    assert len(shown) > 1 and "[" not in "".join(shown) and "]" not in "".join(shown)
    assert state.reply == TEXT and core.last_speech == SPEECH


# --- on only while a voice speaks her replies on a model that performs tags --------------------------------

def _built(tmp_path, **cfg):
    llm = MockLLMClient(states={"reply": SPEECH, "emotion": "joy", "intensity": 0.8})
    core = build_core(config=Config(store_path=tmp_path / "s.json", **cfg), llm=llm,
                      repository=JsonRepository(tmp_path / "s.json"))
    core.reply("?", core.start_session())
    return core, llm.calls[-1]["system"]


def test_on_with_telegram_voice_on_a_tag_model(tmp_path):
    core, system = _built(tmp_path, telegram_voice=True, voice_model="eleven_v4_turbo")
    assert "[starts laughing]" in system and core.last_speech == SPEECH


def test_on_with_the_voicer_on_a_tag_model(tmp_path):
    core, system = _built(tmp_path, voice=True, voice_model="eleven_v3")
    assert "[starts laughing]" in system


def test_off_on_a_model_that_reads_tags_aloud(tmp_path):
    core, system = _built(tmp_path, telegram_voice=True, voice_model="eleven_turbo_v2_5")
    assert "[starts laughing]" not in system and core.last_speech is None


def test_off_without_a_voice(tmp_path):
    core, system = _built(tmp_path, voice_model="eleven_v4_turbo")  # no Telegram voice, no voicer
    assert "[starts laughing]" not in system and core.last_speech is None


# --- the bus carries the speech to the voices only --------------------------------------------------------

def test_the_outbox_record_carries_the_speech_only_when_there_is_one(tmp_path):
    core, _ = _core(tmp_path)
    state = core.reply("?", core.start_session())
    outbox = tmp_path / "outbox.jsonl"
    mirror_reply(outbox, state, core.last_speech)
    mirror_reply(outbox, state)
    first, second = fifo.read_since(outbox, 0)
    assert first["text"] == TEXT and first["speech"] == SPEECH
    assert "speech" not in second


async def test_the_telegram_voice_note_speaks_the_tags_and_its_caption_has_none(tmp_path):
    outbox, sent = tmp_path / "outbox.jsonl", tmp_path / "outbox.sent"
    fifo.append(outbox, TEXT, kind="lili", emotion="joy", speech=SPEECH)
    fifo.append(outbox, "Без позначок.", kind="lili", emotion="calm")
    tts, sent_msgs = MockTTS(), []

    async def send_voice(ogg, caption):
        sent_msgs.append(caption)

    await voice_to_telegram(outbox, sent, tts=tts, to_ogg=lambda b: b, send_voice=send_voice,
                            caption_for=lambda r: r["text"])
    assert [t for t, _ in tts.calls] == [SPEECH, "Без позначок."]  # the voice gets the tags
    assert sent_msgs == [TEXT, "Без позначок."]                      # the caption never does


def test_the_voicer_speaks_the_tags(tmp_path):
    outbox, spoken = tmp_path / "outbox.jsonl", tmp_path / "outbox.spoken"
    fifo.append(outbox, TEXT, kind="lili", emotion="joy", speech=SPEECH)
    tts = MockTTS()
    voice_pending(outbox, spoken, tts, lambda audio: None)
    assert tts.calls == [(SPEECH, "joy")]


def _fake_elevenlabs(monkeypatch) -> list[dict]:
    sent: list[dict] = []

    class _Client:
        def __init__(self, api_key):
            self.text_to_speech = self

        def convert(self, **kw):
            sent.append(kw)
            return [b"MP3"]

    root = types.ModuleType("elevenlabs")
    root.VoiceSettings = lambda **kw: kw
    client = types.ModuleType("elevenlabs.client")
    client.ElevenLabs = _Client
    monkeypatch.setitem(sys.modules, "elevenlabs", root)
    monkeypatch.setitem(sys.modules, "elevenlabs.client", client)
    return sent


def test_a_model_that_reads_tags_aloud_never_receives_one(monkeypatch):
    sent = _fake_elevenlabs(monkeypatch)
    ElevenLabsTTS("key", "voice", "eleven_turbo_v2_5").synth(SPEECH, emotion="joy")
    ElevenLabsTTS("key", "voice", "eleven_v4_turbo").synth(SPEECH, emotion="joy")
    assert [s["text"] for s in sent] == [TEXT, SPEECH]


# --- the server writes the speech for a client's turn -------------------------------------------------------

def test_a_client_turn_on_the_server_writes_the_speech_to_the_outbox(tmp_path):
    core, _ = _core(tmp_path)
    app = create_app(core, token="t", outbox=Outbox(tmp_path / "outbox.jsonl", mirror_user=True))
    res = TestClient(app).post("/v1/turn", json={"text": "?"}, headers={"Authorization": "Bearer t"}).json()
    assert res["reply"] == TEXT  # the client never sees a tag
    user, lili = fifo.read_since(tmp_path / "outbox.jsonl", 0)
    assert user["kind"] == "user" and lili["text"] == TEXT and lili["speech"] == SPEECH


def test_a_telegram_line_answered_by_the_server_carries_the_speech(tmp_path):
    import dataclasses

    from core.config import load_config
    from server.brain import BrainHost

    core, _ = _core(tmp_path)
    app = create_app(core, token="t", outbox=Outbox(tmp_path / "outbox.jsonl", mirror_user=True))
    cfg = dataclasses.replace(load_config(load_env=False), bridge=True, inbox_path=tmp_path / "inbox.jsonl",
                              outbox_path=tmp_path / "outbox.jsonl", scheduler=False, thoughts=False,
                              idle_nudge=False)
    fifo.append(tmp_path / "inbox.jsonl", "розсміши мене", source="telegram")
    host = BrainHost(app.state.service, cfg)
    host.inbox_tick(host._clock())
    [lili] = fifo.read_since(tmp_path / "outbox.jsonl", 0)
    assert lili["text"] == TEXT and lili["speech"] == SPEECH  # no echo of the line; her voice gets the tags
