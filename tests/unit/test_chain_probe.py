"""The chained voice probe's pure helpers — CLI, VAD, sentence assembly, Gemini body/SSE, stats.

No network, no audio hardware, no paid calls — run() is glue and stays uncovered by design (the
same discipline as test_realtime_probe.py).
"""
from __future__ import annotations

from core.streaming import StreamTagFilter
from scripts.chain_probe import (
    DEFAULT_BRAIN,
    EnergyVAD,
    SentenceAssembler,
    StageStats,
    build_gemini_body,
    clean_sentence,
    gemini_finish_reason,
    gemini_sse_delta,
    parse_cli,
    speakable,
    wav_wrap,
)


def test_parse_cli_defaults_and_overrides():
    d = parse_cli([])
    assert d["model"] is None and d["tts_model"] is None and d["whole"] is False
    d = parse_cli(["--model", "gemini-2.5-flash", "--tts-model", "eleven_flash_v2_5", "--whole"])
    assert (d["model"], d["tts_model"], d["whole"]) == ("gemini-2.5-flash", "eleven_flash_v2_5", True)


def test_energy_vad_start_then_stop_after_hangover():
    vad = EnergyVAD(start_peak=1500, keep_peak=800, hangover_s=0.8)
    assert vad.feed(200, 0.0) is None                 # silence — nothing
    assert vad.feed(2000, 0.1) == "start"             # speech begins
    assert vad.feed(1000, 0.5) is None                # still voiced (keep threshold)
    assert vad.feed(100, 0.9) is None                 # quiet, hangover not elapsed
    assert vad.feed(100, 1.4) == "stop"               # 0.9 s past last voice → end of turn
    assert vad.feed(100, 2.0) is None                 # back to idle — no repeat stop


def test_energy_vad_speech_resumes_within_hangover():
    vad = EnergyVAD(hangover_s=0.8)
    vad.feed(2000, 0.0)
    vad.feed(100, 0.5)                                 # a short pause…
    assert vad.feed(1200, 0.6) is None                 # …then he keeps talking — no stop
    assert vad.feed(100, 1.5) == "stop"                # 0.9 s after the resumed voice


def test_sentence_assembler_emits_whole_sentences_incrementally():
    a = SentenceAssembler()
    assert a.feed("Прив") == []                        # mid-word — nothing yet
    assert a.feed("іт. Як ти") == ["Привіт."]          # the first sentence completes
    assert a.feed(" там? Все") == ["Як ти там?"]
    assert a.flush() == ["Все"]                        # the unterminated tail flushes
    assert a.flush() == []                             # …once


def test_gemini_body_snapshot_history_and_thinking_off_for_flash():
    body = build_gemini_body("Ти — Лілі.", [("привіт", "привіт!")], "як ти?",
                             model="gemini-2.5-flash-lite")
    sysblock = body["systemInstruction"]["parts"][0]["text"]
    assert sysblock.startswith("[ГОЛОСОВИЙ РЕЖИМ")     # the voice-style block LEADS
    assert "<emotion>" in sysblock and "Ти — Лілі." in sysblock  # no-tags rule + the snapshot
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert body["contents"][-1]["parts"][0]["text"] == "як ти?"
    # flash family → thinking explicitly OFF (latency is the point)
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert "flash" in DEFAULT_BRAIN                    # the default brain gets the fast path
    # STRUCTURED output — constrained JSON leaves no room for a plain-text CoT preamble (the
    # live flash-lite runs produced three shapes of one; instructions alone did not stop it)
    gen = body["generationConfig"]
    assert gen["responseMimeType"] == "application/json"
    assert gen["responseSchema"]["required"] == ["reply"]


def test_gemini_body_omits_thinking_config_for_reasoning_models():
    body = build_gemini_body("x", [], "y", model="gemini-3.1-pro")
    assert "thinkingConfig" not in body["generationConfig"]  # budget 0 would 400 on pro


def test_gemini_body_carries_the_cores_permissive_safety_settings():
    # Without these Gemini's DEFAULT filter cut replies MID-SENTENCE (the live WS run) — the body
    # must ship the same BLOCK_NONE set the core sends (core/llm.py _GEMINI_SAFETY).
    body = build_gemini_body("x", [], "y", model="gemini-2.5-flash-lite")
    assert {s["threshold"] for s in body["safetySettings"]} == {"BLOCK_NONE"}
    assert len(body["safetySettings"]) == 4


def test_gemini_finish_reason_surfaces_only_cuts():
    assert gemini_finish_reason({"candidates": [{"finishReason": "RECITATION"}]}) == "RECITATION"
    assert gemini_finish_reason({"candidates": [{"finishReason": "SAFETY"}]}) == "SAFETY"
    assert gemini_finish_reason({"candidates": [{"finishReason": "STOP"}]}) is None  # normal end
    assert gemini_finish_reason({"candidates": [{}]}) is None                        # mid-stream
    assert gemini_finish_reason({}) is None


def test_gemini_sse_delta_extracts_text_and_skips_thoughts():
    chunk = {"candidates": [{"content": {"parts": [
        {"text": "мірку", "thought": True}, {"text": "Привіт"}, {"text": "!"},
    ]}}]}
    assert gemini_sse_delta(chunk) == "Привіт!"
    assert gemini_sse_delta({}) == ""                  # a keepalive/empty chunk is harmless


def test_wav_wrap_is_a_valid_wav_container():
    import io
    import wave

    pcm = b"\x00\x01" * 1600
    with wave.open(io.BytesIO(wav_wrap(pcm, 16_000)), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16_000)
        assert w.readframes(w.getnframes()) == pcm


def test_think_block_and_tags_never_reach_tts():
    # The exact shape of the first live run (2026-07-28): the inner-voice <think> block streamed
    # first, the reply carried <emotion>/<style> tags — she SPOKE all of it. The wiring is
    # StreamTagFilter → SentenceAssembler → clean_sentence; only pure prose may come out.
    deltas = [
        "<think>\n[ретроспектива]\nРозмова йде по колу.\n",
        "[арбітр]\nAssociate здається найприроднішим. <emotion>calm 0.6</emotion>\n</think>\n",
        "Мій настрій зараз — калейдоскоп. ",
        "А ти як? <emotion>calm 0.6</emotion> <style>допитлива</style>",
    ]
    filt, assembler = StreamTagFilter(), SentenceAssembler()
    spoken: list[str] = []
    for delta in deltas:
        for s in assembler.feed(filt.feed(delta)):
            spoken.append(clean_sentence(s))
    tail = filt.flush()
    if tail:
        assembler.feed(tail)
    spoken += [clean_sentence(s) for s in assembler.flush()]
    spoken = [s for s in spoken if s]
    assert spoken == ["Мій настрій зараз — калейдоскоп.", "А ти як?"]
    assert "ретроспектива" in filt.think            # the reasoning routed aside, not lost


def test_clean_sentence_strips_plain_emotion_trailer():
    assert clean_sentence("Добре.\nЕМОЦІЯ: calm 0.6") == "Добре."
    assert clean_sentence("  Просто речення.  ") == "Просто речення."
    assert clean_sentence("ЕМОЦІЯ: sad 0.9") == ""


def test_speakable_blocks_the_live_run_leaks():
    # Both leak shapes from the 2026-07-28 sessions: a bare English obscenity and an untagged
    # plain-text CoT block. Neither is governed by a flag — the gate keeps them off her voice.
    assert not speakable("motherfucker")
    assert not speakable("The user is asking if the word means anything to me.")
    assert not speakable("**Plan:** 1. Acknowledge the word.")
    assert not speakable("...")                                     # nothing pronounceable
    assert speakable("Привіт, як ти?")
    assert speakable("Я дивилась на Windows Defender сьогодні.")    # a Latin name mid-Ukrainian is fine
    assert speakable("Пічка — це hill-303.")


def test_stage_stats_summary_median_and_stages():
    s = StageStats()
    assert s.summary() == {"turns": 0}
    s.add(stt=0.4, llm=0.6, tts=0.3, total=1.3)
    s.add(stt=0.6, llm=0.8, tts=0.5, total=1.9)
    s.add(stt=0.5, llm=0.7, tts=0.4, total=1.6)
    out = s.summary()
    assert (out["turns"], out["median_s"], out["min_s"], out["max_s"]) == (3, 1.6, 1.3, 1.9)
    assert out["stages_median_s"] == {"stt": 0.5, "llm_first": 0.7, "tts_first": 0.4}
