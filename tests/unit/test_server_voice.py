"""Her voice on the server (v4.6 LUMI-327) — the switch, the store, the ordered voice job, the 30-day expiry.

The mock TTS (``voice/tts.py`` ``MockTTS``) and fakes only; a temp data root, an injected clock; no network, no paid
call.
"""

import dataclasses
import os
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from core.agent import Core
from core.config import load_config
from core.emotion import Emotion, EmotionState
from core.llm import MockLLMClient
from server.__main__ import telegram_sink_of
from server.app import Outbox, create_app
from server.voice import (
    EXPIRE_MAX,
    VOICE_MAX_CHARS,
    VOICE_UNAVAILABLE,
    LineVoicer,
    VoiceSetup,
    VoiceStore,
    VoiceSwitch,
    VoiceUnavailable,
    voice_text,
)
from state import fifo
from state.bus import mirror_reply, mirror_thought, mirror_user
from state.local_store import JsonRepository
from voice.tts import MockTTS

ID = "0123456789abcdef0123456789abcdef"
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def _state(text="Привіт.", emotion=Emotion.TENDER, intensity=0.6):
    return EmotionState(reply=text, emotion=emotion, intensity=intensity)


def _voicer(tmp_path, *, tts=None, on=True, available=True, outbox=True, on_ready=None):
    switch = VoiceSwitch(tmp_path / "voice.json", first=on, available=available)
    return LineVoicer(tmp_path / "outbox.jsonl" if outbox else None, switch=switch,
                      store=VoiceStore(tmp_path / "voice"), tts=tts if tts is not None else MockTTS(),
                      user_id="owner", on_ready=on_ready)


def _records(tmp_path):
    return fifo.read_since(tmp_path / "outbox.jsonl", 0)


class SlowTTS:
    """Blocks in ``synth`` until released — the outbox order while a line is being voiced."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = []

    def synth(self, text, *, emotion=None):
        self.calls.append((text, emotion))
        self.started.set()
        assert self.release.wait(5)
        return b"MP3"


class FailingTTS:
    def __init__(self):
        self.calls = 0

    def synth(self, text, *, emotion=None):
        self.calls += 1
        raise RuntimeError("quota exceeded")


# --- the switch ---------------------------------------------------------------------------------------------

def test_the_switch_starts_from_telegram_voice_and_persists(tmp_path):
    path = tmp_path / "voice.json"
    assert VoiceSwitch(path, first=True, available=True).view() == {"enabled": True, "available": True}
    assert VoiceSwitch(path, first=False, available=True).enabled is False
    VoiceSwitch(path, first=True, available=True).set(False)
    again = VoiceSwitch(path, first=True, available=True)  # a restart: the file wins over the first value
    assert again.enabled is False and again.active is False
    again.set(True)
    assert VoiceSwitch(path, first=False, available=True).active is True


def test_an_unreadable_switch_file_falls_back_to_the_first_value(tmp_path):
    path = tmp_path / "voice.json"
    path.write_text("{not json", encoding="utf-8")
    assert VoiceSwitch(path, first=True, available=True).enabled is True
    path.write_text('{"enabled": "yes"}', encoding="utf-8")
    assert VoiceSwitch(path, first=False, available=True).enabled is False


def test_without_a_voice_the_switch_is_off_and_cant_be_turned(tmp_path):
    path = tmp_path / "voice.json"
    switch = VoiceSwitch(path, first=True, available=False)
    assert switch.view() == {"enabled": False, "available": False}
    with pytest.raises(VoiceUnavailable, match=VOICE_UNAVAILABLE):
        switch.set(True)
    assert not path.exists()  # nothing written


# --- the text and the store ---------------------------------------------------------------------------------

def test_the_voice_text_is_capped_at_a_sentence_or_a_space():
    assert voice_text("  Коротко.  ") == "Коротко."
    long = ("Речення одне. " * 400).strip()
    capped = voice_text(long)
    assert len(capped) <= VOICE_MAX_CHARS and capped.endswith(".")
    assert voice_text("слово " * 20, limit=17) == "слово слово"
    assert voice_text("x" * 50, limit=10) == "x" * 10


def test_the_store_names_a_line_by_32_hex_under_its_user(tmp_path):
    store = VoiceStore(tmp_path / "voice")
    assert store.path_for("owner", ID) == tmp_path / "voice" / "owner" / f"{ID}.mp3"
    for bad in ("", "abc", ID.upper(), ID + "0", "../" + ID[:29], f"{ID[:31]}g", None):
        with pytest.raises(ValueError):
            store.path_for("owner", bad)
    for user in ("", "..", ".hidden", "a/b", "a\\b"):
        with pytest.raises(ValueError):
            store.path_for(user, ID)
    store.write("owner", ID, b"MP3")
    assert store.read("owner", ID) == b"MP3"
    assert store.read("other", ID) is None  # another user's folder — never this file
    assert store.read("owner", "nope") is None
    assert [p.name for p in (tmp_path / "voice" / "owner").iterdir()] == [f"{ID}.mp3"]  # no temp left


def test_the_store_never_serves_a_link(tmp_path):
    store = VoiceStore(tmp_path / "voice")
    outside = tmp_path / "secret.mp3"
    outside.write_bytes(b"secret")
    (tmp_path / "voice" / "owner").mkdir(parents=True)
    (tmp_path / "voice" / "owner" / f"{ID}.mp3").symlink_to(outside)
    assert store.read("owner", ID) is None


# --- the expiry ---------------------------------------------------------------------------------------------

def _aged(path, days):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    t = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (t, t))
    return path


def test_the_expiry_removes_only_voice_files_older_than_30_days(tmp_path):
    root = tmp_path / "voice"
    old = _aged(root / "owner" / f"{'a' * 32}.mp3", 31)
    fresh = _aged(root / "owner" / f"{'b' * 32}.mp3", 29)
    other = _aged(root / "owner" / "notes.mp3", 90)  # not a voice file's name
    stray = _aged(root / "owner" / f".{'c' * 32}.mp3.tmp", 2)  # a temp file a crash left
    young_tmp = _aged(root / "owner" / f".{'d' * 32}.mp3.tmp", 0)
    outside = _aged(tmp_path / f"{'e' * 32}.mp3", 90)  # beside the voice folder — never touched
    (root / "linked").symlink_to(tmp_path)  # a link to a folder — never followed
    assert VoiceStore(root).expire(NOW) == 2
    assert not old.exists() and not stray.exists()
    assert fresh.exists() and other.exists() and young_tmp.exists() and outside.exists()


def test_the_expiry_is_bounded_per_pass(tmp_path):
    root = tmp_path / "voice"
    for n in range(7):
        _aged(root / "owner" / f"{n:032x}.mp3", 40)
    store = VoiceStore(root)
    assert store.expire(NOW, limit=3) == 3
    assert store.expire(NOW, limit=3) == 3
    assert store.expire(NOW, limit=3) == 1
    assert EXPIRE_MAX >= 100
    assert VoiceStore(tmp_path / "missing").expire(NOW) == 0


# --- the voice job --------------------------------------------------------------------------------------------

def test_one_generation_per_line_from_the_speech_with_its_emotion(tmp_path):
    ready = []
    tts = MockTTS()
    voicer = _voicer(tmp_path, tts=tts, on_ready=lambda vid, ok: ready.append((vid, ok)))
    vid = voicer.line(mirror_reply, _state(), "[sighs] Привіт.", text="[sighs] Привіт.", emotion="tender")
    assert voicer.drain(5)
    assert tts.calls == [("[sighs] Привіт.", "tender")]
    assert ready == [(vid, True)]
    [rec] = _records(tmp_path)
    assert rec["voice"] == f"{vid}.mp3" and rec["text"] == "Привіт." and rec["speech"] == "[sighs] Привіт."
    assert VoiceStore(tmp_path / "voice").read("owner", vid) == "AUDIO:[sighs] Привіт.".encode()


def test_a_long_line_is_capped_before_the_call(tmp_path):
    tts = MockTTS()
    voicer = _voicer(tmp_path, tts=tts)
    voicer.line(mirror_reply, _state("слово " * 1000), None, text="слово " * 1000, emotion="calm")
    voicer.drain(5)
    assert len(tts.calls[0][0]) <= VOICE_MAX_CHARS


def test_a_failed_generation_leaves_no_file_and_the_text_goes(tmp_path, caplog):
    ready = []
    tts = FailingTTS()
    voicer = _voicer(tmp_path, tts=tts, on_ready=lambda vid, ok: ready.append((vid, ok)))
    vid = voicer.line(mirror_reply, _state("Секретні слова."), None, text="Секретні слова.", emotion="calm")
    voicer.drain(5)
    assert tts.calls == 1 and ready == [(vid, False)]
    [rec] = _records(tmp_path)
    assert rec["voice"] is None and rec["text"] == "Секретні слова."
    assert not (tmp_path / "voice").exists() or not any((tmp_path / "voice").rglob("*"))
    assert "Секретні" not in caplog.text and f"voice: failed {vid}" in caplog.text


def test_with_the_switch_off_nothing_is_voiced_and_the_record_says_so(tmp_path):
    tts = MockTTS()
    voicer = _voicer(tmp_path, tts=tts, on=False)
    assert voicer.line(mirror_reply, _state(), None, text="Привіт.", emotion="tender") is None
    assert voicer.line(mirror_thought, "думка", "calm", text="думка", emotion="calm") is None
    assert tts.calls == []
    assert [r["voice"] for r in _records(tmp_path)] == [None, None]  # written at once, in the caller
    unavailable = _voicer(tmp_path / "u", tts=tts, on=True, available=False)
    assert unavailable.line(mirror_reply, _state(), None, text="x", emotion="calm") is None and tts.calls == []


def test_the_outbox_keeps_the_order_of_the_lines_while_one_is_voiced(tmp_path):
    tts = SlowTTS()
    voicer = _voicer(tmp_path, tts=tts)
    voicer.line(mirror_reply, _state("Перша."), None, text="Перша.", emotion="calm")
    assert tts.started.wait(5)
    voicer.write(mirror_user, "твій наступний рядок")  # queued behind her line, not written ahead of it
    voicer.write(mirror_reply, _state("Друга."), None, voice=None)
    assert _records(tmp_path) == []
    tts.release.set()
    assert voicer.drain(5)
    recs = _records(tmp_path)
    assert [(r["kind"], r["text"]) for r in recs] == [("lili", "Перша."), ("user", "твій наступний рядок"),
                                                      ("lili", "Друга.")]
    assert [r["id"] for r in recs] == [1, 2, 3]  # one writer — no id twice


def test_appends_from_many_threads_never_share_an_id(tmp_path):
    voicer = _voicer(tmp_path)
    lines = [threading.Thread(target=voicer.line, args=(mirror_reply, _state(f"{n}."), None),
                              kwargs={"text": f"{n}.", "emotion": "calm"}) for n in range(10)]
    writes = [threading.Thread(target=voicer.write, args=(mirror_user, f"u{n}")) for n in range(10)]
    for t in lines + writes:
        t.start()
    for t in lines + writes:
        t.join()
    assert voicer.drain(5)
    ids = [r["id"] for r in _records(tmp_path)]
    assert sorted(ids) == list(range(1, 21)) == ids


def test_without_an_outbox_the_file_is_still_made_for_the_window(tmp_path):
    voicer = _voicer(tmp_path, outbox=False)
    vid = voicer.line(mirror_reply, _state(), None, text="Привіт.", emotion="tender")
    voicer.drain(5)
    assert VoiceStore(tmp_path / "voice").read("owner", vid) == "AUDIO:Привіт.".encode()
    assert not (tmp_path / "outbox.jsonl").exists()


def test_after_close_a_line_is_written_unvoiced_in_the_caller(tmp_path):
    ready = []
    tts = MockTTS()
    voicer = _voicer(tmp_path, tts=tts, on_ready=lambda vid, ok: ready.append(ok))
    voicer.close()
    vid = voicer.line(mirror_reply, _state(), None, text="Привіт.", emotion="tender")
    assert tts.calls == [] and ready == [False]
    assert _records(tmp_path)[0]["voice"] is None and vid


# --- the server routes her lines through it -----------------------------------------------------------------

def _core(tmp_path):
    return Core(llm=MockLLMClient("x", states={"reply": "Я тут.", "emotion": "joy", "intensity": 0.7}),
                repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.", model="m",
                clock=lambda: NOW, mood_enabled=False)


def test_a_client_turn_is_voiced_once_and_its_record_names_the_file(tmp_path):
    tts = MockTTS()
    setup = VoiceSetup(tts=tts, root=tmp_path / "voice", switch_path=tmp_path / "voice.json", first=True)
    app = create_app(_core(tmp_path), token="t", outbox=Outbox(tmp_path / "outbox.jsonl", mirror_user=True),
                     voice=setup)
    TestClient(app).post("/v1/turn", json={"text": "привіт"}, headers={"Authorization": "Bearer t"})
    assert app.state.service.voicer.drain(5)
    assert tts.calls == [("Я тут.", "joy")]
    user, lili = _records(tmp_path)
    assert user["kind"] == "user" and "voice" not in user
    assert lili["voice"].endswith(".mp3") and (tmp_path / "voice" / "owner" / lili["voice"]).is_file()


def test_without_a_voice_setup_the_server_writes_voice_null_and_never_calls_tts(tmp_path):
    app = create_app(_core(tmp_path), token="t", outbox=Outbox(tmp_path / "outbox.jsonl"))
    TestClient(app).post("/v1/turn", json={"text": "привіт"}, headers={"Authorization": "Bearer t"})
    [lili] = _records(tmp_path)  # written at once, as before v4.6
    assert lili["voice"] is None and lili["text"] == "Я тут."


def test_the_photo_sink_writes_through_the_ordered_writer(tmp_path):
    cfg = dataclasses.replace(load_config(load_env=False), outbox_path=tmp_path / "o.jsonl", bridge=True, image=True)
    calls = []
    sink = telegram_sink_of(cfg, write=lambda fn, *args: calls.append((fn.__name__, args)))
    sink("/abs/pic.png", "ось")
    assert calls == [("mirror_photo", ("/abs/pic.png", "ось"))]
    assert not (tmp_path / "o.jsonl").exists()


def test_the_timer_measures_the_generation(tmp_path, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="lumi.voice")
    ticks = iter([0.0, 1.25])
    voicer = LineVoicer(tmp_path / "outbox.jsonl", switch=VoiceSwitch(None, first=True, available=True),
                        store=VoiceStore(tmp_path / "voice"), tts=MockTTS(), timer=lambda: next(ticks))
    vid = voicer.line(mirror_reply, _state("Привіт."), None, text="Привіт.", emotion="tender")
    voicer.drain(5)
    time.sleep(0)
    assert f"voice: made {vid} (7 chars, 1250 ms)" in caplog.text and "Привіт" not in caplog.text
