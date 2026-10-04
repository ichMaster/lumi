"""Unit tests for the v2.1 data root — LUMI_HOME (LUMI-207).

One variable moves every mutable-state default; an explicit per-path env still wins; authored files
(canon, styles, schedule.toml, models.toml, faces …) are code and never follow it.
"""

from pathlib import Path

import pytest

from core.config import (
    DEFAULT_CANON_PATH,
    DEFAULT_CLOSENESS_PATH,
    DEFAULT_DATA_ROOT,
    DEFAULT_EMOJI_PATH,
    DEFAULT_MODELS_PATH,
    DEFAULT_NATAL_PATH,
    DEFAULT_SCHEDULE_PATH,
    DEFAULT_STYLES_PATH,
    load_config,
)

# Every env that could move a mutable path — cleared so each test sees exactly what it sets.
_PATH_ENVS = (
    "LUMI_HOME", "LUMI_STORE_PATH", "LUMI_FILES_DIR", "LUMI_JOURNAL_DIR",
    "LUMI_INBOX_PATH", "LUMI_OUTBOX_PATH", "LUMI_LISTEN_FLAG",
    "LUMI_CANON_PATH", "LUMI_STYLES_PATH", "LUMI_SCHEDULE_PATH",
)


@pytest.fixture
def clean_env(monkeypatch):
    for key in _PATH_ENVS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _mutable_paths(cfg) -> dict[str, Path]:
    return {
        "store": cfg.store_path,
        "files": cfg.files_dir,
        "journal": cfg.journal_dir,
        "inbox": cfg.inbox_path,
        "outbox": cfg.outbox_path,
        "listen": cfg.listen_flag_path,
        "schedule_state": cfg.schedule_state_path,
    }


def test_unset_home_is_the_in_repo_lumi(clean_env):
    cfg = load_config(load_env=False)
    assert cfg.data_root == DEFAULT_DATA_ROOT
    assert DEFAULT_DATA_ROOT.name == ".lumi"


def test_home_moves_every_mutable_default(clean_env, tmp_path):
    clean_env.setenv("LUMI_HOME", str(tmp_path / "data"))
    cfg = load_config(load_env=False)
    root = tmp_path / "data"
    assert cfg.data_root == root
    assert _mutable_paths(cfg) == {
        "store": root / "store.json",
        "files": root / "files",
        "journal": root / "journal",
        "inbox": root / "inbox.jsonl",
        "outbox": root / "outbox.jsonl",
        "listen": root / "listen.flag",
        "schedule_state": root / "schedule.state",
    }


def test_home_expands_the_tilde(clean_env):
    clean_env.setenv("LUMI_HOME", "~/lumi/prod/data")
    cfg = load_config(load_env=False)
    assert cfg.data_root == Path.home() / "lumi" / "prod" / "data"
    assert "~" not in str(cfg.store_path)


def test_blank_home_counts_as_unset(clean_env):
    clean_env.setenv("LUMI_HOME", "   ")
    assert load_config(load_env=False).data_root == DEFAULT_DATA_ROOT


@pytest.mark.parametrize(
    ("env", "attr", "name"),
    [
        ("LUMI_STORE_PATH", "store_path", "custom-store.json"),
        ("LUMI_FILES_DIR", "files_dir", "custom-files"),
        ("LUMI_JOURNAL_DIR", "journal_dir", "custom-journal"),
        ("LUMI_INBOX_PATH", "inbox_path", "custom-inbox.jsonl"),
        ("LUMI_OUTBOX_PATH", "outbox_path", "custom-outbox.jsonl"),
        ("LUMI_LISTEN_FLAG", "listen_flag_path", "custom.flag"),
    ],
)
def test_per_path_env_beats_home(clean_env, tmp_path, env, attr, name):
    clean_env.setenv("LUMI_HOME", str(tmp_path / "data"))
    clean_env.setenv(env, str(tmp_path / name))
    cfg = load_config(load_env=False)
    assert getattr(cfg, attr) == tmp_path / name  # the explicit override wins …
    others = {k: v for k, v in _mutable_paths(cfg).items() if v != tmp_path / name}
    assert all(p.is_relative_to(tmp_path / "data") for p in others.values())  # … the rest follow the root


def test_authored_files_never_follow_home(clean_env, tmp_path):
    clean_env.setenv("LUMI_HOME", str(tmp_path / "data"))
    cfg = load_config(load_env=False)
    assert cfg.canon_path == DEFAULT_CANON_PATH
    assert cfg.styles_path == DEFAULT_STYLES_PATH
    assert cfg.schedule_path == DEFAULT_SCHEDULE_PATH
    assert cfg.emoji_path == DEFAULT_EMOJI_PATH
    assert cfg.natal_path == DEFAULT_NATAL_PATH
    assert cfg.closeness_path == DEFAULT_CLOSENESS_PATH
    assert DEFAULT_MODELS_PATH.parent.name == "core"
    for path in (cfg.canon_path, cfg.styles_path, cfg.schedule_path):
        assert not path.is_relative_to(tmp_path)


def test_migrate_store_default_follows_the_configured_store(clean_env, tmp_path, monkeypatch):
    import scripts.migrate_store as ms

    clean_env.setenv("LUMI_HOME", str(tmp_path / "data"))
    seen: list[Path] = []
    monkeypatch.setattr(ms, "migrate", seen.append)
    monkeypatch.setattr("sys.argv", ["migrate_store.py"])
    ms.main()
    assert seen == [tmp_path / "data" / "store.json"]  # not the cwd-relative .lumi/store.json
