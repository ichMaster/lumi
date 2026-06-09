"""Prompt placeholders (v0.12) — one resolver expands ``{name}`` tokens to live state.

A small **fixed registry** (ARCHITECTURE §Prompt placeholders): authored prompts (canon,
`inner_voice.md`, `thought_request`, `nudges.md`) and directive topics (`%think about
{last_thought}`) may contain `{name}` tokens that the core expands at prompt-build time, so the
**author** decides placement. Unknown `{tokens}` are left **literal**; per-user tokens
(`last_thought`/`thoughts`) are **isolation-aware** (resolved via the user's surfacing read).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

_log = logging.getLogger("lumi.placeholders")

# The documented placeholder registry (the Core supplies a getter for each).
PLACEHOLDER_NAMES = frozenset({
    "last_thought", "thoughts", "mood", "closeness", "plan", "need",
    "recent", "now", "today", "user",
})

_TOKEN_RE = re.compile(r"\{([a-z_]+)\}")


def resolve_placeholders(text: str, resolvers: dict[str, Callable[[], str]]) -> str:
    """Expand ``{name}`` tokens via ``resolvers`` (name → zero-arg getter).

    A token whose name has **no** resolver is left **literal** (and logged); a resolver that
    returns empty substitutes empty (the token disappears); a getter that raises leaves the
    token literal. Deterministic — purely a function of ``text`` + the getters' values.
    """

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        getter = resolvers.get(name)
        if getter is None:
            _log.debug("unknown placeholder %r left literal", name)
            return match.group(0)  # unknown → literal
        try:
            return getter()
        except Exception:  # noqa: BLE001 — a bad getter never breaks the prompt
            _log.debug("placeholder %r failed; left literal", name)
            return match.group(0)

    return _TOKEN_RE.sub(_sub, text)
