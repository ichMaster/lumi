"""Thread-safety of the JSON store + the bulk vector path (recall startup-backfill fix).

The TUI writes the store from background worker threads (mood, recall backfill) while the
main thread also writes it. Before the lock, two concurrent _persist calls raced on the
shared `store.json.tmp` → `FileNotFoundError` on replace. These pin the fix.
"""

import threading

from core.embedder import MockEmbedder
from core.repository import make_message, make_vector_record
from state.local_store import JsonRepository

OWNER = "owner"


def test_add_vectors_is_one_write_and_idempotent(tmp_path):
    repo = JsonRepository(tmp_path / "s.json")
    e = MockEmbedder()
    texts = [f"повідомлення {i}" for i in range(20)]
    vecs = e.embed(texts)
    recs = [
        make_vector_record(user_id=OWNER, session_id="s1", role="user", text=t,
                           ts=f"2026-06-06T10:00:{i:02d}", vector=v)
        for i, (t, v) in enumerate(zip(texts, vecs, strict=True))
    ]
    repo.add_vectors(recs)
    assert len(repo._vectors[OWNER]) == 20
    repo.add_vectors(recs)  # re-adding the same records upserts, never duplicates
    assert len(repo._vectors[OWNER]) == 20

    # Reloads intact from disk.
    assert len(JsonRepository(tmp_path / "s.json")._vectors[OWNER]) == 20


def test_concurrent_writers_do_not_crash_or_lose_data(tmp_path):
    # Reproduces the startup-backfill crash: two threads persisting the same store at once.
    repo = JsonRepository(tmp_path / "s.json")
    session = repo.create_session(OWNER)
    e = MockEmbedder()
    errors: list[BaseException] = []

    def append_messages():
        try:
            for i in range(60):
                repo.append_message(
                    make_message(session.id, OWNER, "user", f"msg {i}", ts=f"2026-06-06T10:{i:02d}:00")
                )
        except BaseException as exc:  # noqa: BLE001 — capture any thread crash for the assert
            errors.append(exc)

    def add_vectors():
        try:
            for i in range(60):
                [v] = e.embed([f"vec {i}"])
                repo.add_vector(make_vector_record(
                    user_id=OWNER, session_id=session.id, role="lili",
                    text=f"vec {i}", ts=f"2026-06-06T11:{i:02d}:00", vector=v,
                ))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=append_messages), threading.Thread(target=add_vectors)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []  # no FileNotFoundError / dict-changed-during-iteration
    # Both writers' data survived (no last-writer-wins clobber).
    reopened = JsonRepository(tmp_path / "s.json")
    assert len(reopened.load_messages(session.id)) == 60
    assert len(reopened._vectors[OWNER]) == 60
