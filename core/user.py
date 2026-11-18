"""User identity (minimal, v0.2).

The core is **user-scoped from v0.2** so the v2 multi-user server is additive,
not a rewrite (ARCHITECTURE §Identity, users, and memory scopes). v0.2 runs with
a single default ``owner``; full accounts (roles, argon2id passwords, consent
flags) arrive in v2.3 — only ``id`` matters here.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.repository import now_iso

# The single default local user until real accounts arrive (v2.3).
DEFAULT_USER_ID = "owner"


@dataclass(frozen=True)
class User:
    """An account. v0.2 needs only ``id``; the rest fills in at v2.3."""

    id: str = DEFAULT_USER_ID
    role: str = "user"
    created_at: str = ""


def default_user() -> User:
    """The default local ``owner`` user."""
    return User(id=DEFAULT_USER_ID, role="user", created_at=now_iso())


def may_read_journal(user_id: str) -> bool:
    """v4.3 LUMI-309: whether ``user_id`` may read Лілі's journal from a client.

    The journal is owner/admin-only by rule (ROADMAP §v9.6: her private literary life, never shown to other users).
    Today the owner is the only user and the admin, so the rule is the owner's id; the v6.2 accounts replace it with
    the account's role. The server checks it on every journal route, after the token."""
    return user_id == DEFAULT_USER_ID
