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


async def test_a_failed_voice_turn_still_unmutes_the_pipeline_for_the_next_reply(tmp_path):
    # Live bug: finish_turn() (which lifts a barge-in's mute — voice/stream_tts.py) ran only on
    # the SUCCESS path. A turn that raised (EmotionError etc.) skipped it, so once ANY voice turn
    # had been interrupted and a LATER turn happened to fail, the mute never lifted again — every
    # reply after that went silently un-queued ("потім в наступному реплаї може бути звук, а може
    # і не бути"). finish_turn() now runs in `finally`, unconditionally.
    from core.llm import MockLLMClient
    from voice.stream_tts import MockStreamTTS, SpeechPipeline

    core = Core(llm=MockLLMClient(""), repository=JsonRepository(tmp_path / "store.json"),
               canon="Ти — Лілі.", model="m")  # an empty reply → validate() raises EmotionError
    app = LumiApp(core)
    async with app.run_test() as pilot:
        app._voice_pipeline = SpeechPipeline(MockStreamTTS())
        app._voice_pipeline.interrupt()  # simulate: an EARLIER turn was barge-in interrupted
        assert app._voice_pipeline._muted is True

        await app._run_turn("привіт", register="voice")  # this turn RAISES (empty reply)
        await pilot.pause()

        assert app._voice_pipeline._muted is False        # unmuted despite the failure
        app._voice_pipeline.feed_delta("Наступна репліка. ")  # a trailing space completes the sentence
        assert app._voice_pipeline.pending == 1            # …and the NEXT reply queues normally
