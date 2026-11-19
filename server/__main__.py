"""`python -m server` — serve Лілі over the v2.2 API: localhost, one session, the client token.

Order matters: the token is checked first (the server never runs open), then the v2.1 env guard (the
server opens the data root), then the core. The ambient snapshot, today's mood and the recall backfill
run in the background — exactly what the in-process TUI does at mount — so serving starts at once. On
shutdown the current session is closed (its summary + facts). From v2.5 the brain host runs her time here
too — the scheduler, the fast tick, the idle think / nudge (``server/brain.py``).
"""

from __future__ import annotations

import errno
import ipaddress
import logging
import sys
import threading

log = logging.getLogger("lumi.server")

EX_CONFIG = 78  # v2.6: sysexits' EX_CONFIG — the unit's RestartPreventExitStatus, so a refusal never loops


class ConfigRefusal(SystemExit):
    """A refusal only an edit of the settings fixes (no token, a refused bind, a taken port) — the message on stderr,
    exit status 78, so systemd leaves the unit failed instead of restarting it every few seconds (v2.6)."""

    def __init__(self, message: str) -> None:
        super().__init__(EX_CONFIG)
        self.message = message

    def __str__(self) -> str:
        return self.message


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

    def on_exit() -> None:
        bus.close()  # end every events stream (v2.4)
        brain = getattr(app.state, "brain", None)
        if brain is not None:
            brain.request_stop()  # v2.5 review #1: her host starts no new act once the stop begins

    class _Server(uvicorn.Server):
        def handle_exit(self, sig, frame) -> None:
            # no locks inside a signal handler: the work is done from a helper thread
            threading.Thread(target=on_exit, daemon=True, name="lumi-exit").start()
            super().handle_exit(sig, frame)

    return _Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))


def term_as_interrupt() -> None:
    """v2.6: ``systemctl stop`` (SIGTERM) shuts her down like Ctrl+C. uvicorn re-raises a captured signal after
    its graceful stop with the previous handler restored; SIGTERM's default would kill the process before
    ``main``'s ``finally`` (brain stop + the session close: summary, facts). Handled as SIGINT, the re-raise is
    the ``KeyboardInterrupt`` :func:`serve` already swallows."""
    from core.stopsignal import sigterm_as_interrupt

    sigterm_as_interrupt()


def serve(app, *, host: str, port: int) -> None:  # pragma: no cover - the real signal loop
    term_as_interrupt()
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


# v2.6: where the server may listen — loopback, the home LAN (RFC 1918) and Tailscale (its CGNAT 100.64.0.0/10
# and its IPv6 ULA, inside fc00::/7). Never a wildcard or a public address: no exposure beyond LAN/Tailscale.
_BIND_NETS = tuple(ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "::1/128", "fc00::/7",
))
_BIND_FIX = "bind the box's LAN or Tailscale IP (or 127.0.0.1)"


def bind_problem(host: str, *, container: bool = False) -> str | None:
    """Why the server must not listen on ``host`` (v2.6), or ``None`` when it may — ``localhost``, loopback,
    a LAN (RFC 1918) or Tailscale (100.64.0.0/10, IPv6 ULA) address. A wildcard, a public address or any
    other hostname is refused: the server is reachable over LAN/Tailscale only, never from the internet.

    v2.8: inside the Lumi image (``container``) the wildcard is allowed — the container's own interfaces are
    private, and what reaches it is the compose publish rule's job (the box's LAN address only)."""
    name = (host or "").strip()
    if name.lower() == "localhost":
        return None
    try:
        ip = ipaddress.ip_address(name.strip("[]"))
    except ValueError:
        if not name:
            return f"LUMI_SERVER_HOST is empty — {_BIND_FIX}."
        return f"LUMI_SERVER_HOST={name} is a hostname — {_BIND_FIX} as an address."
    if ip.is_unspecified:
        if container:
            return None
        return f"LUMI_SERVER_HOST={name} listens on every interface — {_BIND_FIX} instead."
    if not any(ip in net for net in _BIND_NETS):
        return f"LUMI_SERVER_HOST={name} is a public address — {_BIND_FIX}; never expose her to the internet."
    return None


def bind_error(host: str, port: int) -> OSError | None:
    """Why ``host:port`` can't be bound right now — the way uvicorn binds (``SO_REUSEADDR``) — or ``None``."""
    import socket

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host.strip("[]"), port))
        except OSError as exc:
            return exc
    return None


def port_free(host: str, port: int) -> bool:
    """Whether ``host:port`` can be bound right now."""
    return bind_error(host, port) is None


def _listener_hint(port: int) -> str:
    """How to find what holds a port on this OS (the box is Linux, where ``lsof`` is often missing)."""
    if sys.platform.startswith("linux"):
        return f"ss -ltnp 'sport = :{port}'"
    return f"lsof -nP -iTCP:{port} -sTCP:LISTEN"


def check_port(host: str, port: int) -> None:
    """Refuse a port this instance can't take (review v2.4 #2: before the core — no model call). v2.6: a port
    another service holds is a configuration refusal (exit 78 — systemd doesn't restart it in a loop); an
    address not on this machine yet (the box just booted, Tailscale isn't up) is a plain failure, so systemd
    retries until it is."""
    err = bind_error(host, port)
    if err is None:
        return
    if err.errno == errno.EADDRNOTAVAIL:
        raise SystemExit(f"{host} is not an address of this machine (yet) — is Tailscale/the LAN up? "
                         "Under systemd the server retries until it is.")
    raise ConfigRefusal(
        f"Port {port} on {host} is already in use by another server ({err.strerror or err}). "
        f"Find it: {_listener_hint(port)} — stop it, or give this instance its own LUMI_SERVER_PORT "
        "(and the clients the same)."
    )


def outbox_of(cfg):
    """v2.5: the outbox the server writes — when the Telegram bridge or the voicer wants it (as in-process);
    with the bridge on, your client lines go to Telegram too."""
    from server.app import Outbox

    return Outbox(cfg.outbox_path, mirror_user=cfg.bridge) if (cfg.bridge or cfg.voice) else None


def thumbs_dir_of(cfg):
    """v4.2: the Gallery's thumbnail cache — ``<data root>/cache/thumbs`` (a folder per user below it), outside her
    sandbox so her file tools never see it. Made on the first thumbnail, never at start."""
    return cfg.data_root / "cache" / "thumbs"


def actions_log_of(cfg):
    """v4.5: the owner's action log — ``<data root>/actions.jsonl``, outside her sandbox (one line per action, never
    any content)."""
    return cfg.data_root / "actions.jsonl"


def telegram_sink_of(cfg):
    """The v0.24 ``send_image`` sink — a photo record in the server's outbox (bridge + image on), else None."""
    from state.bus import make_photo_sink

    return make_photo_sink(cfg.outbox_path) if (cfg.bridge and cfg.image) else None


def agora_problem(cfg) -> str | None:
    """v3.1: why the Agora space can't serve as configured (a refusal, exit 78) — or ``None`` (off, or ready)."""
    if not getattr(cfg, "agora", False):
        return None
    if not cfg.agora_token:
        return ("agora.enabled is on but agora.token is not set — the room needs its own token "
                "(agora.token in secrets.yaml).")
    if cfg.agora_token == cfg.server_token:
        return "agora.token equals the client token — the room's token must be its own, never the owner's."
    if not cfg.gemini_api_key:
        return ("agora.enabled is on but GEMINI_API_KEY is not set — the room's model is Gemini "
                "(providers.gemini_api_key in secrets.yaml).")
    return None


def agora_space_of(cfg, core):
    """v3.1: the Agora space over the core's canon and clock — its own store, its own Gemini client, the core's
    embedder shared behind one lock (wrapped only when the space is on). ``None`` when off."""
    if not getattr(cfg, "agora", False):
        return None
    from core.embedder import LockedEmbedder
    from core.space import AgoraSpace, build_space_llm
    from state.space_store import SqliteSpaceStore

    embedder = getattr(core, "_embedder", None)
    if embedder is not None and not isinstance(embedder, LockedEmbedder):
        embedder = core._embedder = LockedEmbedder(embedder)
    space = AgoraSpace(
        SqliteSpaceStore(cfg.agora_store_path), build_space_llm(cfg), model=cfg.agora_model, canon=core._canon,
        clock=core.clock, embedder=embedder, window=cfg.agora_window, timeout_s=cfg.agora_timeout_s,
        cache_log_path=(cfg.store_path.parent / "cache-log.jsonl") if cfg.cache_monitor else None,
        housekeeping_llm=build_space_llm(cfg),  # v3.2: the room's digest and gists on a client of their own
        today_lines=cfg.agora_today_lines, today_digest_words=cfg.agora_today_digest_words,
        gist_words=cfg.agora_gist_words,
        # v3.2: the space recipe — her day (a read of the core's mood), recall over the room in his recall's budget,
        # his real names read «Ich» in the room's prompt
        mood=lambda: core.mood, private_names=cfg.agora_private_names,
        recall_k=cfg.rag_k, recall_floor=cfg.rag_floor, recall_max_chars=cfg.rag_max_chars,
        max_turns_per_hour=cfg.agora_max_turns_per_hour,  # v3.3: the hourly cap → 429 (0 → no cap)
        # v3.4: her initiative — composed on a client of its own (never the turn's, never housekeeping's)
        initiative=cfg.agora_initiative, initiative_llm=build_space_llm(cfg))
    core.attach_space(space)  # v3.2: with him she knows the room (the owner's core — the space's one member)
    return space


SPACE_HOUSEKEEPING_S = 60.0  # v3.2: how often the room's digest and day gists are brought up to date


def start_space_housekeeping(space, clock, *, every_s: float = SPACE_HOUSEKEEPING_S):
    """v3.2: the room's housekeeping on a daemon thread of its own — ``space.housekeep(now)`` every ``every_s``. It
    never takes the service lock, the brain host or his core's client (the room's own housekeeping client does the
    calls). Returns the stop function (it waits briefly for a call in flight). ``None`` space → nothing runs."""
    if space is None:
        return lambda: None
    stop = threading.Event()

    def run() -> None:
        while not stop.wait(every_s):
            try:
                written = space.housekeep(clock())
                if written:
                    log.info("agora housekeeping: %s", ", ".join(written))
            except Exception:  # noqa: BLE001 — a beat never kills the room's housekeeping
                log.exception("agora housekeeping failed")

    thread = threading.Thread(target=run, daemon=True, name="lumi-agora-housekeeping")
    thread.start()

    def stopper(wait_s: float = 5.0) -> None:
        stop.set()
        thread.join(wait_s)

    return stopper


def startup_mood(service) -> None:
    """Today's mood at start, as the server's own act (review v2.5 #2): it never runs beside a turn or one of
    her acts (the shared model client's thinking flag can't flip under one), and listeners hear the result."""
    with service.background():
        service.core.ensure_mood()
        service.publish_state()


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


def close_space(space) -> None:
    """v3.1: close the room's store at the stop (its lines are already committed — nothing is summarized in v3.1).
    Best-effort, like the session close."""
    if space is None:
        return
    try:
        space.close()
    except Exception:  # noqa: BLE001
        log.warning("closing the Agora space failed", exc_info=True)


def main() -> None:  # pragma: no cover - process glue (uvicorn, the real model); the app is tested directly
    from core.config import load_config
    from core.envguard import guard_entry, in_container
    from core.settings import SettingsError

    try:
        cfg = load_config()
    except SettingsError as exc:  # v2.9: a wrong settings file is a configuration refusal — exit 78, not a loop
        raise ConfigRefusal(str(exc)) from None
    if not cfg.server_token:
        raise ConfigRefusal("LUMI_SERVER_TOKEN is not set — the server never runs without a client token "
                            "(server.token in secrets.yaml — see secrets.example.yaml).")
    # v2.6: LAN/Tailscale only — refused before anything runs; v2.8: the image may bind every interface
    if problem := bind_problem(cfg.server_host, container=in_container()):
        raise ConfigRefusal(problem)
    if problem := agora_problem(cfg):  # v3.1: the room's own token and model, or no start
        raise ConfigRefusal(problem)
    guard_entry(cfg)  # v2.1: refuse before touching the data root
    from core.brainlock import guard_single_brain

    guard_single_brain(cfg, "server")  # one brain per memory: never alongside ./lumi on the same root
    check_port(cfg.server_host, cfg.server_port)  # before the core — no model call
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
        raise SystemExit(f"{exc}\nSet the model key in secrets.yaml (see secrets.example.yaml).") from exc
    session = core.start_session()
    space = agora_space_of(cfg, core)  # v3.1: the room's own path (None when agora.enabled is off)
    app = create_app(core, token=cfg.server_token, session=session, env=cfg.env, version=cfg.app_version,
                     on_new_session=lambda: _refresh_world(core, cfg), outbox=outbox_of(cfg),
                     space=space, room_token=cfg.agora_token if space is not None else None,
                     thumbs_dir=thumbs_dir_of(cfg), actions_log=actions_log_of(cfg))
    service = app.state.service
    from server.brain import BrainHost

    brain = app.state.brain = BrainHost(service, cfg)  # v2.5: her acts run here, client or not
    _background("world", lambda: _refresh_world(core, cfg))
    _background("mood", lambda: startup_mood(service))  # her own act — never beside a turn
    _background("backfill", core.ensure_backfill)
    log.info("serving on %s:%s (env=%s version=%s)", cfg.server_host, cfg.server_port, cfg.env, cfg.app_version)
    print(f"Lumi server on http://{cfg.server_host}:{cfg.server_port} "
          f"({cfg.env or 'no env'} v{cfg.app_version or '?'}) — Ctrl+C to stop", flush=True)
    brain.start()
    stop_housekeeping = start_space_housekeeping(space, core.clock)  # v3.2: the room's digest and day gists
    try:
        serve(app, host=cfg.server_host, port=cfg.server_port)
    finally:
        brain.stop()
        stop_housekeeping()
        close_session(app.state.service)  # waits for a turn still in flight — never races it (review #1)
        close_space(space)  # v3.1: the room's store, beside his session


def run() -> None:
    """The server's one entry point — ``python -m server`` and ``./lumi-cli serve`` alike. A configuration refusal
    prints its message for the operator (the terminal, or the unit's journal) and exits 78 (EX_CONFIG) — a bare
    ``SystemExit(78)`` reaching the top would exit silently (v2.6 code review #2)."""
    try:
        main()
    except ConfigRefusal as refusal:
        print(refusal.message, file=sys.stderr, flush=True)
        raise SystemExit(EX_CONFIG) from None


if __name__ == "__main__":
    run()
