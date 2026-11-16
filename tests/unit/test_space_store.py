"""v3.1 LUMI-279 — the space store: keyed by the space, idempotent by source_id, ordered by ts, a session per day."""

from __future__ import annotations

import sqlite3
import threading
import time
from zoneinfo import ZoneInfo

from core.embedder import LockedEmbedder, MockEmbedder
from core.space import SpaceGist, SpaceMessage, assign_session, local_day, session_id_for
from state.space_store import SqliteSpaceStore

KYIV = ZoneInfo("Europe/Kyiv")


def _line(source_id: str, ts: str, *, text: str = "так", speaker: str = "Ада", role: str = "user",
          channel: str = "agora", session_id: str = "agora-2026-11-16") -> SpaceMessage:
    return SpaceMessage(channel=channel, session_id=session_id, speaker=speaker, role=role, text=text, ts=ts,
                        source_id=source_id)


def test_round_trip_keeps_the_channel_and_the_speaker(tmp_path):
    store = SqliteSpaceStore(tmp_path / "spaces.sqlite")
    reply = SpaceMessage(channel="agora", session_id="agora-2026-11-16", speaker="Лілі", role="lili", text="Туман",
                         ts="2026-11-16T08:03:00+02:00", source_id="$t", emotion="thoughtful", intensity=0.5)
    store.add_lines([_line("$a", "2026-11-16T08:01:12+02:00"), reply])
    assert store.get("agora", "$a", "user").speaker == "Ада"
    assert store.get("agora", "$t", "lili") == reply
    assert store.get("agora", "$t", "user") is None  # her reply and the trigger share an id, never a row


def test_idempotent_by_source_id(tmp_path):
    store = SqliteSpaceStore(tmp_path / "spaces.sqlite")
    first = store.add_lines([_line("$a", "2026-11-16T08:01:00+02:00"), _line("$b", "2026-11-16T08:02:00+02:00")])
    again = store.add_lines([_line("$a", "2026-11-16T08:01:00+02:00", text="інше"),
                             _line("$c", "2026-11-16T08:03:00+02:00")])
    assert [m.source_id for m in first] == ["$a", "$b"] and [m.source_id for m in again] == ["$c"]
    assert store.get("agora", "$a", "user").text == "так"  # the first one stays — nothing stored twice


def test_the_window_is_ordered_by_ts_not_by_arrival(tmp_path):
    store = SqliteSpaceStore(tmp_path / "spaces.sqlite")
    store.add_lines([_line("$c", "2026-11-16T08:05:00+02:00", text="третя")])
    store.add_lines([_line("$a", "2026-11-16T08:01:00+02:00", text="перша")])  # late, resent by the bridge (Q1)
    store.add_lines([_line("$b", "2026-11-16T06:03:00+00:00", text="друга")])  # another offset, 08:03 in Kyiv
    assert [m.text for m in store.window("agora", 10)] == ["перша", "друга", "третя"]
    assert [m.text for m in store.window("agora", 2)] == ["друга", "третя"]  # the latest two, oldest first
    assert [m.text for m in store.window("agora", 10, until_ts="2026-11-16T08:03:00+02:00")] == ["перша", "друга"]


def test_a_second_channel_is_never_seen(tmp_path):
    store = SqliteSpaceStore(tmp_path / "spaces.sqlite")
    store.add_lines([_line("$a", "2026-11-16T08:01:00+02:00"),
                     _line("$x", "2026-11-16T08:02:00+02:00", channel="другий")])
    assert [m.source_id for m in store.window("agora", 10)] == ["$a"]
    assert store.get("agora", "$x", "user") is None
    store.add_vectors("другий", [("$x", "user", [1.0, 0.0])])
    assert store.search_vectors("agora", [1.0, 0.0], 5) == []


def test_the_session_follows_her_local_day(tmp_path):
    store = SqliteSpaceStore(tmp_path / "spaces.sqlite")
    # 23:30 UTC on the 15th is 01:30 on the 16th in Kyiv — her day, not the sender's
    assert local_day("2026-11-15T23:30:00+00:00", KYIV) == "2026-11-16"
    assert assign_session(store, "agora", "2026-11-15T23:30:00+00:00", KYIV) == "agora-2026-11-16"
    assert assign_session(store, "agora", "2026-11-16T23:59:59+02:00", KYIV) == "agora-2026-11-16"
    assert assign_session(store, "agora", "2026-11-17T00:00:01+02:00", KYIV) == "agora-2026-11-17"


def test_a_dst_day_keeps_its_own_session():
    # 2026-10-25: Kyiv falls back from +03:00 to +02:00 — both sides of the change are the same local day
    assert local_day("2026-10-25T02:30:00+03:00", KYIV) == local_day("2026-10-25T23:30:00+02:00", KYIV) == "2026-10-25"


def test_the_session_rolls_by_size(tmp_path):
    store = SqliteSpaceStore(tmp_path / "spaces.sqlite")
    sid = session_id_for("agora", "2026-11-16")
    store.add_lines([_line(f"${i}", f"2026-11-16T08:{i:02d}:00+02:00", session_id=sid) for i in range(3)])
    assert assign_session(store, "agora", "2026-11-16T09:00:00+02:00", KYIV, max_lines=5) == sid
    assert assign_session(store, "agora", "2026-11-16T09:00:00+02:00", KYIV, max_lines=3) == "agora-2026-11-16-2"
    # a batch not yet stored counts too
    assert assign_session(store, "agora", "2026-11-16T09:00:00+02:00", KYIV, pending={sid: 2},
                          max_lines=5) == "agora-2026-11-16-2"
    store.add_lines([_line("$r", "2026-11-16T09:00:00+02:00", session_id="agora-2026-11-16-2")])
    assert store.latest_session("agora", "2026-11-16") == ("agora-2026-11-16-2", 1)


def test_vectors_add_search_and_remove_by_source_id(tmp_path):
    store = SqliteSpaceStore(tmp_path / "spaces.sqlite")
    emb = MockEmbedder()
    lines = [_line("$a", "2026-11-16T08:01:00+02:00", text="туман над містом"),
             _line("$b", "2026-11-16T08:02:00+02:00", text="кава зранку")]
    store.add_lines(lines)
    store.add_vectors("agora", [(m.source_id, m.role, v) for m, v in zip(lines, emb.embed([m.text for m in lines]),
                                                                           strict=True)])
    [(score, hit), _] = store.search_vectors("agora", emb.embed(["туман"])[0], 2)
    assert hit.source_id == "$a" and score > 0
    assert store.remove("agora", "$a", "user") is True
    assert store.remove("agora", "$a", "user") is False  # already gone
    assert [m.source_id for _, m in store.search_vectors("agora", emb.embed(["туман"])[0], 5)] == ["$b"]


def test_gists_by_day(tmp_path):
    store = SqliteSpaceStore(tmp_path / "spaces.sqlite")
    store.add_gist(SpaceGist("agora", "2026-11-16", "В Агорі говорили про туман."))
    store.add_gist(SpaceGist("agora", "2026-11-16", "В Агорі говорили про туман і каву."))  # rewritten, one a day
    assert store.gists("agora") == [SpaceGist("agora", "2026-11-16", "В Агорі говорили про туман і каву.")]


def test_a_row_that_no_longer_reads_is_skipped(tmp_path):
    path = tmp_path / "spaces.sqlite"
    store = SqliteSpaceStore(path)
    store.add_lines([_line("$a", "2026-11-16T08:01:00+02:00"), _line("$b", "2026-11-16T08:02:00+02:00")])
    raw = sqlite3.connect(path)
    with raw:
        raw.execute("UPDATE space_messages SET data = '{\"broken\": 1}' WHERE source_id = '$a'")
    raw.close()
    assert [m.source_id for m in store.window("agora", 10)] == ["$b"]


def test_the_file_reopens_with_its_lines(tmp_path):
    path = tmp_path / "spaces.sqlite"
    store = SqliteSpaceStore(path)
    store.add_lines([_line("$a", "2026-11-16T08:01:00+02:00")])
    store.close()
    assert SqliteSpaceStore(path).get("agora", "$a", "user") is not None


def test_concurrent_writers_share_the_connection(tmp_path):
    store = SqliteSpaceStore(tmp_path / "spaces.sqlite")

    def write(prefix: str) -> None:
        for i in range(50):
            store.add_lines([_line(f"{prefix}{i}", f"2026-11-16T08:{i % 60:02d}:00+02:00")])

    threads = [threading.Thread(target=write, args=(p,)) for p in ("$a", "$b", "$c")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(store.window("agora", 1000)) == 150  # every line once


def test_the_locked_embedder_serializes_embed():
    inner = MockEmbedder()
    inside, overlap = [0], [False]
    real = inner.embed

    def slow(texts, *, is_query=False):
        inside[0] += 1
        overlap[0] |= inside[0] > 1
        try:
            time.sleep(0.0001)  # widen the window: without the lock two threads would meet here
            return real(texts, is_query=is_query)
        finally:
            inside[0] -= 1

    inner.embed = slow  # type: ignore[method-assign]
    locked = LockedEmbedder(inner)
    threads = [threading.Thread(target=lambda: [locked.embed(["а б в"]) for _ in range(200)]) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not overlap[0] and len(inner.calls) == 800 and locked.dim == inner.dim


# --- v3.4 LUMI-296: her initiative's offers -------------------------------------------------------------------------

def _offer(initiative_id: str, composed_at: str, *, text: str = "Сьогодні вітер пахне снігом.", last_seq: int = 3):
    from core.space import SpaceOffer

    return SpaceOffer(channel="agora", initiative_id=initiative_id, text=text, composed_at=composed_at,
                      last_seq=last_seq, stats={"model": "gemini-2.5-flash", "input_tokens": 5880,
                                                "output_tokens": 21, "latency_ms": 1520},
                      emotion="calm", intensity=0.4)


def test_an_offer_round_trips_and_the_latest_is_the_newest(tmp_path):
    store = SqliteSpaceStore(tmp_path / "spaces.sqlite")
    assert store.latest_offer("agora") is None
    first = _offer("ini-1", "2026-11-16T10:00:00+02:00")
    second = _offer("ini-2", "2026-11-16T10:40:00+02:00", text="Інша думка.")
    store.add_offer(first)
    store.add_offer(second)
    assert store.get_offer("agora", "ini-1") == first
    assert store.latest_offer("agora") == second
    assert store.get_offer("agora", "ini-x") is None and store.latest_offer("elsewhere") is None


def test_an_offer_is_acked_once(tmp_path):
    store = SqliteSpaceStore(tmp_path / "spaces.sqlite")
    store.add_offer(_offer("ini-1", "2026-11-16T10:00:00+02:00"))
    assert store.ack_offer("agora", "ini-1", "$posted") is True
    assert store.get_offer("agora", "ini-1").event_id == "$posted"
    assert store.ack_offer("agora", "ini-1", "$posted") is False  # idempotent
    assert store.ack_offer("agora", "ini-1", "$other") is False  # never a second line for one offer
    assert store.get_offer("agora", "ini-1").event_id == "$posted"
    assert store.ack_offer("agora", "ini-unknown", "$posted") is False


def test_old_offers_are_pruned_and_new_ones_kept(tmp_path):
    store = SqliteSpaceStore(tmp_path / "spaces.sqlite")
    store.add_offer(_offer("ini-old", "2026-11-15T09:00:00+02:00"))
    store.add_offer(_offer("ini-new", "2026-11-16T09:30:00+02:00"))
    assert store.prune_offers("agora", "2026-11-15T10:00:00+02:00") == 1
    assert store.get_offer("agora", "ini-old") is None and store.get_offer("agora", "ini-new") is not None


def test_last_seq_grows_with_every_new_line_and_never_reuses_one(tmp_path):
    store = SqliteSpaceStore(tmp_path / "spaces.sqlite")
    assert store.last_seq("agora") == 0
    store.add_lines([_line("$a", "2026-11-16T08:01:00+02:00")])
    one = store.last_seq("agora")
    reply = SpaceMessage(channel="agora", session_id="agora-2026-11-16", speaker="Лілі", role="lili", text="так",
                         ts="2026-11-16T08:02:00+02:00", source_id="$a")
    store.add_lines([reply])
    two = store.last_seq("agora")
    assert two > one
    store.remove("agora", "$a", "lili")
    assert store.last_seq("agora") <= two  # a removal never raises it
    store.add_lines([_line("$late", "2026-11-16T07:00:00+02:00")])  # an older ts, arriving late
    assert store.last_seq("agora") > two  # still newer than anything before — a fresh number, never a reused one
    store.add_lines([_line("$a", "2026-11-16T08:01:00+02:00")])  # a known line adds nothing
    assert store.last_seq("agora") > two


def test_an_offer_row_that_no_longer_reads_is_skipped(tmp_path):
    path = tmp_path / "spaces.sqlite"
    store = SqliteSpaceStore(path)
    store.add_offer(_offer("ini-1", "2026-11-16T10:00:00+02:00"))
    store.add_offer(_offer("ini-2", "2026-11-16T10:40:00+02:00"))
    raw = sqlite3.connect(path)
    with raw:
        raw.execute("UPDATE space_offers SET data = '{\"broken\": 1}' WHERE initiative_id = 'ini-2'")
    raw.close()
    assert store.get_offer("agora", "ini-2") is None
    assert store.latest_offer("agora").initiative_id == "ini-1"
    assert store.ack_offer("agora", "ini-2", "$p") is False


def test_a_v3_3_store_opens_and_gains_the_offers_table(tmp_path):
    from state import space_store

    path = tmp_path / "spaces.sqlite"
    old = sqlite3.connect(path)
    with old:
        for statement in space_store._SCHEMA:
            if "space_offers" not in statement:  # v3.3's schema: no offers table yet
                old.execute(statement)
    old.close()
    before = SqliteSpaceStore(tmp_path / "seed.sqlite")  # a line written the v3.3 way, copied in by its row
    before.add_lines([_line("$a", "2026-11-16T08:01:00+02:00")])
    before.close()
    raw = sqlite3.connect(path)
    with raw:
        raw.execute("ATTACH DATABASE ? AS seed", (str(tmp_path / "seed.sqlite"),))
        raw.execute("INSERT INTO space_messages SELECT * FROM seed.space_messages")
    raw.close()
    store = SqliteSpaceStore(path)
    assert store.get("agora", "$a", "user").text == "так"
    store.add_offer(_offer("ini-1", "2026-11-16T10:00:00+02:00"))
    assert store.latest_offer("agora").initiative_id == "ini-1"
