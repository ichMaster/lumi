"""The panel's own configuration (v2.10, LUMI-256): the instance registry + its token.

The admin folder (``LUMI_ADMIN_HOME``; on the box ``~/lumi/admin``, mounted at ``/admin``) holds:

- ``admin.yaml`` — the registry: each instance's folder (mounted read-only), server URL and compose project
  (**only the box's two, prod and dev**), and where the panel listens;
- ``secrets.yaml`` (``0600``) — ``admin.token``, the panel's own token (32+ characters);
- ``health.jsonl`` — the health history the panel writes (its only write).

:func:`load` validates both and refuses with a readable message naming the key, like the instance settings do.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from admin.readers import Instance

INSTANCES = ("prod", "dev")
MIN_TOKEN = 32
DEFAULT_PORT = 8767
HOME_ENV = "LUMI_ADMIN_HOME"
TOKEN_ENV = "LUMI_ADMIN_TOKEN"


class AdminConfigError(SystemExit):
    """The panel must not start on this configuration; ``str(exc)`` is the readable reason."""

    def __str__(self) -> str:
        return str(self.code)


@dataclass(frozen=True)
class AdminConfig:
    home: Path
    token: str
    instances: tuple[Instance, ...]
    host: str
    port: int

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
    if problems:
        raise AdminConfigError(f"Lumi admin — refusing to start ({home}):\n" + "\n".join(f"  • {p}" for p in problems))
    return AdminConfig(home=home, token=token, instances=tuple(instances), host=host, port=port)
