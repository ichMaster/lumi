"""Configuration and secrets.

Config is explicit and switchable (ARCHITECTURE §Configuration and secrets):
the active model id, the canon path, and the memory window live here, never
hardcoded in the core. Secrets (``ANTHROPIC_API_KEY`` from v0.1) are read from
the environment via a gitignored ``.env`` — never committed, never in code.

v0.1 has one backend — Anthropic Claude Haiku. More models become switchable
in v0.9 behind the same ``LLMClient`` seam, so ``provider`` is reserved here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Repo root = the parent of this ``core/`` package.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# A current Claude Haiku model id (the only model to start; more in v0.9).
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# Default canon path (config-referenced — never hardcoded in the core).
DEFAULT_CANON_PATH = _REPO_ROOT / "core" / "canon" / "lili.md"

# Local store file (gitignored runtime data, not source). user_id-keyed in v0.2.
DEFAULT_STORE_PATH = _REPO_ROOT / ".lumi" / "store.json"

# Rolling window: how many recent messages are kept verbatim in context. Older
# messages of the current session are folded into a running digest (compaction),
# in batches of DEFAULT_COMPACTION_BATCH — so the verbatim tail floats between
# memory_window and memory_window + batch.
DEFAULT_MEMORY_WINDOW = 40
DEFAULT_COMPACTION_BATCH = 20

# Model output cap. Extended thinking (Opus 4.8 / Sonnet 4.6) is adaptive and
# off by default; `effort` tunes its depth when on (None → the API default).
DEFAULT_MAX_TOKENS = 4096
DEFAULT_THINKING = False
DEFAULT_EFFORT: str | None = None

# Valid effort levels (Anthropic adaptive thinking). xhigh/max are Opus-tier.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
_TRUTHY = {"1", "true", "on", "yes", "y"}


def _parse_bool(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration.

    ``api_key`` is read from the environment, not stored in the repo. The core
    reads it from here; only ``AnthropicClient`` ever uses it (v0.1).
    """

    provider: str = "anthropic"
    model: str = DEFAULT_MODEL
    canon_path: Path = DEFAULT_CANON_PATH
    store_path: Path = DEFAULT_STORE_PATH
    memory_window: int = DEFAULT_MEMORY_WINDOW
    compaction_batch: int = DEFAULT_COMPACTION_BATCH
    max_tokens: int = DEFAULT_MAX_TOKENS
    thinking: bool = DEFAULT_THINKING
    effort: str | None = DEFAULT_EFFORT
    api_key: str | None = field(default=None, repr=False)


def load_config(*, load_env: bool = True) -> Config:
    """Build a :class:`Config` from the environment (and ``.env``).

    Environment overrides:
      - ``LUMI_MODEL`` — the model id (default :data:`DEFAULT_MODEL`).
      - ``LUMI_PROVIDER`` — the provider (default ``anthropic``; more in v0.9).
      - ``LUMI_CANON_PATH`` — the active canon file.
      - ``LUMI_MEMORY_WINDOW`` — the rolling-window size (filled in v0.2).
      - ``ANTHROPIC_API_KEY`` — the Claude Haiku key (never committed).
    """
    if load_env:
        load_dotenv()

    canon_env = os.getenv("LUMI_CANON_PATH")
    canon_path = Path(canon_env) if canon_env else DEFAULT_CANON_PATH

    store_env = os.getenv("LUMI_STORE_PATH")
    store_path = Path(store_env) if store_env else DEFAULT_STORE_PATH

    window_env = os.getenv("LUMI_MEMORY_WINDOW")
    memory_window = int(window_env) if window_env else DEFAULT_MEMORY_WINDOW

    batch_env = os.getenv("LUMI_COMPACTION_BATCH")
    compaction_batch = int(batch_env) if batch_env else DEFAULT_COMPACTION_BATCH

    max_tokens_env = os.getenv("LUMI_MAX_TOKENS")
    max_tokens = int(max_tokens_env) if max_tokens_env else DEFAULT_MAX_TOKENS

    thinking = _parse_bool(os.getenv("LUMI_THINKING"))

    effort_env = os.getenv("LUMI_EFFORT")
    effort = effort_env.strip().lower() if effort_env and effort_env.strip() else DEFAULT_EFFORT

    return Config(
        provider=os.getenv("LUMI_PROVIDER", "anthropic"),
        model=os.getenv("LUMI_MODEL", DEFAULT_MODEL),
        canon_path=canon_path,
        store_path=store_path,
        memory_window=memory_window,
        compaction_batch=compaction_batch,
        max_tokens=max_tokens,
        thinking=thinking,
        effort=effort,
        api_key=os.getenv("ANTHROPIC_API_KEY"),
    )
