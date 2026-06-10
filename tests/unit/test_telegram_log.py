"""The daemon logger — stderr + optional file, idempotent (v0.13)."""

import logging

from telegram import get_logger


def test_get_logger_writes_a_file(tmp_path):
    log = get_logger("inbound_test_file", tmp_path)
    log.info("flushed 2 message(s) → inbox id=7")
    for h in log.handlers:  # flush file handlers
        h.flush()
    logfile = tmp_path / "telegram-inbound_test_file.log"
    assert logfile.is_file()
    assert "flushed 2 message(s)" in logfile.read_text(encoding="utf-8")


def test_get_logger_is_idempotent(tmp_path):
    a = get_logger("idem_test", tmp_path)
    n = len(a.handlers)
    b = get_logger("idem_test", tmp_path)
    assert a is b and len(b.handlers) == n  # no stacked handlers on repeat


def test_get_logger_without_dir_has_no_file_handler():
    log = get_logger("stderr_only_test")  # no log_dir → stderr only
    assert log.handlers and not any(isinstance(h, logging.FileHandler) for h in log.handlers)
