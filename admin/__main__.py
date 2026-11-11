"""``python -m admin`` — the admin panel (v2.10): the registry + token, the Docker runner, the health loop, uvicorn.

Refuses before anything runs — like the server — on a bad configuration (its token, its registry) or a bind
outside LAN / Tailscale / loopback (inside the image the wildcard is allowed; compose publishes on the LAN address).
"""

from __future__ import annotations

import sys


def main() -> None:  # pragma: no cover - process glue (uvicorn, the real Docker socket); the app is tested directly
    from admin import health as hl
    from admin import readers as rd
    from admin.alerts import Alerts, TelegramSender
    from admin.app import create_app
    from admin.containers import DockerRunner
    from admin.registry import load
    from core.envguard import in_container
    from server.__main__ import bind_problem

    cfg = load()
    if problem := bind_problem(cfg.host, container=in_container()):
        raise SystemExit(f"Lumi admin — {problem}")
    runner = DockerRunner({i.name: i.project for i in cfg.instances})
    sender = TelegramSender(cfg.alerts.token, cfg.alerts.chat_id) if cfg.alerts.off_reason is None else None
    alerts = Alerts(cfg.alerts, sender, redact=lambda name, text: rd.redactor(cfg.instance(name))(text)
                    if cfg.instance(name) else text)
    health = hl.HealthRunner(list(cfg.instances), cfg.home / "health.jsonl",
                             services=lambda inst: runner.list(inst.name), on_run=alerts.observe)
    app = create_app(cfg, runner, health, alerts=alerts)
    import uvicorn

    print(f"Lumi admin on {cfg.host}:{cfg.port} — instances: {', '.join(i.name for i in cfg.instances)}",
          file=sys.stderr, flush=True)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
