"""v1.4 LUMI-191 — the full streamed path end-to-end: TUI → outbox (one record) → voicer; + off-pin."""

from core.agent import Core
from core.llm import MockLLMClient
from state import fifo
from state.local_store import JsonRepository
from tui.app import LumiApp
from voice.tts import MockTTS
from voice.voicer import voice_pending


def _core(tmp_path, llm, *, stream):
    return Core(llm=llm, repository=JsonRepository(tmp_path / "store.json"),
                canon="Ти — Лілі.", model="m", stream=stream)


async def _submit(pilot, app, text):
    app.query_one("#prompt").text = text
    await pilot.press("enter")
    for _ in range(80):
        await pilot.pause()
        if len(app.transcript) >= 2:
            break


async def test_streamed_turn_writes_one_outbox_record_and_voicer_speaks_sentences(tmp_path):
    # The whole v1.4 path: a streamed TUI turn writes EXACTLY ONE complete outbox record (Telegram-safe),
    # and the sentence-mode voicer speaks that one record as its sentences, in order.
    outbox = tmp_path / "outbox.jsonl"
    llm = MockLLMClient(states={"reply": "Привіт. Як ти сьогодні?", "emotion": "joy", "intensity": 0.6},
                        stream_chunk=3)
    app = LumiApp(_core(tmp_path, llm, stream=True))
    async with app.run_test() as pilot:
        app._voice = True                       # the TUI mirrors Лілі's reply to the outbox (for the voicer)
        app._outbox_path = outbox
        await _submit(pilot, app, "привіт")
        assert any("Привіт. Як ти сьогодні?" in ln for ln in app.transcript)  # rendered
        assert app.query_one("#live").display is False                        # live area cleared

    lili = [r for r in fifo.read_since(outbox, 0) if r.get("kind") != "user"]
    assert len(lili) == 1                                                      # ONE complete record
    assert lili[0]["text"] == "Привіт. Як ти сьогодні?"                        # the whole reply

    tts, played = MockTTS(), []
    assert voice_pending(outbox, tmp_path / "outbox.spoken", tts, played.append, sentences=True) == 1
    assert [t for t, _ in tts.calls] == ["Привіт.", "Як ти сьогодні?"]          # spoken sentence-by-sentence


async def test_streamed_and_blocking_write_identical_outbox_records(tmp_path):
    # Off-pin: the streamed turn's outbox record is byte-identical (text/kind/emotion) to a blocking turn's.
    canned = {"reply": "Однакова. Байт-у-байт.", "emotion": "calm", "intensity": 0.5}

    async def _record_for(stream: bool, sub: str) -> dict:
        d = tmp_path / sub
        d.mkdir()
        ob = d / "outbox.jsonl"
        app = LumiApp(_core(d, MockLLMClient(states=dict(canned)), stream=stream))
        async with app.run_test() as pilot:
            app._voice = True
            app._outbox_path = ob
            await _submit(pilot, app, "привіт")
        lili = [r for r in fifo.read_since(ob, 0) if r.get("kind") != "user"]
        assert len(lili) == 1
        return lili[0]

    streamed = await _record_for(True, "s")
    blocking = await _record_for(False, "b")
    for k in ("text", "kind", "emotion", "intensity"):
        assert streamed.get(k) == blocking.get(k)   # streaming changes nothing that reaches the outbox
