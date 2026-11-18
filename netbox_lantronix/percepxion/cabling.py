# Shared code: this file is the same in netbox-lantronix and nautobot-app-ssot-lantronix.
# scripts/check_shared.py fails CI when the copies differ, so change both.
"""Decide console cable changes from what the SLC reports on each port. No ORM imports.

With cable management on, Percepxion is the system of record for console cables on a synced
SLC's ports: the SLC sees the line signals and the attached device, so its report wins over a
cable someone drew earlier. The rules are deliberately narrow:

- Delete a port's cable only when the port is Disconnected (no line signals). A port that is
  Identifying, Unidentified or Authenticating has something plugged in that discovery hasn't
  named yet; its cable is left alone.
- Create a cable only when the SLC reports an attached device that matches exactly one
  device by serial, or failing that exactly one by hostname, and that device has one clear
  console port. The attached device reports its own hostname, so a hostname-only match
  creates a cable only between two free ports; replacing or moving a cable takes a serial.
- Callers limit the candidate devices to the sync's own scope (the SLCs' sites or locations,
  and what the sync user may change), so a reported serial or hostname can't reach a device
  outside it.
- A device that turns up on a different port moves: its old cable is removed only when that
  old port is one this run manages and the SLC reports it Disconnected in the same run. The
  serial and hostname are what the attached device says about itself, a claim rather than
  proof, so one report can't pull a cable off a port that still has something plugged in.
  A console port cabled to something else (a patch panel, a port outside the sync) is never
  touched; that is a finding.
- Ports without a telemetry state this run (the view failed or skipped the device) are never
  changed, and neither is a cable that doesn't end on a console port (a patch panel).

Keys are opaque and hashable (ORM primary keys in practice), so both integrations use the
same decision code with their own snapshots.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from dataclasses import dataclass, field

DISCONNECTED = "disconnected"
CONSOLE_NAMES = ("console", "con0", "con 0", "serial console")


@dataclass(frozen=True)
class ObservedPort:
    """One SLC port as the telemetry view reported it this run."""

    slc: Hashable  # SLC device key
    port: Hashable  # SLC console server port key
    state: str
    attached: bool
    serial: str = ""
    hostname: str = ""
    label: str = ""  # human-readable "SLC Port-03", for findings and journal text


@dataclass(frozen=True)
class ConsolePortRef:
    key: Hashable
    name: str
    # The console server port at the other end of its cable, None if uncabled, OTHER if the
    # cable reaches anything that isn't a console server port.
    cabled_to: Hashable | None = None


OTHER = object()


@dataclass(frozen=True)
class Target:
    """A device that could be the one attached to an SLC port."""

    key: Hashable
    name: str
    serial: str
    console_ports: tuple[ConsolePortRef, ...] = ()


@dataclass(frozen=True)
class Delete:
    port: Hashable  # console server port whose cable goes
    reason: str  # "disconnected", "replaced" or "moved"
    label: str = ""


@dataclass(frozen=True)
class Create:
    port: Hashable  # console server port
    target: Hashable  # device key
    console_port: Hashable
    label: str = ""
    target_name: str = ""
    matched_on: str = ""  # "serial" or "hostname"


@dataclass(frozen=True)
class CableFinding:
    kind: str
    label: str
    detail: str


@dataclass
class CablePlan:
    deletes: list[Delete] = field(default_factory=list)
    creates: list[Create] = field(default_factory=list)
    findings: list[CableFinding] = field(default_factory=list)
    unchanged: int = 0

    def is_empty(self) -> bool:
        return not (self.deletes or self.creates)


def _norm(value: str) -> str:
    return (value or "").strip().casefold()


def _unique(items: list) -> object | None:
    return items[0] if len(items) == 1 else None


def _console_port(target: Target) -> ConsolePortRef | None:
    ports = list(target.console_ports)
    if len(ports) == 1:
        return ports[0]
    named = [p for p in ports if _norm(p.name) in CONSOLE_NAMES]
    return _unique(named)


def plan_cables(
    observed: Iterable[ObservedPort],
    cables: dict[Hashable, Hashable | None],
    targets: Iterable[Target],
) -> CablePlan:
    """Return the cable changes for one run.

    observed: every managed SLC port that has a telemetry state this run.
    cables: console server port key -> the console port key at the far end of its cable,
        OTHER when the cable reaches something else, None or absent when uncabled.
    targets: candidate attached devices, with their console ports and where those are cabled.
    """
    observed = list(observed)
    managed = {o.port for o in observed}
    disconnected = {o.port for o in observed if not o.attached and _norm(o.state) == DISCONNECTED}
    result = CablePlan()
    by_serial: dict[str, list[Target]] = {}
    by_name: dict[str, list[Target]] = {}
    for t in targets:
        if _norm(t.serial):
            by_serial.setdefault(_norm(t.serial), []).append(t)
        if _norm(t.name):
            by_name.setdefault(_norm(t.name), []).append(t)

    deleting: set[Hashable] = set()
    claimed: dict[Hashable, str] = {}  # console port key -> observed label that claimed it

    def delete(port, reason, label):
        if port not in deleting:
            deleting.add(port)
            result.deletes.append(Delete(port, reason, label))

    # Ports that report a device go first, so a device that moved is settled before the
    # Disconnected port it left is visited.
    order = sorted(observed, key=lambda o: 0 if o.attached else 1)
    for o in order:
        current = cables.get(o.port)
        if current is OTHER:
            # Patch panels and other cabling infrastructure are never the SLC's to judge.
            if o.attached or _norm(o.state) == DISCONNECTED:
                result.findings.append(
                    CableFinding(
                        "port_cabled_elsewhere",
                        o.label,
                        "The SLC port's cable doesn't end on a console port; left as is",
                    )
                )
            result.unchanged += 1
            continue
        if not o.attached:
            if _norm(o.state) == DISCONNECTED and current is not None:
                delete(o.port, "disconnected", o.label)
            else:
                result.unchanged += 1
            continue
        target, matched_on = _match(o, by_serial, by_name, result)
        if target is None:
            result.unchanged += 1
            continue
        cp = _console_port(target)
        if cp is None:
            detail = (
                f"{target.name} has no console port"
                if not target.console_ports
                else f"{target.name} has several console ports and none is named Console"
            )
            result.findings.append(CableFinding("console_port_unclear", o.label, detail))
            result.unchanged += 1
            continue
        if cp.key in claimed:
            result.findings.append(
                CableFinding(
                    "duplicate_attachment",
                    o.label,
                    f"{target.name} is also reported on {claimed[cp.key]}; no cable changed",
                )
            )
            continue
        claimed[cp.key] = o.label
        if current == cp.key:
            result.unchanged += 1
            continue
        if cp.cabled_to is OTHER or (cp.cabled_to is not None and cp.cabled_to not in managed):
            result.findings.append(
                CableFinding(
                    "console_port_cabled_elsewhere",
                    o.label,
                    f"{target.name} {cp.name} is cabled to something outside this sync; "
                    "remove that cable to let the SLC's report win",
                )
            )
            result.unchanged += 1
            continue
        if cp.cabled_to is not None and cp.cabled_to != o.port and cp.cabled_to not in disconnected:
            result.findings.append(
                CableFinding(
                    "still_reported_elsewhere",
                    o.label,
                    f"{target.name} {cp.name} is cabled to another SLC port that isn't "
                    "Disconnected; no cable changed until that port reports Disconnected",
                )
            )
            result.unchanged += 1
            continue
        if matched_on == "hostname" and (current is not None or cp.cabled_to is not None):
            # A device can report any hostname it likes; only a serial match may remove cables.
            result.findings.append(
                CableFinding(
                    "hostname_only_match",
                    o.label,
                    f"{target.name} matched on hostname only, and a cable would have to be "
                    "removed; set its serial number in the source of truth to let the sync do it",
                )
            )
            result.unchanged += 1
            continue
        if current is not None:
            delete(o.port, "replaced", o.label)
        if cp.cabled_to is not None and cp.cabled_to != o.port:
            delete(cp.cabled_to, "moved", o.label)
        result.creates.append(Create(o.port, target.key, cp.key, o.label, target.name, matched_on))
    return result


def _match(o: ObservedPort, by_serial, by_name, result: CablePlan):
    """(Target, "serial" | "hostname"), or (None, "") with a finding when it's ambiguous."""
    serial, host = _norm(o.serial), _norm(o.hostname)
    if not serial and not host:
        return None, ""  # attached, but discovery hasn't named it yet
    if serial:
        same = by_serial.get(serial, [])
        if len(same) == 1:
            return same[0], "serial"
        if len(same) > 1:
            result.findings.append(
                CableFinding("attached_ambiguous", o.label, "Several devices have this serial")
            )
            return None, ""
    if host:
        same = by_name.get(host, [])
        if len(same) == 1:
            return same[0], "hostname"
        if len(same) > 1:
            result.findings.append(
                CableFinding("attached_ambiguous", o.label, "Several devices have this name")
            )
            return None, ""
    result.findings.append(
        CableFinding(
            "attached_unmatched",
            o.label,
            "No device matches the attached device's serial or hostname",
        )
    )
    return None, ""
