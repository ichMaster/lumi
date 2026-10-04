"""Contract: with nothing set, every mutable path is exactly today's in-repo ``.lumi`` path (LUMI-207).

The v2.1 data root must be additive — an install that never sets ``LUMI_HOME`` (or any per-path env)
reads and writes the very same files it did before, so the in-place behavior is byte-identical.
"""

from pathlib import Path

from core.config import (
    DEFAULT_FILES_DIR,
    DEFAULT_INBOX_PATH,
    DEFAULT_JOURNAL_DIR,
    DEFAULT_LISTEN_FLAG,
    DEFAULT_OUTBOX_PATH,
    DEFAULT_SCHEDULE_STATE_PATH,
    DEFAULT_STORE_PATH,
    load_config,
)

_REPO = Path(__file__).resolve().parents[2]

_ENVS = (
    "LUMI_HOME", "LUMI_STORE_PATH", "LUMI_FILES_DIR", "LUMI_JOURNAL_DIR",
    "LUMI_INBOX_PATH", "LUMI_OUTBOX_PATH", "LUMI_LISTEN_FLAG",
)


def test_unset_paths_are_literally_todays_in_repo_lumi(monkeypatch):
    for key in _ENVS:
        monkeypatch.delenv(key, raising=False)
    cfg = load_config(load_env=False)
    lumi = _REPO / ".lumi"
    # The literal pre-v2.1 values — pinned by value, not by re-deriving them from the new root.
    assert cfg.data_root == lumi
    assert cfg.store_path == lumi / "store.json"
    assert cfg.files_dir == lumi / "files"
    assert cfg.journal_dir == lumi / "journal"
    assert cfg.inbox_path == lumi / "inbox.jsonl"
    assert cfg.outbox_path == lumi / "outbox.jsonl"
    assert cfg.listen_flag_path == lumi / "listen.flag"
    assert cfg.schedule_state_path == lumi / "schedule.state"


def test_module_defaults_keep_their_pre_v2_1_values():
    lumi = _REPO / ".lumi"
    assert DEFAULT_STORE_PATH == lumi / "store.json"
    assert DEFAULT_FILES_DIR == lumi / "files"
    assert DEFAULT_JOURNAL_DIR == lumi / "journal"
    assert DEFAULT_INBOX_PATH == lumi / "inbox.jsonl"
    assert DEFAULT_OUTBOX_PATH == lumi / "outbox.jsonl"
    assert DEFAULT_LISTEN_FLAG == lumi / "listen.flag"
    assert DEFAULT_SCHEDULE_STATE_PATH == lumi / "schedule.state"
