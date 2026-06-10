"""The bridge monitor — queue status, health verdict, snapshot render (v0.13)."""

from dataclasses import replace

from core.config import load_config
from state import fifo
from telegram.monitor import health, queue_status, render


def test_queue_status(tmp_path):
    q, pos = tmp_path / "q.jsonl", tmp_path / "q.pos"
    for t in ["a", "b", "c"]:
        fifo.append(q, t)
    assert queue_status(q, pos) == (3, 0, 3)  # 3 total, pointer 0, 3 pending
    fifo.save_pointer(pos, 2)
    assert queue_status(q, pos) == (3, 2, 1)  # 1 pending after advancing
    assert queue_status(tmp_path / "missing.jsonl", tmp_path / "x.pos") == (0, 0, 0)


def test_health_verdicts():
    assert health(0, 0, bridge=True).startswith("✓")
    assert "bridge OFF" in health(0, 0, bridge=False)
    assert "inbox not draining" in health(3, 0, bridge=True)
    assert "outbox not sending" in health(0, 5, bridge=True)
    both = health(2, 4, bridge=True)
    assert "inbox not draining" in both and "outbox not sending" in both


def test_render_snapshot(tmp_path):
    cfg = replace(
        load_config(),
        bridge=True,
        inbox_path=tmp_path / "inbox.jsonl",
        outbox_path=tmp_path / "outbox.jsonl",
    )
    fifo.append(cfg.inbox_path, "привіт")
    fifo.append(cfg.outbox_path, "о, привіт!", emotion="joy", intensity=0.8)
    fifo.save_pointer(cfg.outbox_path.with_suffix(".sent"), 1)  # outbox drained

    report = render(cfg, lines=4)
    assert "Lumi Telegram bridge" in report
    assert "inbox" in report and "outbox" in report
    assert "inbox not draining (1 unread" in report  # inbox has 1 unread (TUI not consuming here)
    assert "о, привіт!" in report  # recent outbox record shown
    assert "joy" in report
