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
    monkeypatch.setattr(cm, "load_config", lambda: load_config(load_env=False))


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
