"""v2.9 (LUMI-247) — migrate an instance's ``.env`` to ``settings.yaml`` + ``secrets.yaml``.

Reads a ``.env``, sorts every SET key into ``settings.yaml`` or ``secrets.yaml`` by the settings schema
(``core/settings.py``), converts each value to its type (``on`` → ``true``, ``uk,en`` → a list, …) and validates it.
Nothing is written without ``--apply``: the dry run prints the planned ``settings.yaml``, the secret KEYS going into
``secrets.yaml`` (never a value), the keys it can't place, and the round-trip check (the new files must load to the
same configuration as the old ``.env``). ``--apply`` writes both files (``secrets.yaml`` ``0600``) and renames the
``.env`` to ``.env.migrated`` (kept — the unknown keys stay readable there).

Usage:
    uv run python scripts/env_to_settings.py .env                    # dry run (the dev tree)
    uv run python scripts/env_to_settings.py .env --apply
    # on the box, with the image that will read the files (same Python, same schema):
    docker run --rm -v ~/lumi/dev:/instance ghcr.io/ichmaster/lumi:<tag> \\
        python scripts/env_to_settings.py /instance/.env --out /instance [--apply]

Refuses (exit 1, nothing written) on: an invalid value, an existing ``settings.yaml`` / ``secrets.yaml`` /
``.env.migrated`` in the target, or a round-trip difference.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # repo root on the path

from core import settings as st  # noqa: E402

MIGRATED = ".env.migrated"


@dataclasses.dataclass
class Plan:
    """What a migration would write: ``group.key → value`` per file, plus what it can't place."""

    plain: dict[str, object] = dataclasses.field(default_factory=dict)
    secret: dict[str, object] = dataclasses.field(default_factory=dict)
    unknown: list[str] = dataclasses.field(default_factory=list)  # names neither in the schema nor exempt
    env_only: list[str] = dataclasses.field(default_factory=list)  # exempt names — stay environment-only
    empty: list[str] = dataclasses.field(default_factory=list)  # set to nothing — left unset
    problems: list[str] = dataclasses.field(default_factory=list)


def plan(dotenv: dict[str, str]) -> Plan:
    """Sort a ``.env``'s set keys into the two files (pure: plain data in, a :class:`Plan` out)."""
    out = Plan()
    env = dict(dotenv)
    for s in st.SETTINGS:  # a deprecated name (GOOGLE_API_KEY) moves under its setting's key
        for old in s.old:
            if old in env:
                if s.alias not in env:
                    env[s.alias] = env[old]
                del env[old]
    for name, raw in env.items():
        s = st.BY_ALIAS.get(name)
        if s is None:
            (out.env_only if name in st.ENV_ONLY else out.unknown).append(name)
            continue
        if raw.strip() == "" and s.type not in ("hours", "probability"):
            out.empty.append(name)  # `KEY=` reads as the default everywhere it matters
            continue
        value = st.from_env(s, raw)
        if (p := st.problem(s, value, where=".env")) is not None:
            out.problems.append(f"{p} (env {name})")
            continue
        (out.secret if s.secret else out.plain)[s.key] = value
    return out


def _header(kind: str, source: Path) -> str:
    what = ("every non-secret setting" if kind == "settings" else "the API keys and tokens (keep it 0600)")
    return (f"# Lumi {kind} — {what}. Migrated from {source.name} on {date.today().isoformat()} by\n"
            "# scripts/env_to_settings.py. Every setting is described in settings.example.yaml /\n"
            "# secrets.example.yaml; change one with `lumi-cli config set <group.key> <value>`.\n")


def render(p: Plan, source: Path) -> tuple[str, str]:
    """The two files' text."""
    return (st.render_file(p.plain, _header("settings", source)),
            st.render_file(p.secret, _header("secrets", source)))


@contextlib.contextmanager
def _isolated_environ() -> Iterator[None]:
    """Hide every settings name from the process environment, so the round-trip compares the files alone."""
    names = [s.alias for s in st.SETTINGS] + [o for s in st.SETTINGS for o in s.old] + [st.SETTINGS_DIR_ENV]
    saved = {n: os.environ.pop(n) for n in names if n in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def round_trip(dotenv_text: str, settings_text: str, secrets_text: str) -> list[str]:
    """The ``Config`` fields that differ between the old ``.env`` and the new files ([] = the same)."""
    from core.config import load_config

    with tempfile.TemporaryDirectory() as tmp, _isolated_environ():
        old, new = Path(tmp, "old"), Path(tmp, "new")
        old.mkdir()
        new.mkdir()
        (old / st.DOTENV_FILE).write_text(dotenv_text, encoding="utf-8")
        (new / st.SETTINGS_FILE).write_text(settings_text, encoding="utf-8")
        st.write_private(new / st.SECRETS_FILE, secrets_text)
        a = load_config(load_env=False, settings_dir=old)
        b = load_config(load_env=False, settings_dir=new)
    return [f.name for f in dataclasses.fields(a) if getattr(a, f.name) != getattr(b, f.name)]


def main(argv: list[str] | None = None, out=None, err=None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    ap = argparse.ArgumentParser(description="Migrate a .env to settings.yaml + secrets.yaml (dry run by default).")
    ap.add_argument("env", help="the .env to migrate")
    ap.add_argument("--out", help="the folder to write the two files into (default: the .env's folder)")
    ap.add_argument("--apply", action="store_true", help="write the files and rename the .env to .env.migrated")
    args = ap.parse_args(argv)

    source = Path(args.env).expanduser()
    target = Path(args.out).expanduser() if args.out else source.parent
    if not source.is_file():
        print(f"env_to_settings: {source} does not exist — nothing to migrate", file=err)
        return 1
    existing = [n for n in (st.SETTINGS_FILE, st.SECRETS_FILE) if (target / n).exists()]
    if (source.parent / MIGRATED).exists():
        existing.append(str(source.parent / MIGRATED))
    if existing:
        print(f"env_to_settings: refusing — already there: {', '.join(existing)} (remove by hand to redo)", file=err)
        return 1

    text = source.read_text(encoding="utf-8")
    p = plan(st.read_dotenv(source))
    if p.problems:
        print("env_to_settings: refusing — nothing written:\n" + "\n".join(f"  • {x}" for x in p.problems), file=err)
        return 1
    settings_text, secrets_text = render(p, source)
    diff = round_trip(text, settings_text, secrets_text)
    if diff:
        print("env_to_settings: refusing — the new files would load differently from the .env in: "
              + ", ".join(diff), file=err)
        return 1

    print(f"# --- {target / st.SETTINGS_FILE} ({len(p.plain)} settings) ---", file=out)
    print(settings_text, file=out)
    print(f"# --- {target / st.SECRETS_FILE} (0600 — {len(p.secret)} keys, values not shown) ---", file=out)
    for key in p.secret:
        print(f"#   {key}  ← {st.BY_KEY[key].alias}", file=out)
    if p.unknown:
        print(f"\nunknown keys — NOT migrated: they no longer apply (kept for reference in {MIGRATED}): "
              + ", ".join(sorted(p.unknown)), file=out)
    if p.env_only:
        print(f"environment-only names — not settings, so no file applies them any more (set them in the environment "
              f"if you need them; kept for reference in {MIGRATED}): " + ", ".join(sorted(p.env_only)), file=out)
    if p.empty:
        print("set to nothing (left unset — the default): " + ", ".join(sorted(p.empty)), file=out)
    print("\nround-trip: the new files load to the same configuration as the .env ✓", file=out)
    if not args.apply:
        print("dry run — nothing written; rerun with --apply", file=out)
        return 0
    target.mkdir(parents=True, exist_ok=True)
    (target / st.SETTINGS_FILE).write_text(settings_text, encoding="utf-8")
    st.write_private(target / st.SECRETS_FILE, secrets_text)
    shutil.move(str(source), str(source.parent / MIGRATED))
    os.chmod(source.parent / MIGRATED, 0o600)  # it holds every secret, whatever mode the .env had (code review #9)
    print(f"wrote {target / st.SETTINGS_FILE} + {target / st.SECRETS_FILE} (0600); "
          f"{source.name} → {MIGRATED}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
