"""The panel's own configuration (v2.10, LUMI-256): the instance registry + its token.

The admin folder (``LUMI_ADMIN_HOME``; on the box ``~/lumi/admin``, mounted at ``/admin``) holds:

- ``admin.yaml`` — the registry: each instance's folder (mounted read-only), server URL and compose project
  (**only the box's two, prod and dev**), and where the panel listens;
- ``secrets.yaml`` (``0600``) — ``admin.token``, the panel's own token (32+ characters), and (v2.11)
  ``alerts.telegram_token``, the **admin bot's** own BotFather token — never her prod or dev bot;
- ``health.jsonl`` / ``audit.jsonl`` — the health history and the audit log the panel writes.

``admin.yaml`` ``alerts:`` (v2.11) names the owner's Telegram ``chat_id`` and the ``instances`` that alert (prod by
default); without the chat id or the bot token, alerts are off and the page says why.

:func:`load` validates both and refuses with a readable message naming the key, like the instance settings do.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from admin.readers import Instance

INSTANCES = ("prod", "dev")
MIN_TOKEN = 32
DEFAULT_PORT = 8767
HOME_ENV = "LUMI_ADMIN_HOME"
TOKEN_ENV = "LUMI_ADMIN_TOKEN"
LOCK_ENV = "LUMI_AGENT_LOCK"


class AdminConfigError(SystemExit):
    """The panel must not start on this configuration; ``str(exc)`` is the readable reason."""

    def __str__(self) -> str:
        return str(self.code)


@dataclass(frozen=True)
class AlertsConfig:
    """Who the admin bot tells, about which instances; ``token`` is the admin bot's own (never shown)."""

    chat_id: int | None = None
    instances: tuple[str, ...] = ("prod",)
    token: str = field(default="", repr=False)

    @property
    def off_reason(self) -> str | None:
        if self.chat_id is None:
            return "alerts are off — no alerts.chat_id in admin.yaml"
        if not self.token:
            return "alerts are off — no alerts.telegram_token (the admin bot's token) in the admin secrets.yaml"
        return None


@dataclass(frozen=True)
class AdminConfig:
    home: Path
    token: str = field(repr=False)
    instances: tuple[Instance, ...]
    host: str
    port: int
    alerts: AlertsConfig = AlertsConfig()
    agent_lock: Path | None = None  # v2.11: the deploy agent's lock, mounted read-only (LUMI_AGENT_LOCK)

    def instance(self, name: str) -> Instance | None:
        return next((i for i in self.instances if i.name == name), None)


def _yaml(path: Path) -> dict:
    from core.settings import SettingsError, parse_yaml

    try:
        data = parse_yaml(path.read_text(encoding="utf-8"), path.name)
    except FileNotFoundError:
        return {}
    except SettingsError as exc:
        raise AdminConfigError(f"Lumi admin — {exc}") from None
    if data is not None and not isinstance(data, dict):
        raise AdminConfigError(f"Lumi admin — {path} must be a mapping")
    return data or {}


def load(home: Path | None = None, environ: dict | None = None) -> AdminConfig:
    """The panel's configuration from its folder; refuses readably on any problem."""
    environ = os.environ if environ is None else environ
    home = Path(home or environ.get(HOME_ENV) or Path.home() / "lumi" / "admin").expanduser()
    problems: list[str] = []
    data = _yaml(home / "admin.yaml")
    raw = data.get("instances")
    instances: list[Instance] = []
    if not isinstance(raw, dict) or not raw:
        problems.append("admin.yaml: instances must name prod and/or dev (root, url, project each)")
        raw = {}
    for name, block in raw.items():
        if name not in INSTANCES:
            problems.append(f"admin.yaml: instances.{name} — only {' and '.join(INSTANCES)} are panel instances")
            continue
        if not isinstance(block, dict):
            problems.append(f"admin.yaml: instances.{name} must be a mapping of root, url, project")
            continue
        root, url, project = block.get("root"), block.get("url"), block.get("project")
        if not isinstance(root, str) or not root.strip():
            problems.append(f"admin.yaml: instances.{name}.root — the instance folder (e.g. /instances/{name})")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            problems.append(f"admin.yaml: instances.{name}.url — the server URL (e.g. http://192.168.1.197:8765)")
        if not isinstance(project, str) or not project.strip():
            problems.append(f"admin.yaml: instances.{name}.project — its compose project (e.g. lumi-{name})")
        if not any(p.startswith(f"admin.yaml: instances.{name}.") for p in problems):
            instances.append(Instance(name, Path(root).expanduser(), url.rstrip("/"), project))
    server = data.get("server") or {}
    host = str(environ.get("LUMI_ADMIN_HOST") or (server.get("host") if isinstance(server, dict) else None)
               or "127.0.0.1")
    port_raw = environ.get("LUMI_ADMIN_PORT") or (server.get("port") if isinstance(server, dict) else None)
    try:
        port = int(port_raw or DEFAULT_PORT)
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        problems.append(f"admin.yaml: server.port = {port_raw!r} is not valid — allowed: an integer 1..65535")
        port = DEFAULT_PORT
    secrets = _yaml(home / "secrets.yaml")
    block = secrets.get("admin") if isinstance(secrets.get("admin"), dict) else {}
    token = str(environ.get(TOKEN_ENV) or block.get("token") or "").strip()
    if len(token) < MIN_TOKEN:
        problems.append(f"secrets.yaml: admin.token is {'missing' if not token else 'too short'} — the panel never "
                        f"runs without a token of {MIN_TOKEN}+ characters "
                        "(python3 -c \"import secrets; print(secrets.token_urlsafe(32))\")")
    alerts = _alerts(data.get("alerts"), secrets.get("alerts"), problems)
    if problems:
        raise AdminConfigError(f"Lumi admin — refusing to start ({home}):\n" + "\n".join(f"  • {p}" for p in problems))
    lock = (environ.get(LOCK_ENV) or "").strip()
    return AdminConfig(home=home, token=token, instances=tuple(instances), host=host, port=port, alerts=alerts,
                       agent_lock=Path(lock) if lock else None)


def _alerts(block: object, secret: object, problems: list[str]) -> AlertsConfig:
    """``admin.yaml`` ``alerts: {chat_id, instances}`` + ``secrets.yaml`` ``alerts: {telegram_token}``."""
    if block is None:
        return AlertsConfig()
    if not isinstance(block, dict):
        problems.append("admin.yaml: alerts must be a mapping of chat_id, instances")
        return AlertsConfig()
    chat = block.get("chat_id")
    if chat is not None and (isinstance(chat, bool) or not isinstance(chat, int)):
        problems.append(f"admin.yaml: alerts.chat_id = {chat!r} is not valid — allowed: the owner's Telegram id "
                        "(an integer)")
        chat = None
    names = block.get("instances", ["prod"])
    if not isinstance(names, list) or not all(n in INSTANCES for n in names):
        problems.append(f"admin.yaml: alerts.instances = {names!r} is not valid — allowed: a list of "
                        + " / ".join(INSTANCES))
        names = ["prod"]
    token = secret.get("telegram_token") if isinstance(secret, dict) else None
    return AlertsConfig(chat_id=chat, instances=tuple(names), token=str(token or "").strip())
