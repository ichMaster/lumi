"""User identity (minimal, v0.2).

The core is **user-scoped from v0.2** so the v1 multi-user server is additive,
not a rewrite (ARCHITECTURE §Identity, users, and memory scopes). v0.2 runs with
a single default ``owner``; full accounts (roles, argon2id passwords, consent
flags) arrive in v1.3 — only ``id`` matters here.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.repository import now_iso

# The single default local user until real accounts arrive (v1.3).
DEFAULT_USER_ID = "owner"


@dataclass(frozen=True)
class User:
    """An account. v0.2 needs only ``id``; the rest fills in at v1.3."""

    id: str = DEFAULT_USER_ID
    role: str = "user"
    created_at: str = ""


def default_user() -> User:
    """The default local ``owner`` user."""
    return User(id=DEFAULT_USER_ID, role="user", created_at=now_iso())
