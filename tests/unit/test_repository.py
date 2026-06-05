"""Unit tests for the Repository interface + local JSON store (LUMI-004)."""

import pytest

from core.repository import Message, Repository, Session, make_message
from state.local_store import JsonRepository


def test_jsonrepo_satisfies_repository_protocol(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    assert isinstance(repo, Repository)


def test_create_session_persists(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    session = repo.create_session()
    assert isinstance(session, Session)
    assert session.id
    assert session.started_at
    assert session.ended_at is None
    assert repo.get_session(session.id) == session


def test_append_and_load_messages_round_trip(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    session = repo.create_session()
    repo.append_message(make_message(session.id, "user", "Привіт", ts="2026-06-06T10:00:00+00:00"))
    repo.append_message(make_message(session.id, "lili", "Привіт. Я тут.", ts="2026-06-06T10:00:01+00:00"))

    msgs = repo.load_messages(session.id)
    assert [m.role for m in msgs] == ["user", "lili"]
    assert msgs[0].text == "Привіт"
    assert msgs[1].text == "Привіт. Я тут."


def test_messages_reload_after_restart(tmp_path):
    path = tmp_path / "store.json"
    repo = JsonRepository(path)
    session = repo.create_session()
    repo.append_message(make_message(session.id, "user", "запам'ятай це"))

    # Simulate a restart: a brand-new store instance over the same file.
    reopened = JsonRepository(path)
    reloaded_session = reopened.get_session(session.id)
    assert reloaded_session is not None
    assert reloaded_session.id == session.id
    msgs = reopened.load_messages(session.id)
    assert len(msgs) == 1
    assert msgs[0].text == "запам'ятай це"


def test_end_session_sets_ended_at(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    session = repo.create_session()
    ended = repo.end_session(session.id)
    assert ended is not None
    assert ended.ended_at is not None
    assert repo.get_session(session.id).ended_at is not None


def test_unknown_session_returns_empty_and_none(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    assert repo.get_session("missing") is None
    assert repo.end_session("missing") is None
    assert repo.load_messages("missing") == []


def test_message_rejects_unknown_role():
    with pytest.raises(ValueError, match="role"):
        Message(session_id="s", role="robot", text="x", ts="2026-06-06T10:00:00+00:00")


def test_message_shape_has_no_user_id_yet():
    # v0.1 shapes match ARCHITECTURE §Data model minus user_id (added in v0.2).
    fields = set(Message.__dataclass_fields__)
    assert fields == {"session_id", "role", "text", "ts"}
    assert "user_id" not in Session.__dataclass_fields__
