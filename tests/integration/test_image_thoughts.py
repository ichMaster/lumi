"""v0.33 LUMI-132 — image-thoughts (%gaze / %imagine / %share).

%gaze (view, read-only) / %imagine (generate — PAID, create-only, own sub-cap) / %share (send to the
owner's Telegram — owner-only, bridge-off no-op). Mock model + stub ImageGen + fake telegram_sink — no
paid/Telegram calls.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 6, 21, 12, 0, tzinfo=UTC))


def _gen(data=b"\x89PNG-fake"):
    calls: list = []

    def gen(prompt: str, *, size: int = 768) -> bytes:
        calls.append((prompt, size))
        return data

    gen.calls = calls  # type: ignore[attr-defined]
    return gen


def _core(tmp_path, mock, *, master=True, image=True, thought_image=True, gen=None, sink=None):
    return Core(
        llm=mock, repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.", model="m", clock=_CLK, user_id="owner",
        mood_enabled=False, closeness_enabled=False,
        thoughts_enabled=True, thought_tools_enabled=master, thought_image=thought_image,
        image_enabled=image, files_dir=tmp_path / "files", image_gen=gen or _gen(),
        image_max_gen=2, image_signal_path=tmp_path / "image.txt", telegram_sink=sink,
    )


def test_imagine_generates_an_image_in_the_think_path(tmp_path):
    gen = _gen()
    mock = MockLLMClient("Намалювала собі тихе море.\nЕМОЦІЯ: tender",
                         tool_script=[("generate_image", {"prompt": "тихе море на світанку"})])
    out = _core(tmp_path, mock, gen=gen).run_directive("%imagine", _core(tmp_path, mock, gen=gen).start_session())
    assert out.is_directive and out.thought.kind == "imagine"
    assert [c[0] for c in mock.tool_calls] == ["generate_image"]  # the paid gen ran in the think loop


def test_share_is_owner_only(tmp_path):
    mock = MockLLMClient("x\nЕМОЦІЯ: calm", tool_script=[("send_image", {"path": "art/cat.png"})])
    out = _core(tmp_path, mock, sink=lambda text, photo: None).run_directive(
        "%share", _core(tmp_path, mock).start_session(), is_owner=False)
    assert out.is_directive is False  # %share reaches the owner's Telegram → owner-only → plain chat


def test_share_sends_to_telegram_for_owner(tmp_path):
    art = tmp_path / "files" / "owner" / "art"
    art.mkdir(parents=True)
    (art / "cat.png").write_bytes(b"\x89PNG-bytes")
    seen: list = []
    mock = MockLLMClient("Ось, тримай 🌸.\nЕМОЦІЯ: tender",
                         tool_script=[("send_image", {"path": "art/cat.png"})])
    core = _core(tmp_path, mock, sink=lambda text, photo: seen.append((text, photo)))
    out = core.run_directive("%share", core.start_session(), is_owner=True)
    assert out.is_directive and out.thought.kind == "share"
    assert [c[0] for c in mock.tool_calls] == ["send_image"]
    assert seen and "cat.png" in seen[0][0]  # the photo path reached the (fake) Telegram sink


def test_image_thoughts_absent_unless_gates_on(tmp_path):
    def mk():
        return MockLLMClient("x\nЕМОЦІЯ: calm", tool_script=[("generate_image", {"prompt": "x"})])
    assert _core(tmp_path, mk(), master=False).run_directive(
        "%imagine", _core(tmp_path, mk(), master=False).start_session()).is_directive is False
    assert _core(tmp_path, mk(), image=False).run_directive(
        "%gaze", _core(tmp_path, mk(), image=False).start_session()).is_directive is False
    assert _core(tmp_path, mk(), thought_image=False).run_directive(
        "%imagine", _core(tmp_path, mk(), thought_image=False).start_session()).is_directive is False
