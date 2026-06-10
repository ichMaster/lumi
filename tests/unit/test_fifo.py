"""The file bus — append-only JSONL FIFO + id pointer (v0.13, LUMI-053)."""

from state.fifo import append, load_pointer, read_since, save_pointer


def test_append_assigns_monotonic_ids(tmp_path):
    q = tmp_path / "q.jsonl"
    assert append(q, "a") == 1
    assert append(q, "b") == 2
    assert append(q, "c") == 3
    assert [r["text"] for r in read_since(q, 0)] == ["a", "b", "c"]  # ordered


def test_append_carries_extra_fields_and_ts(tmp_path):
    q = tmp_path / "q.jsonl"
    append(q, "hi", emotion="joy", theme="3am")
    rec = read_since(q, 0)[0]
    assert rec["text"] == "hi" and rec["emotion"] == "joy" and rec["theme"] == "3am"
    assert rec["ts"]  # stamped


def test_read_since_returns_only_newer(tmp_path):
    q = tmp_path / "q.jsonl"
    for t in ["a", "b", "c", "d"]:
        append(q, t)
    assert [r["id"] for r in read_since(q, 2)] == [3, 4]  # only id > 2
    assert read_since(q, 4) == []  # caught up
    assert read_since(tmp_path / "missing.jsonl", 0) == []  # missing → empty


def test_pointer_round_trips_and_survives_restart(tmp_path):
    pos = tmp_path / "q.pos"
    assert load_pointer(pos) == 0  # none yet
    save_pointer(pos, 7)
    assert load_pointer(pos) == 7  # persisted (a fresh read = a "restart")


def test_consumer_flow(tmp_path):
    q, pos = tmp_path / "q.jsonl", tmp_path / "q.pos"
    append(q, "a")
    append(q, "b")
    new = read_since(q, load_pointer(pos))           # consume from the pointer
    assert [r["text"] for r in new] == ["a", "b"]
    save_pointer(pos, new[-1]["id"])                  # advance
    append(q, "c")
    new2 = read_since(q, load_pointer(pos))           # only the new one
    assert [r["text"] for r in new2] == ["c"]


def test_trim_safe(tmp_path):
    # Trimming consumed records (id <= pointer) leaves the consumer + monotonic ids correct.
    q, pos = tmp_path / "q.jsonl", tmp_path / "q.pos"
    for t in ["a", "b", "c"]:
        append(q, t)
    save_pointer(pos, 2)  # consumed a, b
    # simulate a trim: rewrite the file dropping id <= pointer (keep the un-consumed tail)
    kept = read_since(q, 2)  # [c (id 3)]
    import json
    q.write_text("\n".join(json.dumps(r) for r in kept) + "\n", encoding="utf-8")
    assert [r["text"] for r in read_since(q, load_pointer(pos))] == ["c"]  # consumer unaffected
    assert append(q, "d") == 4  # next id stays monotonic (continues from the kept tail)
