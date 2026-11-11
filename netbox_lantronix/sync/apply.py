"""Build NetBox snapshots for the planner and apply a plan atomically."""

from __future__ import annotations

import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone
from netbox.context import current_request, events_queue, query_cache
from netbox.context_managers import event_tracking
from utilities.request import NetBoxFakeRequest

from ..bootstrap import CUSTOM_FIELD, ensure_objects
from ..models import (
    DeviceLink,
    LinkStatusChoices,
    PortFacts,
    SyncFinding,
)
from .planner import Finding, NbDevice, NbPort, Plan, SourceContext

FINDINGS_KEEP_RUNS = 20
# Port states that mean nothing is plugged in.
IDLE_STATES = frozenset({"", "Disconnected", "Unknown"})


def build_context(source) -> SourceContext:
    from dcim.models import DeviceType

    return SourceContext(
        pk=source.pk,
        organization_id=source.organization_id,
        site=source.site_id,
        overrides={
            o.percepxion_device_id: o.device_type.slug
            for o in source.overrides.select_related("device_type")
        },
        rules={
            (r.model.upper(), r.rj45_ports, r.usb_ports): r.device_type.slug
            for r in source.rules.select_related("device_type")
        },
        available_types=frozenset(
            DeviceType.objects.filter(manufacturer__slug="lantronix").values_list("slug", flat=True)
        ),
    )


def build_snapshot(source, px_devices) -> tuple[list[NbDevice], list[NbPort]]:
    """NetBox devices the planner might match (by link, serial or name) and their console ports."""
    from dcim.models import ConsoleServerPort, Device

    # Lower-cased: NetBox treats device names as unique per site without regard to case.
    names = {d.name.strip().lower() for d in px_devices if d.name and d.name.strip()}
    serials = {d.serial.strip() for d in px_devices if d.serial and d.serial.strip()}
    qs = (
        Device.objects.annotate(name_lower=Lower("name"))
        .filter(Q(name_lower__in=names) | Q(serial__in=serials) | Q(percepxion_link__source=source))
        .select_related("device_type__manufacturer", "platform", "percepxion_link")
        .distinct()
    )
    devices = []
    for d in qs:
        link = getattr(d, "percepxion_link", None)
        devices.append(
            NbDevice(
                pk=d.pk,
                name=d.name or "",
                serial=d.serial or "",
                manufacturer=d.device_type.manufacturer.slug,
                platform=d.platform.slug if d.platform_id else None,
                link_source=link.source_id if link else None,
                link_pxid=link.percepxion_device_id if link else None,
                link_status=link.status if link else None,
                cf_pxid=(d.custom_field_data or {}).get(CUSTOM_FIELD),
                site=d.site_id,
            )
        )
    ports = [
        NbPort(p.device_id, p.name, p.label or "", p.description or "")
        for p in ConsoleServerPort.objects.filter(device_id__in=[d.pk for d in devices])
    ]
    return devices, ports


class SyncPermissionDenied(Exception):
    """The sync user's NetBox object permissions don't allow a planned write."""


def _check(model, user, action, pk):
    """Enforce the sync user's object permissions on a written object (NetBox view pattern)."""
    if not model.objects.restrict(user, action).filter(pk=pk).exists():
        raise SyncPermissionDenied(f"{user} may not {action} {model._meta.verbose_name} {pk}")


def fake_request(user):
    return NetBoxFakeRequest(
        {
            "id": uuid.uuid4(),
            "user": user,
            "META": {},
            "COOKIES": {},
            "POST": {},
            "GET": {},
            "FILES": {},
            "path": "",
            "method": "POST",
        }
    )


@contextmanager
def _tracking(user):
    """NetBox change logging as ``user``. NetBox's event_tracking clears its context vars only
    on a clean exit, so clear them here too: a failed run must not leave the fake request set."""
    try:
        with event_tracking(fake_request(user)):
            yield
    finally:
        current_request.set(None)
        events_queue.set({})
        query_cache.set(None)


def _update_device(device, upd, platform, user, counts) -> None:
    from dcim.models import Device

    if not upd.changes:
        return
    # Before and after: the target must be in scope both ways (NetBox view pattern).
    _check(Device, user, "change", device.pk)
    device.snapshot()
    if "name" in upd.changes:
        device.name = upd.changes["name"]
    if "serial" in upd.changes:
        device.serial = upd.changes["serial"]
    if "platform" in upd.changes:
        device.platform = platform
    if "cf_pxid" in upd.changes:
        device.custom_field_data[CUSTOM_FIELD] = upd.changes["cf_pxid"]
    device.full_clean()
    device.save()
    _check(Device, user, "change", device.pk)
    counts["devices"]["updated"] += 1


def _changeable(user, device_pks) -> set[int]:
    """The given devices the user's object permissions let them change. One query."""
    from dcim.models import Device

    pks = set(device_pks)
    if not pks:
        return set()
    return set(
        Device.objects.restrict(user, "change").filter(pk__in=pks).values_list("pk", flat=True)
    )


def _rejected(plan: Plan, name: str, pxid: str, exc: ValidationError) -> None:
    """Record a device NetBox's own validation refused; the rest of the run goes on."""
    reason = "; ".join(exc.messages)[:300]
    plan.findings.append(Finding("conflict", name, pxid, f"NetBox rejected the device: {reason}"))


def apply_plan(plan: Plan, source, user) -> dict:
    """Write the plan in one transaction. Returns run counts.

    A permission failure raises and rolls the whole run back. A device that fails NetBox
    validation is skipped with a finding (added to ``plan.findings``) and the run goes on.
    """
    now = timezone.now()
    counts = {
        "devices": {"created": 0, "updated": 0, "unchanged": plan.unchanged, "pending": 0},
        "ports": {"updated": 0, "unchanged": plan.ports_unchanged},
        "facts": {"updated": 0},
    }
    with _tracking(user), transaction.atomic():
        # Inside the transaction and the change log: a failed run leaves nothing behind.
        _, platform, _ = ensure_objects()
        _apply_pending(plan, source, user, counts)
        pk_by_pxid, rejected_pks = _apply_updates(plan, source, user, platform, now, counts)
        pk_by_pxid.update(_apply_creates(plan, source, user, platform, now, counts))
        _apply_ports(plan, user, pk_by_pxid, rejected_pks, counts)

        # Facts also come from devices with no device or port change this run.
        for pxid, device_pk in DeviceLink.objects.filter(
            source=source, percepxion_device_id__in=plan.seen, status=LinkStatusChoices.ACTIVE
        ).values_list("percepxion_device_id", "device_id"):
            pk_by_pxid.setdefault(pxid, device_pk)
        # Port facts and link details are written only for devices the sync user may change.
        in_scope = _changeable(user, pk_by_pxid.values())
        pk_by_pxid = {pxid: pk for pxid, pk in pk_by_pxid.items() if pk in in_scope}
        counts["facts"]["updated"] = _apply_facts(plan, pk_by_pxid, now)
        _apply_link_details(plan, source, in_scope, now)
    return counts


def _apply_pending(plan: Plan, source, user, counts) -> None:
    """Record adoptions that wait for approval."""
    # A device the sync user can't change gets no pending link: approving it would make
    # every later run fail, and the link would keep other sources off the device.
    in_scope = _changeable(user, [pk for pk, _ in plan.pending])
    for pk, pxid in plan.pending:
        if pk not in in_scope:
            plan.findings.append(
                Finding(
                    "conflict",
                    "",
                    pxid,
                    "A matching NetBox device is outside the sync user's permissions",
                )
            )
            continue
        _, made = DeviceLink.objects.get_or_create(
            device_id=pk,
            defaults={
                "source": source,
                "percepxion_device_id": pxid,
                "status": LinkStatusChoices.PENDING,
            },
        )
        counts["devices"]["pending"] += int(made)


def _apply_updates(plan: Plan, source, user, platform, now, counts):
    """Update matched devices and link adopted ones.

    Returns (pxid -> device pk for the devices written or linked, pks NetBox rejected)."""
    from dcim.models import Device

    pk_by_pxid: dict[str, int] = {}
    rejected_pks: set[int] = set()
    for upd in plan.updates:
        device = Device.objects.get(pk=upd.pk)
        try:
            with transaction.atomic():
                _update_device(device, upd, platform, user, counts)
        except ValidationError as exc:
            _rejected(plan, upd.changes.get("name") or device.name or "", upd.pxid, exc)
            rejected_pks.add(device.pk)
            continue
        if upd.link == "adopt":
            DeviceLink.objects.create(
                source=source, device=device, percepxion_device_id=upd.pxid, last_seen=now
            )
        pk_by_pxid[upd.pxid] = device.pk
    return pk_by_pxid, rejected_pks


def _apply_creates(plan: Plan, source, user, platform, now, counts) -> dict[str, int]:
    """Create new devices with their links. Returns pxid -> device pk for those created."""
    from dcim.models import Device, DeviceType

    created: dict[str, int] = {}
    types = {
        t.slug: t for t in DeviceType.objects.filter(slug__in={c.type_slug for c in plan.creates})
    }
    for c in plan.creates:
        device = Device(
            name=c.name,
            device_type=types[c.type_slug],
            role=source.role,
            site=source.site,
            platform=platform,
            serial=c.serial,
            status="active",
            custom_field_data={CUSTOM_FIELD: c.pxid},
        )
        try:
            with transaction.atomic():
                device.full_clean()
                device.save()
        except ValidationError as exc:
            _rejected(plan, c.name, c.pxid, exc)
            continue
        _check(Device, user, "add", device.pk)
        DeviceLink.objects.create(
            source=source,
            device=device,
            percepxion_device_id=c.pxid,
            type_source=c.type_source,
            last_seen=now,
        )
        created[c.pxid] = device.pk
        counts["devices"]["created"] += 1
    return created


def _apply_ports(plan: Plan, user, pk_by_pxid, rejected_pks, counts) -> None:
    """Write port labels and descriptions: existing devices (diffed by the planner) and
    devices created this run."""
    from dcim.models import ConsoleServerPort

    targets = {k: v for k, v in plan.port_changes.items() if k[0] not in rejected_pks}
    for pxid, ports in plan.new_device_ports.items():
        if pxid not in pk_by_pxid:
            continue  # the device was rejected
        for pname, change in ports.items():
            targets[(pk_by_pxid[pxid], pname)] = change
    if not targets:
        return
    port_qs = ConsoleServerPort.objects.filter(
        device_id__in={pk for pk, _ in targets}, name__in={n for _, n in targets}
    )
    for port in port_qs:
        change = targets.get((port.device_id, port.name))
        if change is None or (port.label, port.description) == (
            change.label,
            change.description,
        ):
            continue
        _check(ConsoleServerPort, user, "change", port.pk)
        port.snapshot()
        port.label, port.description = change.label, change.description
        port.full_clean()
        port.save()
        _check(ConsoleServerPort, user, "change", port.pk)
        counts["ports"]["updated"] += 1


def _apply_link_details(plan: Plan, source, in_scope, now) -> None:
    """Refresh each seen link's last-seen time, management address and firmware."""
    # last_seen is bookkeeping: a queryset update writes no change record.
    DeviceLink.objects.filter(source=source, percepxion_device_id__in=plan.seen).update(
        last_seen=now
    )
    for link in DeviceLink.objects.filter(
        source=source, percepxion_device_id__in=plan.seen, device_id__in=in_scope
    ):
        ip = plan.mgmt_ips.get(link.percepxion_device_id, "")
        firmware = plan.firmware.get(link.percepxion_device_id) or link.firmware_version
        if ip != link.mgmt_ip or firmware != link.firmware_version:
            link.snapshot()
            link.mgmt_ip = ip
            link.firmware_version = firmware
            link.save()


def _apply_facts(plan: Plan, pk_by_pxid, now) -> int:
    from dcim.models import ConsoleServerPort

    updated = 0
    for pxid, ports in plan.facts.items():
        device_pk = pk_by_pxid.get(pxid)
        if device_pk is None:
            continue
        for port in ConsoleServerPort.objects.filter(device_id=device_pk, name__in=list(ports)):
            facts = ports[port.name]
            attached = facts.get("attached", True)
            existing = PortFacts.objects.filter(port=port).first()
            if existing is None and not attached and facts.get("state") in IDLE_STATES:
                continue  # nothing was ever reported here; don't create an empty row
            if existing and all(getattr(existing, k) == v for k, v in facts.items()):
                if attached:
                    PortFacts.objects.filter(pk=existing.pk).update(last_reported=now)
                continue
            obj = existing or PortFacts(port=port, last_reported=now)
            if existing:
                obj.snapshot()
            for k, v in facts.items():
                setattr(obj, k, v)
            if attached:  # last_reported dates the facts, not the state
                obj.last_reported = now
            obj.full_clean()
            obj.save()
            updated += 1
    return updated


def record_findings(plan: Plan, source, job) -> dict:
    from core.models import Job

    # A reschedule can delete a periodic job's row while its run is in progress; keep the
    # findings anyway, unattached.
    if job is not None and not Job.objects.filter(pk=job.pk).exists():
        job = None
    SyncFinding.objects.bulk_create(
        SyncFinding(
            source=source,
            job=job,
            kind=f.kind,
            device_name=f.device_name[:64],
            percepxion_device_id=f.pxid[:64],
            detail=f.detail[:500],
            model=f.signature[0] if f.signature else "",
            rj45_ports=f.signature[1] if f.signature else None,
            usb_ports=f.signature[2] if f.signature else None,
        )
        for f in plan.findings
    )
    keep_jobs = list(
        SyncFinding.objects.filter(source=source)
        .exclude(job=None)
        .values_list("job_id", flat=True)
        .distinct()
        .order_by("-job_id")[:FINDINGS_KEEP_RUNS]
    )
    SyncFinding.objects.filter(source=source).exclude(job=None).exclude(
        job_id__in=keep_jobs
    ).delete()
    # Findings whose job row was deleted mid-run are kept for 30 days.
    SyncFinding.objects.filter(
        source=source, job=None, created__lt=timezone.now() - timedelta(days=30)
    ).delete()
    return dict(Counter(f.kind for f in plan.findings))
