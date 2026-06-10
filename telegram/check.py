"""Pre-flight / health check for the Telegram bridge (v0.13).

Verifies the config is loaded and the bot **connects** (a `getMe` call, with a hard timeout so it
can never hang) — **without ever printing the token**. Mirrors the `-m telegram.inbound/outbound`
launch pattern, so paste it as one line:

    uv run python -m telegram.check
"""

from __future__ import annotations

import asyncio
import re
import sys

from core.config import load_config

_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]+$")  # BotFather format: 123456789:AA...


def main() -> int:
    cfg = load_config()
    token = cfg.telegram_token
    placeholder = token.startswith("REPLACE_ME")
    print("bridge enabled:  ", cfg.bridge)
    print("token set:       ", bool(token) and not placeholder)
    print("token format ok: ", bool(_TOKEN_RE.match(token)))
    print("allowlist:       ", cfg.telegram_allowlist or "(EMPTY — nobody is served)")

    if not token or placeholder:
        print("\n✗ Set LUMI_TELEGRAM_TOKEN in .env (from @BotFather).")
        return 1
    if not _TOKEN_RE.match(token):
        print("\n✗ Token doesn't look like a BotFather token (123456789:AA...). Re-copy it.")
        return 1
    if not cfg.telegram_allowlist:
        print("\n✗ Set LUMI_TELEGRAM_ALLOWLIST in .env (your @userinfobot id).")
        return 1

    async def _getme() -> int:
        from aiogram import Bot

        bot = Bot(token)
        try:
            me = await asyncio.wait_for(bot.get_me(), timeout=12)
            print(f"\n✓ Connected as @{me.username} (id {me.id}). Bridge is ready.")
            return 0
        except TimeoutError:
            print("\n✗ getMe timed out — can't reach api.telegram.org (network / firewall / region block?).")
            return 1
        except Exception as exc:  # noqa: BLE001 — surface any aiogram/Telegram error plainly
            print(f"\n✗ getMe failed: {type(exc).__name__} - {exc}")
            return 1
        finally:
            await bot.session.close()

    return asyncio.run(_getme())


if __name__ == "__main__":
    sys.exit(main())
