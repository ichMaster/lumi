"""Unit tests for the Repository interface + local JSON store (LUMI-004, LUMI-007)."""

import pytest

from core.repository import Message, Repository, Session, make_message
from core.user import DEFAULT_USER_ID
from state.local_store import JsonRepository

OWNER = DEFAULT_USER_ID


def test_jsonrepo_satisfies_repository_protocol(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    assert isinstance(repo, Repository)


def test_create_session_persists(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    session = repo.create_session(OWNER)
    assert isinstance(session, Session)
    assert session.id
    assert session.user_id == OWNER
    assert session.started_at
    assert session.ended_at is None
    assert repo.get_session(session.id) == session


def test_append_and_load_messages_round_trip(tmp_path):
    repo = JsonRepository(tmp_path / "store.json")
    session = repo.create_session(OWNER)
    repo.append_message(
        make_message(session.id, OWNER, "user", "Привіт", ts="2026-06-06T10:00:00+00:00")
    )
    repo.append_message(
        make_message(session.id, OWNER, "lili", "Привіт. Я тут.", ts="2026-06-06T10:00:01+00:00")
    )

    msgs = repo.load_messages(session.id)
    assert [m.role for m in msgs] == ["user", "lili"]
    assert all(m.user_id == OWNER for m in msgs)
    assert msgs[0].text == "Привіт"
    assert msgs[1].text == "Привіт. Я тут."


def test_messages_reload_after_restart(tmp_path):
    path = tmp_path / "store.json"
    repo = JsonRepository(path)
    session = repo.create_session(OWNER)
    repo.append_message(make_message(session.id, OWNER, "user", "запам'ятай це"))

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
    session = repo.create_session(OWNER)
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
        Message(
            session_id="s",
            user_id=OWNER,
            role="robot",
            text="x",
            ts="2026-06-06T10:00:00+00:00",
        )


def test_records_carry_user_id():
    # v0.2 shapes match ARCHITECTURE §Data model — every per-user record has user_id.
    assert "user_id" in Message.__dataclass_fields__
    assert "user_id" in Session.__dataclass_fields__


def test_pre_v0_2_store_migrates_missing_user_id(tmp_path):
    # A pre-v0.2 store (no user_id) loads with the default owner injected.
    path = tmp_path / "old.json"
    path.write_text(
        '{"sessions": {"s1": {"id": "s1", "started_at": "2026-06-01T00:00:00+00:00"}},'
        ' "messages": {"s1": [{"session_id": "s1", "role": "user", "text": "hi",'
        ' "ts": "2026-06-01T00:00:01+00:00"}]}}',
        encoding="utf-8",
    )
    repo = JsonRepository(path)
    assert repo.get_session("s1").user_id == OWNER
    assert repo.load_messages("s1")[0].user_id == OWNER
