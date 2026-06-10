"""The Telegram bridge (v0.13) — two dumb daemons over the inbox/outbox file bus.

The TUI stays the only brain (it calls ``core.reply``); these daemons never touch the core. The
bot token is a secret (read from ``.env``, never logged or committed). ``aiogram`` is an optional
dependency, imported lazily inside each daemon's ``run()`` so the pure, testable logic imports
without it.
"""
