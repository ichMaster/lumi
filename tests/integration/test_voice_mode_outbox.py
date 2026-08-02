"""v1.6.2 — a voice-mode turn (register="voice") must not double-speak.

LUMI-202's SpeechPipeline already speaks the reply live; mirroring it to the outbox too would let
the OLD local voicer daemon (v0.14, reading outbox.jsonl) replay the SAME reply a second time —
the double-audio bug reported live. Mock model, in-process TUI via Textual's Pilot harness — no
network, no paid calls, no real audio device.
"""
from core.agent import Core
from core.llm import MockLLMClient
from state.local_store import JsonRepository
from tui.app import LumiApp


def _app(tmp_path):
    core = Core(llm=MockLLMClient("Привіт!"), repository=JsonRepository(tmp_path / "store.json"),
               canon="Ти — Лілі.", model="m")
    return LumiApp(core)


async def test_voice_register_turn_is_not_mirrored_to_outbox(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app._voice = True  # simulate the old local voicer daemon also being enabled
        app._outbox_path = tmp_path / "outbox.jsonl"
        await app._run_turn("привіт", register="voice")
        await pilot.pause()
        assert not app._outbox_path.exists()  # the SpeechPipeline already spoke it — no duplicate


async def test_normal_turn_still_mirrors_to_outbox_for_the_old_voicer(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app._voice = True
        app._outbox_path = tmp_path / "outbox.jsonl"
        await app._run_turn("привіт")  # register=None — a normal text-mode turn
        await pilot.pause()
        assert app._outbox_path.exists()  # unrelated behavior stays exactly as before
        assert "Привіт!" in app._outbox_path.read_text(encoding="utf-8")


async def test_voice_stage_line_prints_and_feeds_the_latency_section(tmp_path):
    # LUMI-206: the three stamps assemble into one dim line per spoken turn + the /latency history.
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app._voice_stage = {"commit": 100.0, "endpoint_s": 0.7, "llm_ts": 100.9}
        app._note_voice_stages(101.2)                     # the first audio chunk landed at 101.2
        await pilot.pause()
        assert any("voice: endpoint 0.7s · llm first 0.9s · tts first 0.3s = 1.2s" in t
                   for t in app.transcript)
        assert round(app._voice_stages[-1]["total_s"], 2) == 1.2  # recorded for /latency
        assert app._voice_stage is None                   # one line per turn

        app._voice_stage = {"commit": None, "llm_ts": None}
        app._note_voice_stages(50.0)                      # missing stamps → silence, never an error
        assert len(app._voice_stages) == 1


async def test_a_failed_voice_turn_still_resets_the_pipeline_for_the_next_reply(tmp_path):
    # Live bug (earlier fix): finish_turn() ran only on the SUCCESS path in _run_turn, so a turn
    # that raised (EmotionError etc.) skipped it, leaving the StreamTagFilter/SentenceAssembler in
    # whatever half-parsed state the failed stream left them — corrupting the NEXT turn's parsing.
    # finish_turn() now runs in `finally`, unconditionally (pause/resume itself no longer depends
    # on turn success at all — only on interrupt()/resume(), see voice/stream_tts.py).
    from core.llm import MockLLMClient
    from voice.stream_tts import MockStreamTTS, SpeechPipeline

    core = Core(llm=MockLLMClient(""), repository=JsonRepository(tmp_path / "store.json"),
               canon="Ти — Лілі.", model="m")  # an empty reply → validate() raises EmotionError
    app = LumiApp(core)
    async with app.run_test() as pilot:
        app._voice_pipeline = SpeechPipeline(MockStreamTTS())
        stale_filt = app._voice_pipeline._filt

        await app._run_turn("привіт", register="voice")  # this turn RAISES (empty reply)
        await pilot.pause()

        assert app._voice_pipeline._filt is not stale_filt  # finish_turn() ran despite the failure
        app._voice_pipeline.feed_delta("Наступна репліка. ")  # a trailing space completes the sentence
        assert app._voice_pipeline.pending == 1            # …and the NEXT reply queues normally
