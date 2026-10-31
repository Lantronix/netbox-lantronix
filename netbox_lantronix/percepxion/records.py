"""Plain records read from Percepxion. No NetBox or Django imports.

String fields are kept raw; sanitizing happens in the sync planner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _to_int(value: Any) -> int:
    """Percepxion returns counts as strings ("16"); missing or junk becomes 0."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _org_ids(tenants: Any) -> frozenset[str]:
    if not isinstance(tenants, list):
        return frozenset()
    return frozenset(
        str(t["id"]) for t in tenants if isinstance(t, dict) and t.get("id") not in (None, "")
    )


def _str_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


@dataclass(frozen=True)
class PxDevice:
    device_id: str
    name: str
    serial: str
    model: str
    rj45_ports: int
    usb_ports: int
    org_ids: frozenset[str]
    ip: str | None
    product_family: str

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> PxDevice:
        attrs = raw.get("attributes") or {}
        return cls(
            device_id=str(raw.get("device_id", "")),
            name=str(raw.get("device_name") or ""),
            serial=str(raw.get("serial_num") or ""),
            model=str(attrs.get("model") or ""),
            rj45_ports=_to_int(attrs.get("num_rj45_ports")),
            usb_ports=_to_int(attrs.get("num_usb_ports")),
            org_ids=_org_ids(raw.get("tenant")),
            ip=_str_or_none(attrs.get("ipaddress")),
            product_family=str(attrs.get("product_family") or ""),
        )


@dataclass(frozen=True)
class PxPort:
    device_id: str
    number: int
    name: str
    hostname: str
    facts: dict[str, str] = field(default_factory=dict)
    org_ids: frozenset[str] = frozenset()

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> PxPort:
        hw = raw.get("hardware") or {}
        net = raw.get("network") or {}
        return cls(
            device_id=str(raw.get("parent_device_id", "")),
            number=_to_int(raw.get("port_number")),
            name=str(raw.get("name") or ""),
            hostname=str(net.get("hostname") or ""),
            facts={
                "make": str(hw.get("make") or ""),
                "model": str(hw.get("model") or ""),
                "serial": str(hw.get("serial_number") or ""),
                "os": str(hw.get("os") or ""),
                "os_version": str(hw.get("os_version") or ""),
            },
            org_ids=_org_ids(raw.get("tenant")),
        )
