"""v1.5 (LUMI-195) — migrate a JSON store to the SQLite backend (and back).

Forward (default): back up ``store.json``, then open :class:`~state.sqlite_store.SqliteRepository`
over it — the adoption path imports messages + closeness and **streams** the ``.vectors.jsonl``
re-pack into float32 BLOBs, all **without a single embedder call** (a pure format re-pack).
Idempotent: a second run finds the data already in the DB and changes nothing. The old
``.vectors.jsonl`` is left in place for you to archive once you've verified the migration.

Rollback: ``--export-json`` writes the DB's messages back into ``store.json`` and the vectors back
into ``.vectors.jsonl`` (float lists), so ``LUMI_STORE_BACKEND=json`` picks everything up again.

Usage:
    uv run python scripts/migrate_store.py [--store .lumi/store.json]
    uv run python scripts/migrate_store.py --export-json   # rollback to the JSON backend

Stop the TUI / daemons first — a live writer would race the migration.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on the path

from state.sqlite_store import SqliteRepository, _unpack_vector  # noqa: E402


def _counts(repo: SqliteRepository) -> tuple[int, int, int]:
    msgs = repo._db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    vecs = repo._db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    clos = repo._db.execute("SELECT COUNT(*) FROM closeness").fetchone()[0]
    return msgs, vecs, clos


def migrate(store_path: Path) -> None:
    if not store_path.is_file():
        raise SystemExit(f"no store at {store_path}")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = store_path.with_name(f"{store_path.name}.bak-migrate-{stamp}")
    shutil.copy2(store_path, backup)
    print(f"backup: {backup}")

    repo = SqliteRepository(store_path)  # adoption imports messages/closeness + streams the vectors
    msgs, vecs, clos = _counts(repo)
    print(f"migrated → {repo._db_path}: {msgs} messages, {vecs} vectors, {clos} closeness records")
    jsonl = store_path.parent / f"{store_path.stem}.vectors.jsonl"
    if jsonl.is_file():
        print(f"note: {jsonl} left in place — archive/delete it once you've verified the migration")
    print("done. Set LUMI_STORE_BACKEND=sqlite in .env")


def export_json(store_path: Path) -> None:
    db_path = store_path.parent / f"{store_path.stem}.db"
    if not db_path.is_file():
        raise SystemExit(f"no DB at {db_path} — nothing to export")
    repo = SqliteRepository(store_path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = store_path.with_name(f"{store_path.name}.bak-export-{stamp}")
    shutil.copy2(store_path, backup)
    print(f"backup: {backup}")

    # Messages back into store.json (the light aggregates are already there).
    data = json.loads(store_path.read_text(encoding="utf-8"))
    messages: dict[str, list[dict]] = {}
    for sid, raw in repo._db.execute("SELECT session_id, data FROM messages ORDER BY seq"):
        messages.setdefault(sid, []).append(json.loads(raw))
    data["messages"] = messages
    closeness: dict[str, dict] = {}
    for uid, raw in repo._db.execute("SELECT user_id, data FROM closeness"):
        closeness[uid] = json.loads(raw)
    data["closeness"] = closeness
    store_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Vectors back into the JSONL (float lists), streamed.
    jsonl = store_path.parent / f"{store_path.stem}.vectors.jsonl"
    cols = "user_id, msg_id, kind, ts, role, text, parent_msg_id, chunk_index, vector"
    with jsonl.open("w", encoding="utf-8") as fh:
        for (u, m, kd, ts, ro, tx, p, ci, blob) in repo._db.execute(f"SELECT {cols} FROM vectors"):
            rec = {"user_id": u, "msg_id": m, "vector": list(_unpack_vector(blob)), "text": tx,
                   "ts": ts, "role": ro, "parent_msg_id": p, "chunk_index": ci, "kind": kd}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    n_msgs = sum(len(v) for v in messages.values())
    print(f"exported: {n_msgs} messages → {store_path.name}, vectors → {jsonl.name}")
    print("done. Set LUMI_STORE_BACKEND=json in .env")


def main() -> None:
    ap = argparse.ArgumentParser(description="Migrate the Lumi store JSON ↔ SQLite (v1.5)")
    ap.add_argument("--store", default=".lumi/store.json", help="path to store.json")
    ap.add_argument("--export-json", action="store_true", help="rollback: DB → store.json + vectors.jsonl")
    args = ap.parse_args()
    path = Path(args.store)
    if args.export_json:
        export_json(path)
    else:
        migrate(path)


if __name__ == "__main__":
    main()
