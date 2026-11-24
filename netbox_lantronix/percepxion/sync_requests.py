# Shared code: this file is the same in netbox-lantronix and nautobot-app-ssot-lantronix.
# scripts/check_shared.py fails CI when the copies differ, so change both.
"""Pending focused-sync requests: many requests, one waiting sync.

Events, the sync API and the change watch each ask for some console servers to be synced.
Requests made while a focused sync is waiting to start join it; the job takes the whole set
when it starts. So a burst of port changes across a few SLCs becomes one sync of those SLCs.

State lives in a shared store (the Django cache in both products, django-redis, which has
locks), behind a small interface so this module is tested without a framework.
"""

from __future__ import annotations

from typing import Protocol

WAITING_TTL = 15 * 60  # a waiting mark outlives a crashed worker by this much at most
PENDING_TTL = 24 * 60 * 60
SEEN_TTL = 60 * 60
LOCK_TIMEOUT = 10


class Store(Protocol):
    def get(self, key: str, default=None): ...
    def set(self, key: str, value, timeout: int | None = None) -> None: ...
    def delete(self, key: str) -> None: ...
    def lock(self, key: str, timeout: int | None = None): ...


class SyncRequests:
    """Requests for one source (or one install), under the store key prefix ``key``."""

    def __init__(self, store: Store, key: str):
        self.store = store
        self.key = key

    def _lock(self):
        return self.store.lock(f"{self.key}:lock", timeout=LOCK_TIMEOUT)

    def add(self, device_ids) -> bool:
        """Add console servers to the pending set. True means no focused sync is waiting yet:
        the caller enqueues one now, and it is marked waiting."""
        ids = {d for d in device_ids if d}
        if not ids:
            return False
        with self._lock():
            pending = set(self.store.get(f"{self.key}:pending") or ())
            self.store.set(f"{self.key}:pending", sorted(pending | ids), PENDING_TTL)
            if self.store.get(f"{self.key}:waiting"):
                return False
            self.store.set(f"{self.key}:waiting", True, WAITING_TTL)
            return True

    def take(self) -> set[str]:
        """Return and clear the pending set, and clear the waiting mark. Called by the focused
        sync when it starts; requests after this point enqueue the next one."""
        with self._lock():
            pending = set(self.store.get(f"{self.key}:pending") or ())
            self.store.delete(f"{self.key}:pending")
            self.store.delete(f"{self.key}:waiting")
            return pending

    def release(self) -> None:
        """Clear the waiting mark without taking the pending set: the caller's enqueue failed,
        so the next request enqueues again and that job syncs everything pending."""
        with self._lock():
            self.store.delete(f"{self.key}:waiting")

    def seen(self, event_id: str) -> bool:
        """True if this event ID was seen in the last hour; records it otherwise."""
        key = f"{self.key}:event:{event_id}"
        with self._lock():
            if self.store.get(key):
                return True
            self.store.set(key, True, SEEN_TTL)
            return False
