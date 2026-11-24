# Shared code: this file is the same in netbox-lantronix and nautobot-app-ssot-lantronix.
# scripts/check_shared.py fails CI when the copies differ, so change both.
"""Signed Percepxion events (webhook-payload v1): signature check and envelope parsing.

Percepxion webhooks, and the lab relay that stands in for them, POST a JSON envelope signed
with HMAC-SHA256 over ``<timestamp>.<raw body>``. The timestamp is sent in
``X-Percepxion-Timestamp``; ``X-Percepxion-Signature`` carries one or more ``v1=<hex>``
entries, more than one only while a secret is being rotated.

Only the event ID, type, organization and Percepxion device ID leave ``parse``. Everything a
device reports (hostnames, models, serials) stays in the body, so nothing from it reaches a
log, a job or the sync queue.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ..sanitize import clean_text, safe_id

SCHEME = "v1"
TIMESTAMP_HEADER = "X-Percepxion-Timestamp"
SIGNATURE_HEADER = "X-Percepxion-Signature"
TOLERANCE = 300  # seconds either side of this server's clock
MAX_BODY = 1 << 20
MIN_SECRET_LENGTH = 32
SYNC_EVENT_TYPES = frozenset(
    {
        "percepxion.port.attached_changed",
        "percepxion.port.state_changed",
        "percepxion.device.status_changed",
    }
)


class EventRejected(Exception):
    """Not a valid signed event. The message says why and never echoes secrets or the body."""


@dataclass(frozen=True)
class Event:
    id: str
    type: str
    organization_id: str
    device_id: str


def signature(secret: str | bytes, timestamp: int | str, body: bytes) -> str:
    """Hex HMAC-SHA256 over ``<timestamp>.<body>``."""
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    msg = str(int(timestamp)).encode("ascii") + b"." + body
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def sign(secrets: str | Iterable[str], body: bytes, timestamp: int | None = None) -> dict:
    """Headers for one delivery. For tests and tools; Percepxion signs its own."""
    if isinstance(secrets, str):
        secrets = [secrets]
    ts = int(time.time()) if timestamp is None else int(timestamp)
    sigs = ",".join(f"{SCHEME}={signature(s, ts, body)}" for s in secrets)
    return {TIMESTAMP_HEADER: str(ts), SIGNATURE_HEADER: sigs}


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lower = name.lower()
    for key, value in headers.items():
        if key.lower() == lower:
            return value
    return None


def verify(
    secrets: Iterable[str],
    headers: Mapping[str, str],
    body: bytes,
    tolerance: int = TOLERANCE,
    now: float | None = None,
) -> int:
    """Return the signed timestamp, or raise EventRejected.

    One of the signatures must match one of the secrets, and the timestamp must be within
    ``tolerance`` seconds. Pass the current and previous secret while rotating.
    """
    secrets = [s for s in secrets if s]
    if not secrets:
        raise EventRejected("no webhook secret is configured")
    if len(body) > MAX_BODY:
        raise EventRejected("body too large")
    ts_raw = _header(headers, TIMESTAMP_HEADER)
    sig_raw = _header(headers, SIGNATURE_HEADER)
    if not ts_raw or not sig_raw:
        raise EventRejected("missing timestamp or signature header")
    try:
        ts = int(ts_raw.strip())
    except ValueError:
        raise EventRejected("timestamp is not an integer") from None
    current = time.time() if now is None else now
    if abs(current - ts) > tolerance:
        raise EventRejected("timestamp outside the allowed window")
    candidates = []
    for part in sig_raw.split(","):
        scheme, _, value = part.strip().partition("=")
        if scheme == SCHEME and value:
            candidates.append(value.strip().lower())
    if not candidates:
        raise EventRejected("no v1 signature")
    for secret in secrets:
        expected = signature(secret, ts, body)
        if any(hmac.compare_digest(expected, c) for c in candidates):
            return ts
    raise EventRejected("signature mismatch")


def parse(body: bytes) -> Event | None:
    """The event, or None when its type doesn't call for a sync. Raises EventRejected for a
    body that isn't a webhook-payload v1 envelope. Call only after ``verify``."""
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        raise EventRejected("body is not JSON") from None
    if not isinstance(data, dict) or str(data.get("spec_version")) != "1":
        raise EventRejected("not a webhook-payload v1 envelope")
    event_type = data.get("type")
    event_id = clean_text(data.get("id"), 128)
    org = data.get("tenant_id")
    if not isinstance(event_type, str) or not event_id or not isinstance(org, str) or not org:
        raise EventRejected("envelope is missing id, type or tenant_id")
    if event_type not in SYNC_EVENT_TYPES:
        return None
    device = data.get("device")
    device_id = safe_id(device.get("id")) if isinstance(device, dict) else None
    if device_id is None:
        raise EventRejected("event has no usable device id")
    return Event(event_id, event_type, org, device_id)


def usable_secrets(raw: object) -> list[str]:
    """Secrets from configuration: a list of strings, or one comma-separated string.

    Raises ValueError (naming no value) when one is shorter than MIN_SECRET_LENGTH."""
    if raw in (None, "", [], ()):
        return []
    if isinstance(raw, str):
        items = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = None
    if items is None or not all(isinstance(s, str) for s in items):
        raise ValueError("webhook_secrets must be a list of strings")
    secrets = [s.strip() for s in items if s.strip()]
    if any(len(s) < MIN_SECRET_LENGTH for s in secrets):
        raise ValueError(f"each webhook secret must be at least {MIN_SECRET_LENGTH} characters")
    return secrets
