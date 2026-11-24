# Shared code: this file is the same in netbox-lantronix and nautobot-app-ssot-lantronix.
# scripts/check_shared.py fails CI when the copies differ, so change both.
"""Pending focused-sync requests: merging, the waiting mark, take, duplicates."""

import threading
from contextlib import contextmanager

from netbox_lantronix.percepxion.sync_requests import WAITING_TTL, SyncRequests


class FakeStore:
    """The slice of the Django cache SyncRequests uses, with a clock for expiry."""

    def __init__(self):
        self.data, self.now, self.locks = {}, 0.0, 0
        self._lock = threading.Lock()

    def get(self, key, default=None):
        value, expires = self.data.get(key, (default, None))
        if expires is not None and expires <= self.now:
            return default
        return value

    def set(self, key, value, timeout=None):
        self.data[key] = (value, None if timeout is None else self.now + timeout)

    def delete(self, key):
        self.data.pop(key, None)

    @contextmanager
    def lock(self, key, timeout=None):
        with self._lock:
            self.locks += 1
            yield


def make():
    store = FakeStore()
    return store, SyncRequests(store, "test:source:1")


def test_first_request_enqueues_later_ones_join():
    _, req = make()
    assert req.add(["A"]) is True
    assert req.add(["B", "A"]) is False
    assert req.add(["C"]) is False
    assert req.take() == {"A", "B", "C"}


def test_take_clears_so_the_next_request_enqueues_again():
    _, req = make()
    req.add(["A"])
    assert req.take() == {"A"}
    assert req.take() == set()
    assert req.add(["B"]) is True


def test_empty_requests_do_nothing():
    _, req = make()
    assert req.add([]) is False
    assert req.add(["", None]) is False
    assert req.take() == set()


def test_waiting_mark_expires_after_a_crashed_worker():
    store, req = make()
    assert req.add(["A"]) is True
    store.now += WAITING_TTL + 1
    assert req.add(["B"]) is True  # the lost job's devices are kept and synced by the new one
    assert req.take() == {"A", "B"}


def test_release_after_a_failed_enqueue_keeps_the_devices():
    _, req = make()
    assert req.add(["A"]) is True
    req.release()
    assert req.add(["B"]) is True
    assert req.take() == {"A", "B"}


def test_sources_are_independent():
    store = FakeStore()
    one, two = SyncRequests(store, "s:1"), SyncRequests(store, "s:2")
    assert one.add(["A"]) is True
    assert two.add(["B"]) is True
    assert one.take() == {"A"}
    assert two.take() == {"B"}


def test_duplicate_event_ids():
    store, req = make()
    assert req.seen("evt-1") is False
    assert req.seen("evt-1") is True
    assert req.seen("evt-2") is False
    store.now += 60 * 60 + 1
    assert req.seen("evt-1") is False


def test_every_change_happens_under_the_lock():
    store, req = make()
    req.add(["A"])
    req.take()
    req.seen("e")
    assert store.locks == 3


def test_concurrent_requests_enqueue_once():
    _, req = make()
    results = []
    threads = [
        threading.Thread(target=lambda i=i: results.append(req.add([f"D{i}"]))) for i in range(20)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1
    assert req.take() == {f"D{i}" for i in range(20)}
