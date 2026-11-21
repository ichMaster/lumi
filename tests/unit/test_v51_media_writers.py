"""v5.1 LUMI-341 — the media ledger's writers: her voiced line (the server's voicer and the outbound daemon's old way),
the window's dictation, a Telegram voice note. Mock TTS / STT, fake bots and streams; no network, no paid call."""

from __future__ import annotations

import io
import json
import threading

from core import ledgers, usage_origin
from core.emotion import Emotion, EmotionState
from server.dictation import DictationSetup, relay
from server.voice import LineVoicer, VoiceStore, VoiceSwitch
from state import fifo
from state.bus import mirror_reply
from telegram.inbound import voice_to_text
from voice.stt import MockSTT, provider_of
from voice.tts import MockTTS

PCM = b"\x01\x00" * 160  # 10 ms of 16 kHz mono int16
STOP = json.dumps({"type": "stop"})


def _state(text):
    return EmotionState(reply=text, emotion=Emotion.CALM, intensity=0.5)


def _media(path):
    return ledgers.read_records(path) if path.exists() else []


# --- her voice on the server ----------------------------------------------------------------------------------------
class _ModelTTS(MockTTS):
    model = "eleven_flash_v2_5"


class _FailingTTS:
    model = "eleven_flash_v2_5"

    def synth(self, text, *, emotion=None):
        raise RuntimeError("quota exceeded")


def _voicer(tmp_path, tts):
    return LineVoicer(tmp_path / "outbox.jsonl", switch=VoiceSwitch(tmp_path / "voice.json", first=True,
                                                                    available=True),
                      store=VoiceStore(tmp_path / "voice"), tts=tts, user_id="owner",
                      ledger=tmp_path / ledgers.MEDIA_LEDGER)


def test_a_voiced_line_writes_one_tts_record_with_the_origin_of_its_turn(tmp_path):
    voicer = _voicer(tmp_path, _ModelTTS())
    with usage_origin.usage_origin("turn", "window"):  # the turn's thread queues the line…
        voicer.line(mirror_reply, _state("Привіт, як ти?"), None, text="Привіт, як ти?", emotion="tender")
    assert voicer.drain(5)  # …the worker thread voices it
    (rec,) = _media(tmp_path / ledgers.MEDIA_LEDGER)
    assert rec["service"] == "tts" and rec["provider"] == "elevenlabs" and rec["model"] == "eleven_flash_v2_5"
    assert rec["units"] == {"chars": len("Привіт, як ти?")} and rec["outcome"] == "ok"
    assert (rec["origin"], rec["channel"]) == ("turn", "window")  # captured at queue time, not the worker's empty
    assert "Привіт" not in (tmp_path / ledgers.MEDIA_LEDGER).read_text(encoding="utf-8")


def test_a_line_pushed_with_no_work_around_it_is_housekeeping(tmp_path):
    voicer = _voicer(tmp_path, _ModelTTS())
    worker = threading.Thread(target=voicer.line, args=(mirror_reply, _state("думка"), None),
                              kwargs={"text": "думка", "emotion": "calm"})
    worker.start()
    worker.join(5)
    assert voicer.drain(5)
    assert _media(tmp_path / ledgers.MEDIA_LEDGER)[0]["origin"] == "housekeeping:voice"


def test_a_failed_voice_is_recorded_and_the_line_still_goes_as_text(tmp_path):
    voicer = _voicer(tmp_path, _FailingTTS())
    voicer.line(mirror_reply, _state("Текст"), None, text="Текст", emotion="calm")
    assert voicer.drain(5)
    (rec,) = _media(tmp_path / ledgers.MEDIA_LEDGER)
    assert rec["outcome"] == "error:RuntimeError"
    (line,) = fifo.read_since(tmp_path / "outbox.jsonl", 0)
    assert line["text"] == "Текст" and line["voice"] is None


def test_a_broken_ledger_never_breaks_the_voice(tmp_path):
    (tmp_path / ledgers.MEDIA_LEDGER).mkdir()  # the ledger can't be written
    voicer = _voicer(tmp_path, _ModelTTS())
    voice_id = voicer.line(mirror_reply, _state("Ще раз"), None, text="Ще раз", emotion="joy")
    assert voicer.drain(5)
    (line,) = fifo.read_since(tmp_path / "outbox.jsonl", 0)
    assert line["voice"] == f"{voice_id}.mp3"


# --- the outbound daemon's old way ----------------------------------------------------------------------------------
async def test_an_old_record_voiced_by_the_daemon_is_recorded_as_housekeeping(tmp_path):
    from telegram.outbound import forward

    outbox = tmp_path / "outbox.jsonl"
    fifo.append(outbox, "привіт", kind="lili", emotion="joy")
    sent = []

    async def send_voice(ogg, caption):
        sent.append(caption)

    async def noop(*_):
        return None

    handled = await forward(outbox, tmp_path / "outbox.sent", batch=5, voice_dir=tmp_path / "voice",
                            send_text=noop, send_photo=noop, send_voice=send_voice,
                            to_ogg=lambda b: b, caption_for=lambda r: r["text"], tts=_ModelTTS(), voice_mode=True,
                            ledger=tmp_path / ledgers.MEDIA_LEDGER)
    assert handled == (1, False) and sent == ["привіт"]
    (rec,) = _media(tmp_path / ledgers.MEDIA_LEDGER)
    assert (rec["service"], rec["origin"], rec["channel"]) == ("tts", "housekeeping:voice", "telegram")
    assert rec["units"] == {"chars": len("привіт")}


# --- the window's dictation -----------------------------------------------------------------------------------------
def _client(script):
    items = list(script)

    async def receive():
        return items.pop(0) if items else None

    return receive


async def _dictate(tmp_path, script, *, stt=None, stream=None):
    sent = []

    async def send(message):
        sent.append(message)

    async def factory():
        return stream

    setup = DictationSetup(stt=stt or MockSTT("слова"), stream_factory=factory if stream else None, lang="uk",
                           provider="deepgram", model="nova-3", ledger=tmp_path / ledgers.MEDIA_LEDGER)
    return await relay(_client(script), send, setup, channel="window"), sent


async def test_a_dictation_writes_one_stt_record_with_its_seconds_never_its_words(tmp_path):
    outcome, _ = await _dictate(tmp_path, [PCM] * 100 + [STOP])  # 1 s of audio, recognized whole
    assert outcome == "done"
    (rec,) = _media(tmp_path / ledgers.MEDIA_LEDGER)
    assert (rec["service"], rec["provider"], rec["model"], rec["mode"]) == ("stt", "deepgram", "nova-3", "batch")
    assert rec["units"] == {"audio_s": 1.0} and rec["outcome"] == "ok"
    assert (rec["origin"], rec["channel"]) == ("turn", "window")
    assert "слова" not in (tmp_path / ledgers.MEDIA_LEDGER).read_text(encoding="utf-8")


async def test_a_failed_recognition_is_an_error_record(tmp_path):
    class Boom:
        def recognize(self, audio, *, lang="uk", content_type="audio/wav"):
            raise TimeoutError("slow")

    outcome, _ = await _dictate(tmp_path, [PCM] * 50 + [STOP], stt=Boom())
    assert outcome == "error"
    assert _media(tmp_path / ledgers.MEDIA_LEDGER)[0]["outcome"] == "error:TimeoutError"


async def test_a_dictation_without_audio_records_nothing(tmp_path):
    outcome, _ = await _dictate(tmp_path, [STOP])
    assert outcome == "done" and _media(tmp_path / ledgers.MEDIA_LEDGER) == []


# --- a Telegram voice note --------------------------------------------------------------------------------------------
class _Voice:
    file_id = "f1"
    duration = 7


class _File:
    file_path = "voice/file.ogg"


class _Bot:
    def __init__(self, boom=False):
        self.boom = boom

    async def get_file(self, file_id):
        if self.boom:
            raise RuntimeError("telegram down")
        return _File()

    async def download_file(self, path):
        return io.BytesIO(b"OGGDATA")


async def test_a_voice_note_writes_one_stt_record_with_telegram_s_duration(tmp_path):
    path = tmp_path / ledgers.MEDIA_LEDGER
    assert await voice_to_text(_Bot(), _Voice(), MockSTT("привіт"), ledger=path) == "привіт"
    (rec,) = _media(path)
    assert (rec["service"], rec["provider"], rec["mode"], rec["units"]) == ("stt", "mock", "batch", {"audio_s": 7.0})
    assert (rec["origin"], rec["channel"], rec["outcome"]) == ("turn", "telegram", "ok")
    assert "привіт" not in path.read_text(encoding="utf-8")


async def test_a_failed_note_recognition_is_recorded_and_a_failed_download_is_not(tmp_path):
    path = tmp_path / ledgers.MEDIA_LEDGER

    class Boom:
        def recognize(self, audio, *, lang="uk", content_type="audio/ogg"):
            raise ConnectionError("down")

    assert await voice_to_text(_Bot(), _Voice(), Boom(), ledger=path) == ""
    assert _media(path)[0]["outcome"] == "error:ConnectionError"
    assert await voice_to_text(_Bot(boom=True), _Voice(), MockSTT("x"), ledger=path) == ""
    assert len(_media(path)) == 1  # never recognized → nothing more


def test_provider_of_names_each_adapter():
    from voice.stt import DeepgramSTT, ElevenLabsScribeSTT

    assert provider_of(DeepgramSTT("k")) == "deepgram"
    assert provider_of(ElevenLabsScribeSTT("k")) == "elevenlabs"
    assert provider_of(MockSTT()) == "mock"
