"""`python -m server` — serve Лілі over the v2.2 API: localhost, one session, the client token.

Order matters: the token is checked first (the server never runs open), then the v2.1 env guard (the
server opens the data root), then the core. The ambient snapshot, today's mood and the recall backfill
run in the background — exactly what the in-process TUI does at mount — so serving starts at once. On
shutdown the current session is closed (its summary + facts). From v2.5 the brain host runs her time here
too — the scheduler, the fast tick, the idle think / nudge (``server/brain.py``).
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("lumi.server")


def _setup_logging(cfg) -> None:
    """``lumi.*`` → ``<data root>/lumi.log`` (the server is the brain now — it owns the log the TUI used)."""
    try:
        path = cfg.store_path.parent / "lumi.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        root = logging.getLogger("lumi")
        root.setLevel(logging.INFO)
        if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
            handler = logging.FileHandler(path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            root.addHandler(handler)
    except OSError:
        pass


def _refresh_world(core, cfg) -> None:
    """The ambient *now / here* snapshot (v0.4) — only when configured; best-effort, never raises."""
    if not (cfg.location or (cfg.lat is not None and cfg.lon is not None) or cfg.news_url):
        return
    try:
        from core.worldcontext import fetch_world_context

        core.set_world_context(fetch_world_context(
            core.clock, location=cfg.location, lat=cfg.lat, lon=cfg.lon, weather_url=cfg.weather_url,
            news_url=cfg.news_url, news_cap=cfg.news_cap,
        ))
    except Exception:  # noqa: BLE001 — ambient context is best-effort
        log.warning("ambient snapshot failed", exc_info=True)


def make_server(app, *, host: str, port: int):
    """uvicorn, but the first Ctrl+C also ends every ``/v1/events`` stream (v2.4) — a listening client
    must never hold the graceful stop open (uvicorn waits for open responses to finish first)."""
    import uvicorn

    bus = app.state.service.bus

    class _Server(uvicorn.Server):
        def handle_exit(self, sig, frame) -> None:
            # no locks inside a signal handler: the bus is closed from a helper thread
            threading.Thread(target=bus.close, daemon=True, name="lumi-events-close").start()
            super().handle_exit(sig, frame)

    return _Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))


def serve(app, *, host: str, port: int) -> None:  # pragma: no cover - the real signal loop
    try:
        make_server(app, host=host, port=port).run()
    except KeyboardInterrupt:  # uvicorn re-raises the captured signal after a clean stop
        pass


def _background(name: str, fn) -> None:
    def run() -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001 — startup housekeeping never takes the server down
            log.warning("%s failed", name, exc_info=True)

    threading.Thread(target=run, daemon=True, name=f"lumi-{name}").start()


def port_free(host: str, port: int) -> bool:
    """Whether ``host:port`` can be bound right now — the way uvicorn binds (``SO_REUSEADDR``)."""
    import socket

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def outbox_of(cfg):
    """v2.5: the outbox the server writes — when the Telegram bridge or the voicer wants it (as in-process);
    with the bridge on, your client lines go to Telegram too."""
    from server.app import Outbox

    return Outbox(cfg.outbox_path, mirror_user=cfg.bridge) if (cfg.bridge or cfg.voice) else None


def telegram_sink_of(cfg):
    """The v0.24 ``send_image`` sink — a photo record in the server's outbox (bridge + image on), else None."""
    from state.bus import make_photo_sink

    return make_photo_sink(cfg.outbox_path) if (cfg.bridge and cfg.image) else None


SHUTDOWN_WAIT_S = 30.0  # how long a stop waits for an in-flight turn before leaving the session open


def close_session(service, *, wait_s: float = SHUTDOWN_WAIT_S) -> bool:
    """Close the current session at shutdown — never while a turn is still inside the core.

    A force stop (a second Ctrl+C) cancels the request, not the turn's runner thread, which keeps the
    service lock until the turn is done. So take that lock first: wait up to ``wait_s`` for the in-flight
    turn, then summarize + close. If it doesn't finish in time, leave the session open (logged) rather than
    run ``end_session`` alongside it. Returns whether the session was closed."""
    if not service.lock.acquire(blocking=False):
        print(f"Waiting up to {wait_s:.0f}s for the turn in flight to finish (Ctrl+C again to leave now)…",
              flush=True)
        if not service.lock.acquire(timeout=wait_s):
            log.warning("a turn was still running after %.0fs — the session is left open, not raced", wait_s)
            return False
    try:
        service.core.end_session(service.session)  # the CURRENT one (/session/new may have replaced it)
        log.info("stopped — session closed")
        return True
    except Exception:  # noqa: BLE001 — best-effort, like the TUI's quit
        log.warning("closing the session failed", exc_info=True)
        return False
    finally:
        service.lock.release()


def main() -> None:  # pragma: no cover - process glue (uvicorn, the real model); the app is tested directly
    from core.config import load_config
    from core.envguard import guard_entry

    cfg = load_config()
    if not cfg.server_token:
        raise SystemExit("LUMI_SERVER_TOKEN is not set — the server never runs without a client token "
                         "(see .env.example).")
    guard_entry(cfg)  # v2.1: refuse before touching the data root
    from core.brainlock import guard_single_brain

    guard_single_brain(cfg, "server")  # one brain per memory: never alongside ./lumi on the same root
    if not port_free(cfg.server_host, cfg.server_port):  # review v2.4 #2: before the core — no model call
        raise SystemExit(
            f"Port {cfg.server_port} on {cfg.server_host} is already in use by another server. "
            f"Find it: lsof -nP -iTCP:{cfg.server_port} -sTCP:LISTEN — stop it, or give this instance "
            "its own LUMI_SERVER_PORT."
        )
    try:
        import uvicorn  # noqa: F401 — fail early, with the hint, when the extra is missing

        from server.app import create_app
    except ImportError as exc:
        raise SystemExit("The server needs the `server` extra: uv sync --extra server") from exc
    from core.agent import build_core
    from core.llm import LLMError

    _setup_logging(cfg)
    try:
        core = build_core(config=cfg, telegram_sink=telegram_sink_of(cfg))
    except LLMError as exc:
        raise SystemExit(f"{exc}\nSet the model key in .env (see .env.example).") from exc
    session = core.start_session()
    app = create_app(core, token=cfg.server_token, session=session, env=cfg.env, version=cfg.app_version,
                     on_new_session=lambda: _refresh_world(core, cfg), outbox=outbox_of(cfg))
    service = app.state.service
    from server.brain import BrainHost

    brain = BrainHost(service, cfg)  # v2.5: her scheduled and idle acts run here, client or not
    _background("world", lambda: _refresh_world(core, cfg))
    _background("mood", lambda: (core.ensure_mood(), service.refresh_state()))  # v2.4: listeners hear it
    _background("backfill", core.ensure_backfill)
    log.info("serving on %s:%s (env=%s version=%s)", cfg.server_host, cfg.server_port, cfg.env, cfg.app_version)
    print(f"Lumi server on http://{cfg.server_host}:{cfg.server_port} "
          f"({cfg.env or 'no env'} v{cfg.app_version or '?'}) — Ctrl+C to stop", flush=True)
    brain.start()
    try:
        serve(app, host=cfg.server_host, port=cfg.server_port)
    finally:
        brain.stop()
        close_session(app.state.service)  # waits for a turn still in flight — never races it (review #1)


if __name__ == "__main__":
    main()
