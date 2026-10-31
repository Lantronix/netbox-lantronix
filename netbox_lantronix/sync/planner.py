"""Decide what one sync run changes, from snapshots. No NetBox or Django imports.

The job builds plain snapshots of NetBox state (NbDevice, NbPort) and reads Percepxion
records (PxDevice, PxPort); plan() returns every create, update and finding. Applying the
plan is a separate step (sync/apply.py), so this module is the whole decision surface and
is tested without a database.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace

from ..percepxion.records import PxDevice, PxPort
from ..percepxion.resolver import emg_sku, is_default_port_name, resolve_sku, slug_for
from ..sanitize import clean_text, safe_id, safe_ip, safe_name, safe_serial

LANTRONIX = "lantronix"
PLATFORM = "lantronix_itmgos"
LABEL_MAX = 64
DESCRIPTION_MAX = 200
FACT_MAX = 100
STATE_MAX = 30  # PortFacts.state max_length


@dataclass(frozen=True)
class NbDevice:
    pk: int
    name: str
    serial: str
    manufacturer: str  # manufacturer slug
    platform: str | None  # platform slug
    link_source: int | None = None  # source pk of an existing DeviceLink
    link_pxid: str | None = None
    link_status: str | None = None  # "active" or "pending"
    cf_pxid: str | None = None  # current percepxion_device_id custom field value
    site: int | None = None  # site pk


@dataclass(frozen=True)
class NbPort:
    device_pk: int
    name: str  # "Port-01"
    label: str
    description: str


@dataclass(frozen=True)
class SourceContext:
    pk: int
    organization_id: str
    site: int | None = None  # source default site pk
    overrides: dict[str, str] = field(default_factory=dict)  # pxid -> type slug
    rules: dict[tuple[str, int, int], str] = field(default_factory=dict)  # signature -> slug
    available_types: frozenset[str] = frozenset()  # Lantronix device-type slugs in NetBox


@dataclass(frozen=True)
class Finding:
    kind: str
    device_name: str
    pxid: str = ""
    detail: str = ""
    # (model, RJ45 count, USB count) for ambiguous models: lets the UI offer "Create rule".
    signature: tuple[str, int, int] | None = None


@dataclass(frozen=True)
class DeviceCreate:
    pxid: str
    name: str
    serial: str
    type_slug: str
    type_source: str


@dataclass(frozen=True)
class DeviceUpdate:
    pk: int
    pxid: str
    changes: dict[str, str]  # field -> new value: name, serial, platform, cf_pxid
    link: str  # "keep", "adopt" (create active link)


@dataclass(frozen=True)
class PortChange:
    label: str
    description: str


@dataclass
class Plan:
    creates: list[DeviceCreate] = field(default_factory=list)
    updates: list[DeviceUpdate] = field(default_factory=list)
    pending: list[tuple[int, str]] = field(default_factory=list)  # (device pk, pxid)
    seen: list[str] = field(default_factory=list)  # pxids returned this run, linked or created
    # Management IP Percepxion reports per device (validated), used by the SSH button when the
    # device has no NetBox primary IP. Never written to IPAM.
    mgmt_ips: dict[str, str] = field(default_factory=dict)
    unchanged: int = 0
    # Ports for existing devices: (device pk, port name) -> change. Only differing ports.
    port_changes: dict[tuple[int, str], PortChange] = field(default_factory=dict)
    # Ports for devices created this run: pxid -> port name -> desired values (non-empty only).
    new_device_ports: dict[str, dict[str, PortChange]] = field(default_factory=dict)
    # Facts: pxid -> port name -> facts dict (only ports that reported at least one fact).
    facts: dict[str, dict[str, dict]] = field(default_factory=dict)
    ports_unchanged: int = 0
    findings: list[Finding] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.creates
            or self.updates
            or self.pending
            or self.port_changes
            or self.new_device_ports
        )


def port_name(number: int) -> str:
    return f"Port-{number:02d}"


def resolve_type(
    dev: PxDevice, ctx: SourceContext, sysconfig: Callable[[str], str | None]
) -> tuple[str | None, str, list[str]]:
    """Return (slug, type_source, candidates). slug is None when unresolved.

    Order: device override, stored show sysconfig, source model rule, a single
    port-count match. EMG models map directly. ``sysconfig`` is called only when needed,
    so Percepxion output is fetched for unresolved SLC 9000 creates only.
    """
    if dev.device_id in ctx.overrides:
        return ctx.overrides[dev.device_id], "override", []
    emg = emg_sku(dev.model)
    if emg:
        return slug_for(emg), "ports", [slug_for(emg)]
    candidates = resolve_sku(dev.model, dev.rj45_ports, dev.usb_ports)
    if len(candidates) == 1:
        return candidates[0], "ports", candidates
    if candidates:
        slug = sysconfig(dev.device_id)
        if slug:
            return slug, "sysconfig", candidates
        rule = ctx.rules.get((dev.model.upper(), dev.rj45_ports, dev.usb_ports))
        if rule:
            return rule, "rule", candidates
    return None, "", candidates


def _desired_port(port: PxPort) -> PortChange:
    label = "" if is_default_port_name(port.name, port.number) else clean_text(port.name, LABEL_MAX)
    return PortChange(label=label, description=clean_text(port.hostname, DESCRIPTION_MAX))


def _facts(port: PxPort) -> dict:
    raw = dict(port.facts)
    attached = raw.pop("attached", None)
    facts: dict = {
        k: clean_text(v, STATE_MAX if k == "state" else FACT_MAX) for k, v in raw.items()
    }
    if attached is None:  # port search only: no state, facts when any were reported
        return facts if any(facts.values()) else {}
    # Telemetry state: an unplugged port carries only its state, so earlier facts stay as
    # history and the port shows nothing attached.
    facts["attached"] = attached == "yes"
    return facts


def plan(
    ctx: SourceContext,
    px_devices: Iterable[PxDevice],
    px_ports: Iterable[PxPort],
    nb_devices: Iterable[NbDevice],
    nb_ports: Iterable[NbPort],
    sysconfig: Callable[[str], str | None] = lambda pxid: None,
) -> Plan:
    result = Plan()
    nb_devices = list(nb_devices)
    by_link = {d.link_pxid: d for d in nb_devices if d.link_source == ctx.pk and d.link_pxid}
    by_serial: dict[str, list[NbDevice]] = {}
    by_name: dict[str, list[NbDevice]] = {}
    for d in nb_devices:
        if d.serial:
            by_serial.setdefault(d.serial, []).append(d)
        by_name.setdefault(d.name, []).append(d)

    # Screen Percepxion devices: organization, name safety, duplicate names.
    accepted: list[tuple[PxDevice, str]] = []
    for dev in px_devices:
        if ctx.organization_id not in dev.org_ids:
            result.findings.append(Finding("out_of_org", clean_text(dev.name, 64), dev.device_id))
            continue
        pxid, serial = safe_id(dev.device_id), safe_serial(dev.serial)
        if pxid is None or serial is None:
            result.findings.append(
                Finding(
                    "rejected_name",
                    clean_text(dev.name, 64),
                    "",
                    "Percepxion device ID or serial has unexpected characters",
                )
            )
            continue
        if serial != dev.serial:
            dev = replace(dev, serial=serial)
        name = safe_name(dev.name)
        if name is None:
            result.findings.append(
                Finding(
                    "rejected_name",
                    clean_text(dev.name, 64),
                    dev.device_id,
                    "Name has characters outside letters, digits, dot, underscore, hyphen",
                )
            )
            continue
        accepted.append((dev, name))
    name_counts = Counter(name for _, name in accepted)

    matched: dict[str, int] = {}  # pxid -> NetBox device pk (existing devices with active link)
    created: set[str] = set()
    for dev, name in accepted:
        if name_counts[name] > 1:
            result.findings.append(
                Finding(
                    "duplicate_name",
                    name,
                    dev.device_id,
                    "More than one Percepxion device has this name",
                )
            )
            continue
        target, link_action = _match(dev, name, ctx, by_link, by_serial, by_name, result)
        if target is None:
            if link_action == "skip":
                continue
            slug, source, candidates = resolve_type(dev, ctx, sysconfig)
            if slug is None:
                kind = "ambiguous_model" if candidates else "no_device_type"
                detail = (
                    "Candidates: "
                    + ", ".join(candidates)
                    + ". Run show sysconfig on the device from Percepxion, or add a model rule."
                    if candidates
                    else f"Model {clean_text(dev.model, 32)} has no device type"
                )
                signature = (
                    (clean_text(dev.model, 32).upper(), dev.rj45_ports, dev.usb_ports)
                    if candidates
                    else None
                )
                result.findings.append(Finding(kind, name, dev.device_id, detail[:500], signature))
                continue
            if slug not in ctx.available_types:
                result.findings.append(
                    Finding(
                        "no_device_type",
                        name,
                        dev.device_id,
                        f"Device type {slug} is not in NetBox",
                    )
                )
                continue
            result.creates.append(DeviceCreate(dev.device_id, name, dev.serial, slug, source))
            result.seen.append(dev.device_id)
            _record_ip(result, dev)
            created.add(dev.device_id)
            continue
        changes = _device_changes(target, dev, name, by_name)
        if changes is None:
            result.findings.append(
                Finding(
                    "conflict", name, dev.device_id, "Another NetBox device already has this name"
                )
            )
            changes = {}
        if changes or link_action == "adopt":
            result.updates.append(DeviceUpdate(target.pk, dev.device_id, changes, link_action))
        else:
            result.unchanged += 1
        matched[dev.device_id] = target.pk
        result.seen.append(dev.device_id)
        _record_ip(result, dev)

    # Devices this source linked before that Percepxion no longer returns.
    returned = {dev.device_id for dev, _ in accepted}
    for pxid, d in by_link.items():
        if pxid not in returned and d.link_status == "active":
            result.findings.append(Finding("missing", d.name, pxid, "Not returned by Percepxion"))

    _plan_ports(result, px_ports, nb_ports, matched, created)
    return result


def _record_ip(result: Plan, dev: PxDevice) -> None:
    ip = safe_ip(dev.ip)
    if ip:
        result.mgmt_ips[dev.device_id] = ip


def _match(dev, name, ctx, by_link, by_serial, by_name, result):
    """Return (NbDevice or None, link action). Action "skip" means record nothing more."""
    linked = by_link.get(dev.device_id)
    if linked is not None:
        if linked.link_status == "pending":
            result.findings.append(
                Finding(
                    "adoption_pending",
                    name,
                    dev.device_id,
                    "Approve the pending link to start syncing",
                )
            )
            return None, "skip"
        return linked, "keep"

    if dev.serial:
        same_serial = [d for d in by_serial.get(dev.serial, []) if d.manufacturer == LANTRONIX]
        if len(same_serial) == 1:
            d = same_serial[0]
            if d.link_source is not None:
                result.findings.append(
                    Finding(
                        "conflict",
                        name,
                        dev.device_id,
                        "Matching serial is linked to another source",
                    )
                )
                return None, "skip"
            if d.platform not in (None, PLATFORM):
                result.findings.append(
                    Finding(
                        "conflict",
                        name,
                        dev.device_id,
                        f"Matching serial has platform {d.platform}",
                    )
                )
                return None, "skip"
            if ctx.site is not None and d.site != ctx.site:
                # Outside this source's site: never adopt without approval.
                result.pending.append((d.pk, dev.device_id))
                result.findings.append(
                    Finding(
                        "adoption_pending",
                        name,
                        dev.device_id,
                        "Matching serial is in another site; approve to link",
                    )
                )
                return None, "skip"
            return d, "adopt"
        if len(same_serial) > 1:
            result.findings.append(
                Finding("conflict", name, dev.device_id, "Several NetBox devices have this serial")
            )
            return None, "skip"

    same_name = by_name.get(name, [])
    if not same_name:
        return None, "create"
    if len(same_name) > 1:
        result.findings.append(
            Finding("conflict", name, dev.device_id, "Several NetBox devices have this name")
        )
        return None, "skip"
    d = same_name[0]
    reason = None
    if d.link_source is not None:
        reason = "Same-named device is linked to another Percepxion device"
    elif d.manufacturer != LANTRONIX:
        reason = f"Same-named device is manufacturer {d.manufacturer}"
    elif d.platform not in (None, PLATFORM):
        reason = f"Same-named device has platform {d.platform}"
    elif d.serial and d.serial != dev.serial:
        reason = "Same-named device has a different serial"
    if reason:
        result.findings.append(Finding("conflict", name, dev.device_id, reason))
        return None, "skip"
    # Same name, Lantronix, empty serial: never adopt without approval.
    result.pending.append((d.pk, dev.device_id))
    result.findings.append(
        Finding(
            "adoption_pending",
            name,
            dev.device_id,
            "Same-named device has no serial; approve to link",
        )
    )
    return None, "skip"


def _device_changes(nb: NbDevice, dev: PxDevice, name: str, by_name) -> dict[str, str] | None:
    """Fields to change on a matched device, or None if a rename would collide."""
    changes: dict[str, str] = {}
    if nb.name != name:
        if any(other.pk != nb.pk for other in by_name.get(name, [])):
            return None
        changes["name"] = name
    if dev.serial and nb.serial != dev.serial:
        changes["serial"] = dev.serial
    if nb.platform is None:
        changes["platform"] = PLATFORM
    if nb.cf_pxid != dev.device_id:
        changes["cf_pxid"] = dev.device_id
    return changes


def _plan_ports(result: Plan, px_ports, nb_ports, matched, created) -> None:
    current = {(p.device_pk, p.name): p for p in nb_ports}
    for port in px_ports:
        if port.number <= 0:
            continue
        pname = port_name(port.number)
        desired = _desired_port(port)
        facts = _facts(port)
        if port.device_id in created:
            if desired.label or desired.description:
                result.new_device_ports.setdefault(port.device_id, {})[pname] = desired
        elif port.device_id in matched:
            existing = current.get((matched[port.device_id], pname))
            if existing is None:
                continue
            if (existing.label, existing.description) != (desired.label, desired.description):
                result.port_changes[(existing.device_pk, pname)] = desired
            else:
                result.ports_unchanged += 1
        else:
            continue
        if facts:
            result.facts.setdefault(port.device_id, {})[pname] = facts
