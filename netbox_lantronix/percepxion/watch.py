# Shared code: this file is the same in netbox-lantronix and nautobot-app-ssot-lantronix.
# scripts/check_shared.py fails CI when the copies differ, so change both.
"""Change watch: which console servers' ports changed since the last look.

Each port's telemetry state becomes a short fingerprint of the fields a sync writes. A console
server whose fingerprints differ from the previous run's needs a focused sync. A server with no
previous fingerprints is a baseline, not a change, so a restart doesn't sync the whole fleet.
"""

from __future__ import annotations

import hashlib

from .telemetry import PxPortState

FIELDS = ("state", "attached", "make", "os", "os_version", "model", "serial", "hostname")


def fingerprint(states: dict[int, PxPortState]) -> dict[str, str]:
    """Port number (as a string, so it survives a JSON cache) -> 16-hex-digit fingerprint."""
    out = {}
    for number, s in states.items():
        text = "\x1f".join(str(getattr(s, f)) for f in FIELDS)
        out[str(number)] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return out


def changed(previous: dict[str, dict[str, str]], current: dict[str, dict[str, str]]) -> set[str]:
    """Device IDs whose port fingerprints differ, including ports that appeared or went away.

    Devices only in ``current`` are new baselines; devices only in ``previous`` (no longer
    read) are ignored."""
    return {
        device_id
        for device_id, ports in current.items()
        if device_id in previous and previous[device_id] != ports
    }
