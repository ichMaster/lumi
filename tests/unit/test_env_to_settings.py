"""v2.9 LUMI-247 — ``scripts/env_to_settings.py``: a ``.env`` → ``settings.yaml`` + ``secrets.yaml``.

A fixture ``.env`` covering every setting type, two secrets, an unknown key, an exempt name, an old name, an empty
value and a commented key; ``tmp_path`` only — no network, no paid call.
"""

from __future__ import annotations

import io
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import env_to_settings as mig  # noqa: E402

from core.config import load_config  # noqa: E402
from core.settings import read_yaml  # noqa: E402

SECRET_VALUES = ("sk-ant-FIXTURE-1234567890", "tg-FIXTURE-0987654321", "g-FIXTURE-old-name")

DOTENV = f"""# a fixture .env
ANTHROPIC_API_KEY={SECRET_VALUES[0]}
LUMI_TELEGRAM_TOKEN="{SECRET_VALUES[1]}"
GOOGLE_API_KEY={SECRET_VALUES[2]}
LUMI_ENV=dev
LUMI_SERVER_PORT=8766
LUMI_RAG=on
LUMI_MOOD=off
LUMI_RAG_FLOOR=0.25
LUMI_THINK_SHOW=OPEN
LUMI_VOICE_ID=abc123
LUMI_JOURNAL_DIR=~/journal
LUMI_WIKI_LANG=uk, en ,pl
LUMI_TELEGRAM_ALLOWLIST=111,222
LUMI_GEMINI_CACHE_TTL=5h
LUMI_QUIET_HOURS=23-7
LUMI_THOUGHTS_QUIET_HOURS=
LUMI_TELEGRAM_PHOTO=on
LUMI_STT_DEVICE=4
LUMI_MODEL=
LUMI_SOMETHING_OLD=1
LUMI_REALTIME_VOICE=marin
# LUMI_STREAM=on
"""


def _run(tmp_path: Path, *args: str, dotenv: str = DOTENV) -> tuple[int, str, str, Path]:
    env = tmp_path / ".env"
    if not env.exists():
        env.write_text(dotenv, encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    code = mig.main([str(env), *args], out=out, err=err)
    return code, out.getvalue(), err.getvalue(), env


def test_the_plan_sorts_types_secrets_and_leftovers(tmp_path):
    from core.settings import read_dotenv

    (tmp_path / ".env").write_text(DOTENV, encoding="utf-8")
    p = mig.plan(read_dotenv(tmp_path / ".env"))
    assert p.problems == []
    assert p.plain["recall.rag"] is True and p.plain["mood.enabled"] is False
    assert p.plain["server.port"] == 8766 and p.plain["recall.rag_floor"] == 0.25
    assert p.plain["persona.think_show"] == "open"  # the enum's canonical case
    assert p.plain["wiki.lang"] == ["uk", "en", "pl"] and p.plain["telegram.allowlist"] == [111, 222]
    assert p.plain["model.gemini_cache_ttl"] == "5h" and p.plain["nudge.quiet_hours"] == "23-7"
    assert p.plain["thoughts.quiet_hours"] == "off"  # set-but-empty = no quiet hours (not "inherit")
    assert p.plain["telegram.photo"] == 1.0 and p.plain["dictation.stt_device"] == "4"
    assert set(p.secret) == {"providers.anthropic_api_key", "telegram.token", "providers.gemini_api_key"}
    assert p.unknown == ["LUMI_SOMETHING_OLD"] and p.env_only == ["LUMI_REALTIME_VOICE"]
    assert p.empty == ["LUMI_MODEL"]
    assert "LUMI_STREAM" not in str(p)  # a commented key never moves


def test_a_dry_run_writes_nothing_and_shows_no_secret(tmp_path):
    code, out, err, env = _run(tmp_path)
    assert code == 0, err
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]  # nothing written, nothing renamed
    assert "dry run — nothing written" in out and "round-trip" in out
    assert "providers.anthropic_api_key  ← ANTHROPIC_API_KEY" in out  # the KEY is listed …
    for value in SECRET_VALUES:
        assert value not in out and value not in err  # … never its value
    assert "LUMI_SOMETHING_OLD" in out  # the unknown key is reported


def test_apply_writes_both_files_and_keeps_the_old_env(tmp_path, monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "LUMI_TELEGRAM_TOKEN", "LUMI_RAG"):
        monkeypatch.delenv(name, raising=False)
    before = load_config(load_env=False, settings_dir=_only_env(tmp_path))
    code, out, err, env = _run(tmp_path, "--apply")
    assert code == 0, err
    assert not env.exists() and (tmp_path / ".env.migrated").read_text(encoding="utf-8") == DOTENV
    secrets = tmp_path / "secrets.yaml"
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o600
    assert read_yaml(secrets)["providers"]["anthropic_api_key"] == SECRET_VALUES[0]
    assert "anthropic_api_key" not in (tmp_path / "settings.yaml").read_text(encoding="utf-8")
    for value in SECRET_VALUES:
        assert value not in out
    after = load_config(load_env=False, settings_dir=tmp_path)
    assert after == before  # the round trip: the same configuration
    assert after.gemini_api_key == SECRET_VALUES[2]  # the old name landed under its setting


def _only_env(tmp_path: Path) -> Path:
    folder = tmp_path / "old"
    folder.mkdir()
    (folder / ".env").write_text(DOTENV, encoding="utf-8")
    return folder


def test_an_invalid_value_aborts_with_nothing_written(tmp_path):
    code, out, err, env = _run(tmp_path, "--apply", dotenv="LUMI_MEMORY_WINDOW=lots\nLUMI_STORE_BACKEND=mongo\n")
    assert code == 1
    assert "memory.window" in err and "an integer" in err
    assert "store.backend" in err and "one of json, sqlite" in err
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


@pytest.mark.parametrize("existing", ["settings.yaml", "secrets.yaml", ".env.migrated"])
def test_an_existing_target_is_refused(tmp_path, existing):
    (tmp_path / existing).write_text("# already here\n", encoding="utf-8")
    code, out, err, env = _run(tmp_path, "--apply")
    assert code == 1 and "refusing" in err and existing in err
    assert env.exists()  # the .env is untouched


def test_out_writes_into_another_folder(tmp_path):
    dest = tmp_path / "instance"
    code, out, err, env = _run(tmp_path, "--apply", "--out", str(dest))
    assert code == 0, err
    assert (dest / "settings.yaml").exists() and (dest / "secrets.yaml").exists()
    assert (tmp_path / ".env.migrated").exists()


def test_a_missing_env_is_a_readable_refusal(tmp_path):
    err = io.StringIO()
    assert mig.main([str(tmp_path / "nope.env")], out=io.StringIO(), err=err) == 1
    assert "does not exist" in err.getvalue()


def test_the_round_trip_catches_a_difference(monkeypatch):
    # a broken renderer (it drops a setting) must be refused before anything is written
    assert mig.round_trip("LUMI_MEMORY_WINDOW=30\n", "memory:\n  window: 31\n", "") == ["memory_window"]
    assert mig.round_trip("LUMI_MEMORY_WINDOW=30\n", "memory:\n  window: 30\n", "") == []



def test_review_9_the_migrated_env_is_private_and_unknown_keys_are_said_to_stop_applying(tmp_path):
    env = tmp_path / ".env"
    env.write_text(DOTENV, encoding="utf-8")
    env.chmod(0o644)  # the Mac dev tree's .env was world-readable
    code, out, err, _ = _run(tmp_path, "--apply")
    assert code == 0, err
    assert stat.S_IMODE((tmp_path / ".env.migrated").stat().st_mode) == 0o600
    assert "no longer apply" in out and "LUMI_SOMETHING_OLD" in out



def test_review_12_a_failed_apply_keeps_nothing_and_the_env(tmp_path, monkeypatch):
    from core import settings as st

    real = st.write_atomic

    def fail_second(path, text, *, private=False):
        if not private:
            raise OSError(28, "No space left on device")
        return real(path, text, private=private)

    monkeypatch.setattr(st, "write_atomic", fail_second)
    code, out, err, env = _run(tmp_path, "--apply")
    assert code == 1 and "nothing kept" in err
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]  # no lone secrets.yaml, the .env in place
