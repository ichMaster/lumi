"""v4.2 LUMI-306 — ``Core.last_pictures``: the sandbox paths of the pictures she made or sent in this turn.

Pinned: a saved ``generate_image`` and a ``send_image`` the sink really took count, each path once and normalised;
a failed generation, an unsent picture and an error don't; the list resets every turn and doesn't depend on the
tool trace. The image model and the sink are fakes — no paid call."""

from __future__ import annotations

from core.agent import Core
from core.imagegen import ImageGenError
from core.llm import MockLLMClient
from state.local_store import JsonRepository

_STATE = {"reply": "ось", "emotion": "tender", "intensity": 0.6}


def _core(tmp_path, script, *, sink=None, gen=None, trace=False, show="path", signal=None):
    def ok(prompt, *, size=768):
        return b"\x89PNG\r\n\x1a\n-fake"

    return Core(llm=MockLLMClient(states=_STATE, tool_script=script), repository=JsonRepository(tmp_path / "s.json"),
                canon="C", model="m", files_dir=tmp_path / "files", user_id="owner", mood_enabled=False,
                closeness_enabled=False, thoughts_enabled=False, image_enabled=True, image_gen=gen or ok,
                image_show=show, image_signal_path=signal, telegram_sink=sink, tool_max_steps=6,
                file_tool_trace=trace)


def _picture(tmp_path, rel="art/cat.png"):
    f = tmp_path / "files" / "owner" / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"\x89PNG\r\n\x1a\n-cat")


def test_a_made_and_a_sent_picture_count_once_each_in_order(tmp_path):
    _picture(tmp_path)
    sent = []
    core = _core(tmp_path, [("generate_image", {"prompt": "дощ", "filename": "rain.png"}),
                            ("send_image", {"path": "art/cat.png"}),
                            ("send_image", {"path": "art/./cat.png"})],  # the same picture again, spelt otherwise
                 sink=lambda p, c: sent.append(p))
    core.reply("?", core.start_session())
    assert core.last_pictures == ["art/rain.png", "art/cat.png"]
    assert len(sent) == 2  # Telegram got both sends, as before


def test_a_name_with_spaces_keeps_its_whole_path(tmp_path):
    """Code review #1: the path was cut at the first space ("art/мій") — a broken picture in the chat, and the
    v0.23 display signal named a file that doesn't exist."""
    signal = tmp_path / "image.txt"
    core = _core(tmp_path, [("generate_image", {"prompt": "кіт", "filename": "мій кіт на даху.png"})],
                 show="path,viewer", signal=signal)
    core.reply("?", core.start_session())
    saved = tmp_path / "files" / "owner" / "art" / "мій кіт на даху.png"
    assert saved.is_file()
    assert core.last_pictures == ["art/мій кіт на даху.png"]
    assert signal.read_text(encoding="utf-8") == str(saved)


def test_created_path_reads_the_whole_path_or_nothing():
    from core.agent import _created_path

    assert _created_path("created art/cat.png (123 bytes)") == "art/cat.png"
    assert _created_path("created art/a (1) b.png (9 bytes)") == "art/a (1) b.png"
    assert _created_path("error: file already exists (no overwrite): 'art/cat.png'") is None
    assert _created_path("created art/cat.png") is None


def test_failures_and_unsent_pictures_do_not_count(tmp_path):
    _picture(tmp_path)

    def down(prompt, *, size=768):
        raise ImageGenError("down")

    core = _core(tmp_path, [("generate_image", {"prompt": "x"}), ("send_image", {"path": "art/cat.png"}),
                            ("send_image", {"path": "art/missing.png"}), ("send_image", {"path": "../out.png"})],
                 sink=None, gen=down)
    core.reply("?", core.start_session())
    assert core.last_pictures == []


def test_the_list_resets_every_turn_and_ignores_the_trace(tmp_path):
    for trace in (False, True):
        core = _core(tmp_path / str(trace), [("generate_image", {"prompt": "x", "filename": "one.png"})], trace=trace)
        session = core.start_session()
        core.reply("?", session)
        assert core.last_pictures == ["art/one.png"]
        core._llm = MockLLMClient(states=_STATE)  # the next turn calls no tool
        core.reply("ще", session)
        assert core.last_pictures == []
