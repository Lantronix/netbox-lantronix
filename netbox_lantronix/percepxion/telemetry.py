"""Per-port attached-device state from Percepxion's device telemetry view.

Port search reports stale connection status and keeps a previously cabled device's facts on a
port after it's unplugged. The telemetry view reports each port's discovery state and whether a
device is attached, so attached-device facts come from here when it answers.

The view also carries full running and startup configurations and tech-support text. Only the
fields in ``KEEP`` are read; everything else is dropped here, before any caller sees it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from .records import PxPort

_DP_INFO = re.compile(r"^telemetry/port:(\d+)/status_record/dp_info$")
KEEP = frozenset(
    {
        "status",
        "managed_device",
        "md_type",
        "md_version",
        "md_model",
        "md_sn",
        "md_hostname",
        "md_ipv4_mgmt",
        "md_lastseen",
    }
)


@dataclass(frozen=True)
class PxPortState:
    number: int
    state: str
    attached: bool
    make: str = ""
    os: str = ""
    os_version: str = ""
    model: str = ""
    serial: str = ""
    hostname: str = ""
    ip: str = ""
    last_seen: str = ""


def _split_type(md_type: str) -> tuple[str, str]:
    # "Cisco IOS" -> ("Cisco", "IOS"); the field is "make and OS" in one string.
    make, _, os_name = md_type.strip().partition(" ")
    return make, os_name.strip()


def parse_port_states(raw: Any) -> dict[int, PxPortState]:
    """Map port number to state for every dp_info group in a telemetry view response."""
    states: dict[int, PxPortState] = {}
    groups = raw.get("status_record") if isinstance(raw, dict) else None
    for group in groups or []:
        if not isinstance(group, dict):
            continue
        match = _DP_INFO.match(str(group.get("name") or ""))
        if not match:
            continue
        values: dict[str, str] = {}
        for item in group.get("items") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("name") or "").rsplit("/", 1)[-1]
            if key in KEEP:
                values[key] = str(item.get("value") or "").strip()
        attached = values.get("managed_device", "").lower() == "yes"
        number = int(match.group(1))
        if not attached:
            states[number] = PxPortState(number, values.get("status", ""), False)
            continue
        make, os_name = _split_type(values.get("md_type", ""))
        states[number] = PxPortState(
            number=number,
            state=values.get("status", ""),
            attached=True,
            make=make,
            os=os_name,
            os_version=values.get("md_version", ""),
            model=values.get("md_model", ""),
            serial=values.get("md_sn", ""),
            hostname=values.get("md_hostname", ""),
            ip=values.get("md_ipv4_mgmt", ""),
            last_seen=values.get("md_lastseen", ""),
        )
    return states


def merge_state(port: PxPort, state: PxPortState | None) -> PxPort:
    """Replace a port-search record's attached-device data with telemetry state.

    No state (the view failed or skipped the port): the port-search record is kept as is.
    Not attached: hostname cleared, facts carry only the state, so the port shows nothing
    attached and earlier facts stay as history.
    """
    if state is None:
        return port
    if not state.attached:
        return replace(port, hostname="", facts={"state": state.state, "attached": "no"})
    return replace(
        port,
        hostname=state.hostname,
        facts={
            "make": state.make,
            "model": state.model,
            "serial": state.serial,
            "os": state.os,
            "os_version": state.os_version,
            "state": state.state,
            "attached": "yes",
        },
    )
