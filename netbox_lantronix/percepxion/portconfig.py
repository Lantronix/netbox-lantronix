# Shared code: this file is the same in netbox-lantronix and nautobot-app-ssot-lantronix.
# scripts/check_shared.py fails CI when the copies differ, so change both.
"""Each device port's SSH TCP port, from the device configuration Percepxion holds.

The console server lets every device port have its own SSH port (``set deviceport port <n>
sshport <tcp port>``) and lets SSH to a port be turned off (``sshin``). Port search doesn't
report either, so they are read from the stored configuration.

That configuration is the unit's whole settings tree. Percepxion masks its password fields,
but SNMP community strings and other operational detail are in it. Only the two fields in
``KEEP``, of the ``dpgen<n>`` records, are read; the rest is dropped here, before any caller
sees it, and nothing from it is logged or stored.
"""

from __future__ import annotations

import re
from typing import Any

_PORT_RECORD = re.compile(r"^telemetry/cm/config_record/dpgen([1-9]\d{0,2})$")
KEEP = frozenset({"sshport", "sshin"})
SSH_OFF = 0


def _tcp_port(value: Any) -> int | None:
    text = str(value).strip()
    if not text.isascii() or not text.isdigit() or len(text) > 5:
        return None
    number = int(text)
    return number if 1 <= number <= 65535 else None


def parse_port_ssh(raw: Any) -> dict[int, int]:
    """Map device port number to its SSH TCP port; ``SSH_OFF`` where SSH to the port is off.

    A port whose settings are missing or malformed is left out, so the caller falls back to
    its configured default for that port.
    """
    ports: dict[int, int] = {}
    records = raw.get("config_record") if isinstance(raw, dict) else None
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        match = _PORT_RECORD.match(str(record.get("name") or ""))
        if not match:
            continue
        number = int(match.group(1))
        prefix = f"dpgen{number}_"
        values: dict[str, Any] = {}
        fields = record.get("field")
        for item in fields if isinstance(fields, list) else []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("name") or "").rsplit("/", 1)[-1].removeprefix(prefix)
            if key in KEEP:
                values[key] = item.get("value")
        enabled = str(values.get("sshin") or "").strip().lower()
        tcp_port = _tcp_port(values.get("sshport"))
        if enabled == "disable":
            ports[number] = SSH_OFF
        elif enabled == "enable" and tcp_port is not None:
            ports[number] = tcp_port
    return ports
