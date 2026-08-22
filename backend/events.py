"""Tiny in-memory pub/sub: orchestrator publishes real state-change events,
the SSE route in app.py fans them out to connected browsers."""
import queue
import threading

_subscribers = []
_lock = threading.Lock()


def subscribe():
    q = queue.Queue()
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q):
    with _lock:
        if q in _subscribers:
            _subscribers.remove(q)


def publish(event):
    with _lock:
        subs = list(_subscribers)
    for q in subs:
        q.put(event)


if __name__ == "__main__":
    q = subscribe()
    publish({"type": "test"})
    assert q.get_nowait() == {"type": "test"}
    unsubscribe(q)
    assert q not in _subscribers
    print("events.py self-check OK")
