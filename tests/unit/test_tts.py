"""The /voice TTS adapter — MockTTS + emotion bias, imports without the SDK (v0.14, LUMI-058)."""

from voice.tts import ElevenLabsTTS, MockTTS, voice_settings_for


def test_import_and_construct_without_elevenlabs():
    # the `elevenlabs` import is lazy (inside synth) → the module + adapter need no SDK installed
    tts = ElevenLabsTTS("sk-eleven-not-real", "voice123", "eleven_multilingual_v2")
    assert tts.voice_id == "voice123" and tts.model == "eleven_multilingual_v2"


def test_mock_tts_records_calls_and_returns_audio():
    tts = MockTTS()
    audio = tts.synth("привіт", emotion="joy")
    assert audio == b"AUDIO:" + "привіт".encode()
    assert tts.calls == [("привіт", "joy")]
    tts.synth("ще")
    assert tts.calls[-1] == ("ще", None)


def test_emotion_biases_delivery_total_over_unknown():
    calm = voice_settings_for("calm")
    playful = voice_settings_for("playful")
    assert calm != playful  # different feelings → different delivery
    assert playful[1] > calm[1]  # livelier → more style
    assert voice_settings_for("nonsense") == voice_settings_for(None)  # unknown → neutral middle
