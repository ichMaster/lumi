"""The CLI (LUMI-214) — each subcommand against a TestClient-backed server; no sockets, no paid API."""

import io

import httpx
import pytest
from fastapi.testclient import TestClient

import cli.main as cm
from cli.main import main, mask_config
from core.agent import Core
from core.config import load_config
from core.llm import MockLLMClient
from server.app import create_app
from state.local_store import JsonRepository
from tui.remote import RemoteCore

TOKEN = "cli-test-token"


@pytest.fixture(autouse=True)
def env_only(monkeypatch):
    monkeypatch.setattr(cm, "load_config", lambda **kw: load_config(load_env=False, **kw))


def _api(tmp_path):
    core = Core(llm=MockLLMClient(states={"reply": "так", "emotion": "calm", "intensity": 0.5}),
                repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.",
                model="claude-haiku-4-5-20251001")
    app = create_app(core, token=TOKEN, env="dev", version="2.2.0")
    return RemoteCore("http://testserver", TOKEN, client=TestClient(app)), core


def _run(argv, api, *, ask=lambda q: "n"):
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, client=api, ask=ask, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_status(tmp_path):
    api, _ = _api(tmp_path)
    code, out, _ = _run(["status"], api)
    assert code == 0
    assert "dev v2.2.0" in out and "claude-haiku-4-5-20251001" in out and "turns 0" in out


def test_memory_show(tmp_path):
    api, _ = _api(tmp_path)
    code, out, _ = _run(["memory", "show"], api)
    assert code == 0 and "Memory is empty" in out


@pytest.mark.parametrize("answer", ["n", "", "no", "maybe"])
def test_memory_clear_never_clears_without_a_yes(tmp_path, answer):
    api, core = _api(tmp_path)
    cleared: list = []
    core.clear_memory = lambda *a, **k: cleared.append(1)  # observe the destructive call
    code, out, _ = _run(["memory", "clear"], api, ask=lambda q: answer)
    assert code == 0 and "Cancelled." in out and cleared == []


def test_memory_clear_with_a_yes(tmp_path):
    api, core = _api(tmp_path)
    cleared: list = []
    core.clear_memory = lambda *a, **k: cleared.append(1)
    asked: list = []
    code, out, _ = _run(["memory", "clear"], api, ask=lambda q: asked.append(q) or "y")
    assert code == 0 and cleared == [1] and "Memory cleared" in out
    assert asked and "[y/N]" in asked[0]


def test_model_and_model_set(tmp_path):
    api, _ = _api(tmp_path)
    code, out, _ = _run(["model"], api)
    assert code == 0 and "**Двигун:**" in out
    code, out, _ = _run(["model-set"], api)
    assert code == 0 and "Профілі" in out


def test_config_masks_every_secret(monkeypatch):
    # Hermetic: an earlier test's load_dotenv() may have put the developer's .env (its own server port)
    # into os.environ — pin the server keys this test asserts on.
    for key in ("LUMI_SERVER_PORT", "LUMI_SERVER_HOST"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SUPERSECRET")
    monkeypatch.setenv("LUMI_SERVER_TOKEN", "tok-SUPERSECRET")
    monkeypatch.setenv("LUMI_TELEGRAM_TOKEN", "123:SUPERSECRET")
    out = io.StringIO()
    assert main(["config"], out=out) == 0
    text = out.getvalue()
    assert "SUPERSECRET" not in text
    assert "server_token = •••set•••" in text and "server_port = 8765" in text


def test_mask_config_unset_secrets_say_so():
    masked = mask_config(load_config(load_env=False))
    assert all(v in ("•••set•••", "(unset)") for k, v in masked.items()
               if any(h in k.lower() for h in ("key", "token", "secret", "password")))


def test_server_down_is_one_readable_line_and_exit_1():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    api = RemoteCore("http://127.0.0.1:1", TOKEN,
                     client=httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://127.0.0.1:1"))
    code, out, err = _run(["status"], api)
    assert code == 1 and err.startswith("lumi: server unreachable") and "Traceback" not in err


def test_a_wrong_token_is_exit_1(tmp_path):
    api, _ = _api(tmp_path)
    api._auth = {"Authorization": "Bearer wrong"}
    code, _, err = _run(["memory", "show"], api)
    assert code == 1 and "rejected the token" in err


def test_no_token_configured_refuses(monkeypatch):
    monkeypatch.delenv("LUMI_SERVER_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="LUMI_SERVER_TOKEN"):
        main(["status"])


@pytest.mark.parametrize("interrupt", [EOFError, KeyboardInterrupt])
def test_memory_clear_with_no_terminal_or_ctrl_c_cancels(tmp_path, interrupt):
    # Code review #3: `./lumi-cli memory clear < /dev/null` (or Ctrl+C at the prompt) was a traceback.
    api, core = _api(tmp_path)
    cleared: list = []
    core.clear_memory = lambda *a, **k: cleared.append(1)

    def ask(question):
        raise interrupt

    code, out, err = _run(["memory", "clear"], api, ask=ask)
    assert code == 0 and "Cancelled." in out and cleared == [] and "Traceback" not in err


# --- v2.4: `think` — a thought fired from the CLI reaches every listening client ------------------------

def _thinking_api(tmp_path, **kw):
    core = Core(llm=MockLLMClient("хмари сьогодні пишуть листи\nЕМОЦІЯ: playful",
                                  states={"reply": "так", "emotion": "calm", "intensity": 0.5}),
                repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.", model="m",
                mood_enabled=False, **kw)
    app = create_app(core, token=TOKEN, env="dev", version="2.4.0")
    return RemoteCore("http://testserver", TOKEN, client=TestClient(app)), app


def test_think_prints_the_thought_and_every_listener_receives_it(tmp_path):
    api, app = _thinking_api(tmp_path)
    tui = app.state.service.bus.subscribe()  # a connected TUI
    code, out, _ = _run(["think", "про", "хмари"], api)
    assert code == 0 and out.strip() == "💭 хмари сьогодні пишуть листи"
    event, data = tui.get_nowait()
    assert event == "thought" and data["text"] == "хмари сьогодні пишуть листи" and data["kind"] == "think"


def test_think_with_the_thought_stream_off_says_so(tmp_path):
    api, app = _thinking_api(tmp_path, thoughts_enabled=False)
    tui = app.state.service.bus.subscribe()
    code, out, _ = _run(["think"], api)
    assert code == 0 and "LUMI_THOUGHTS" in out
    assert tui.empty()  # nothing to push


def test_serve_says_why_it_refuses_instead_of_exiting_silently(monkeypatch, capsys, env_only):
    """v2.6 code review #2: a configuration refusal is exit 78 for systemd — and still a readable line."""
    monkeypatch.delenv("LUMI_SERVER_TOKEN", raising=False)
    monkeypatch.setattr("core.config.load_config", lambda: load_config(load_env=False))
    with pytest.raises(SystemExit) as exc:
        main(["serve"])
    assert exc.value.code == 78
    assert "LUMI_SERVER_TOKEN is not set" in capsys.readouterr().err


# --- v2.9 LUMI-248: config get | set | validate over the settings files ---------------------------------------

import logging  # noqa: E402
import stat  # noqa: E402

from core.settings import read_yaml  # noqa: E402

SECRETS = {"ANTHROPIC_API_KEY": "sk-ant-CLI-SECRET-0001", "LUMI_TELEGRAM_TOKEN": "777:CLI-SECRET-0002",
           "LUMI_SERVER_TOKEN": "tok-CLI-SECRET-0003"}
SETTINGS_YAML = """# my settings — a hand comment that must survive
memory:
  window: 40          # tuned by hand
  compaction_batch: 20

recall:
  rag: true
"""
SECRETS_YAML = f"""providers:
  anthropic_api_key: {SECRETS["ANTHROPIC_API_KEY"]}
telegram:
  token: "{SECRETS["LUMI_TELEGRAM_TOKEN"]}"
"""


def _folder(tmp_path):
    (tmp_path / "settings.yaml").write_text(SETTINGS_YAML, encoding="utf-8")
    (tmp_path / "secrets.yaml").write_text(SECRETS_YAML, encoding="utf-8")
    (tmp_path / "secrets.yaml").chmod(0o600)
    return tmp_path


def _cfg(argv, tmp_path):
    out, err = io.StringIO(), io.StringIO()
    code = main(["config", *argv, "--dir", str(tmp_path)], out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_config_get_shows_the_value_and_where_it_comes_from(tmp_path, monkeypatch):
    for name in ("LUMI_MEMORY_WINDOW", "LUMI_RAG_K", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    _folder(tmp_path)
    assert _cfg(["get", "memory.window"], tmp_path)[1].startswith("memory.window = 40  (settings.yaml")
    assert "recall.rag_k = 4  (default" in _cfg(["get", "LUMI_RAG_K"], tmp_path)[1]  # the env name works too
    code, out, _ = _cfg(["get", "providers.anthropic_api_key"], tmp_path)
    assert code == 0 and "= •••set•••  (secrets.yaml" in out and SECRETS["ANTHROPIC_API_KEY"] not in out
    assert "= (unset)" in _cfg(["get", "providers.openai_api_key"], tmp_path)[1]
    monkeypatch.setenv("LUMI_MEMORY_WINDOW", "55")
    assert "memory.window = 55  (env" in _cfg(["get", "memory.window"], tmp_path)[1]


def test_config_set_validates_and_refuses_a_wrong_value_writing_nothing(tmp_path):
    _folder(tmp_path)
    before = (tmp_path / "settings.yaml").read_bytes()
    code, out, err = _cfg(["set", "persona.think_show", "loud"], tmp_path)
    assert code == 1 and "persona.think_show" in err and "one of debug, open, off" in err
    code, out, err = _cfg(["set", "server.port", "70000"], tmp_path)
    assert code == 1 and "an integer 1..65535" in err
    code, out, err = _cfg(["set", "memory.nonsense", "1"], tmp_path)
    assert code == 1 and "unknown setting" in err
    assert (tmp_path / "settings.yaml").read_bytes() == before  # byte for byte


def test_config_set_changes_only_its_line_and_keeps_the_comments(tmp_path, monkeypatch):
    monkeypatch.delenv("LUMI_MEMORY_WINDOW", raising=False)
    _folder(tmp_path)
    code, out, err = _cfg(["set", "memory.window", "50"], tmp_path)
    assert code == 0, err
    assert "memory.window = 50 → " in out and "restart needed" in out
    text = (tmp_path / "settings.yaml").read_text(encoding="utf-8")
    assert text == SETTINGS_YAML.replace("  window: 40          # tuned by hand", "  window: 50")
    assert load_config(load_env=False, settings_dir=tmp_path).memory_window == 50
    _cfg(["set", "recall.k", "7"], tmp_path)  # a new key in an existing group
    _cfg(["set", "wiki.lang", "uk,pl"], tmp_path)  # a new group
    data = read_yaml(tmp_path / "settings.yaml")
    assert data["recall"] == {"rag": True, "k": 7} and data["wiki"] == {"lang": ["uk", "pl"]}
    assert "# my settings — a hand comment that must survive" in (tmp_path / "settings.yaml").read_text()


def test_config_set_writes_a_secret_into_secrets_yaml_0600_without_echoing_it(tmp_path):
    _folder(tmp_path)
    code, out, err = _cfg(["set", "LUMI_SERVER_TOKEN", SECRETS["LUMI_SERVER_TOKEN"]], tmp_path)
    assert code == 0, err
    assert "server.token = •••set••• → " in out and "(0600)" in out
    assert SECRETS["LUMI_SERVER_TOKEN"] not in out + err
    assert read_yaml(tmp_path / "secrets.yaml")["server"]["token"] == SECRETS["LUMI_SERVER_TOKEN"]
    assert stat.S_IMODE((tmp_path / "secrets.yaml").stat().st_mode) == 0o600
    assert "token" not in (tmp_path / "settings.yaml").read_text(encoding="utf-8")


def test_config_set_into_an_empty_folder_creates_the_file(tmp_path):
    code, out, err = _cfg(["set", "server.port", "8766"], tmp_path)
    assert code == 0, err
    assert read_yaml(tmp_path / "settings.yaml") == {"server": {"port": 8766}}


def test_config_set_notes_an_environment_override(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMI_MEMORY_WINDOW", "55")
    assert "still wins over the file" in _cfg(["set", "memory.window", "50"], tmp_path)[1]


def test_config_validate(tmp_path):
    _folder(tmp_path)
    code, out, err = _cfg(["validate"], tmp_path)
    assert code == 0 and "are valid" in out
    (tmp_path / "settings.yaml").write_text("memory:\n  window: 0\n  future: 1\nserver:\n  port: x\n",
                                            encoding="utf-8")
    code, out, err = _cfg(["validate"], tmp_path)
    assert code == 1
    assert err.count("error: ") == 2 and "memory.window" in err and "server.port" in err


def test_config_validate_names_a_misplaced_secret_without_its_value(tmp_path):
    (tmp_path / "settings.yaml").write_text(f"providers:\n  anthropic_api_key: {SECRETS['ANTHROPIC_API_KEY']}\n",
                                            encoding="utf-8")
    code, out, err = _cfg(["validate"], tmp_path)
    assert code == 1 and "belongs in secrets.yaml" in err and SECRETS["ANTHROPIC_API_KEY"] not in out + err


def test_no_secret_in_any_config_output_or_log(tmp_path, monkeypatch, caplog):
    _folder(tmp_path)
    monkeypatch.setenv("LUMI_SERVER_TOKEN", SECRETS["LUMI_SERVER_TOKEN"])
    runs = [["show"], ["get", "providers.anthropic_api_key"], ["get", "telegram.token"], ["get", "server.token"],
            ["set", "providers.anthropic_api_key", SECRETS["ANTHROPIC_API_KEY"]],
            ["set", "telegram.token", SECRETS["LUMI_TELEGRAM_TOKEN"]], ["validate"], ["set", "server.port", "x"]]
    seen = []
    with caplog.at_level(logging.DEBUG):
        for argv in runs:
            code, out, err = _cfg(argv, tmp_path)
            seen.append(out + err)
    seen.append("\n".join(r.getMessage() for r in caplog.records))
    for value in SECRETS.values():
        assert all(value not in text for text in seen), value


def test_config_show_masks_a_secret_whatever_the_field_is_called(tmp_path):
    _folder(tmp_path)
    code, out, _ = _cfg(["show"], tmp_path)
    assert code == 0 and "api_key = •••set•••" in out and "telegram_token = •••set•••" in out
    assert SECRETS["ANTHROPIC_API_KEY"] not in out


# --- v2.9 code review #2: config set never rewrites a file ----------------------------------------------------

_SECTIONS = """schedule:
  - name: morning-brief
    enabled: false
models:
  aliases:
    fast: "anthropic:claude-haiku-4-5-20251001"
"""


@pytest.mark.parametrize("layout, key, value, expect", [
    ("telegram:\n  allowlist:\n    - 12345\n  batch: 5\n", "telegram.allowlist", "12345,678",
     {"allowlist": [12345, 678], "batch": 5}),                                 # a block list under the key
    ("memory:\n    window: 40\n", "memory.compaction_batch", "10",
     {"window": 40, "compaction_batch": 10}),                                  # a group indented by 4
    ("memory:\n    window: 40\n", "memory.window", "50", {"window": 50}),
])
def test_review_2_set_handles_the_layout_and_keeps_the_sections(tmp_path, layout, key, value, expect):
    text = "# my comment\n" + layout + _SECTIONS
    (tmp_path / "settings.yaml").write_text(text, encoding="utf-8")
    code, out, err = _cfg(["set", key, value], tmp_path)
    assert code == 0, err
    data = read_yaml(tmp_path / "settings.yaml")
    assert data[key.split(".")[0]] == expect
    assert data["schedule"] == [{"name": "morning-brief", "enabled": False}]
    assert data["models"] == {"aliases": {"fast": "anthropic:claude-haiku-4-5-20251001"}}
    assert (tmp_path / "settings.yaml").read_text(encoding="utf-8").startswith("# my comment\n")


def test_review_2_an_uneditable_layout_is_refused_and_nothing_written(tmp_path):
    text = "memory: {window: 40}\n" + _SECTIONS  # an inline group: no line to change
    (tmp_path / "settings.yaml").write_text(text, encoding="utf-8")
    code, out, err = _cfg(["set", "memory.window", "50"], tmp_path)
    assert code == 1 and "edit it by hand" in err and "nothing was written" in err
    assert (tmp_path / "settings.yaml").read_text(encoding="utf-8") == text
