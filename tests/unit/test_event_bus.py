"""The v2.4 push channel's plumbing (LUMI-219) — the event bus and the SSE frame generator, as plain data."""

import queue
import threading

from server.app import EventBus, event_frames, origin_of


def _drain(sub: queue.Queue) -> list:
    out = []
    while True:
        try:
            out.append(sub.get_nowait())
        except queue.Empty:
            return out


def test_every_listener_hears_every_event():
    bus = EventBus()
    a, b = bus.subscribe(), bus.subscribe()
    bus.publish("turn", {"emotion": "joy"})
    assert _drain(a) == _drain(b) == [("turn", {"emotion": "joy"})]
    assert bus.listeners == 2


def test_an_unsubscribed_listener_hears_nothing_more():
    bus = EventBus()
    sub = bus.subscribe()
    bus.unsubscribe(sub)
    bus.unsubscribe(sub)  # twice is harmless
    bus.publish("state", {})
    assert _drain(sub) == [] and bus.listeners == 0


def test_a_full_queue_drops_its_oldest_and_never_blocks_the_publisher():
    bus = EventBus(maxsize=3)
    slow = bus.subscribe()  # never read
    done = threading.Event()

    def publish_many():
        for i in range(10):
            bus.publish("turn", {"n": i})
        done.set()

    threading.Thread(target=publish_many).start()
    assert done.wait(2)  # the publisher was never held up by the stuck listener
    assert [d["n"] for _, d in _drain(slow)] == [7, 8, 9]  # only the newest survive


def test_close_ends_every_stream_and_a_late_listener_ends_at_once():
    bus = EventBus()
    sub = bus.subscribe()
    bus.close()
    frames = list(event_frames(sub, ("state", {"s": 1}), heartbeat_s=5))
    assert len(frames) == 1 and frames[0].startswith("event: state\n")  # the first state, then the end
    late = bus.subscribe()
    assert len(list(event_frames(late, ("state", {}), heartbeat_s=5))) == 1
    assert bus.listeners == 0


def test_frames_are_sse_and_silence_becomes_a_heartbeat_comment():
    bus = EventBus()
    sub = bus.subscribe()
    frames = event_frames(sub, ("state", {"origin": None, "state": {"mood": "тихо"}}), heartbeat_s=0.01)
    assert next(frames) == 'event: state\ndata: {"origin": null, "state": {"mood": "тихо"}}\n\n'
    assert next(frames) == ": heartbeat\n\n"  # nothing published — a comment keeps the line alive
    bus.publish("turn", {"emotion": "calm"})
    assert next(frames) == 'event: turn\ndata: {"emotion": "calm"}\n\n'
    bus.close()
    assert list(frames) == []


def test_origin_is_the_trimmed_bounded_client_id_or_none():
    assert origin_of(None) is None and origin_of("  ") is None
    assert origin_of(" tui-1 ") == "tui-1"
    assert len(origin_of("x" * 500)) == 64
