"""Build NetBox snapshots for the planner and apply a plan atomically."""

from __future__ import annotations

import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
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
from .planner import NbDevice, NbPort, Plan, SourceContext

FINDINGS_KEEP_RUNS = 20


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

    names = {d.name for d in px_devices if d.name}
    serials = {d.serial.strip() for d in px_devices if d.serial and d.serial.strip()}
    qs = (
        Device.objects.filter(
            Q(name__in=names) | Q(serial__in=serials) | Q(percepxion_link__source=source)
        )
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


def apply_plan(plan: Plan, source, user) -> dict:
    """Write the plan in one transaction. Returns run counts. Raises on any failure."""
    from dcim.models import ConsoleServerPort, Device, DeviceType

    _, platform, _ = ensure_objects()
    now = timezone.now()
    counts = {
        "devices": {"created": 0, "updated": 0, "unchanged": plan.unchanged, "pending": 0},
        "ports": {"updated": 0, "unchanged": plan.ports_unchanged},
        "facts": {"updated": 0},
    }
    with _tracking(user), transaction.atomic():
        for pk, pxid in plan.pending:
            _, made = DeviceLink.objects.get_or_create(
                device_id=pk,
                defaults={
                    "source": source,
                    "percepxion_device_id": pxid,
                    "status": LinkStatusChoices.PENDING,
                },
            )
            counts["devices"]["pending"] += int(made)

        pk_by_pxid: dict[str, int] = {}
        for upd in plan.updates:
            device = Device.objects.get(pk=upd.pk)
            if upd.changes:
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
            if upd.link == "adopt":
                DeviceLink.objects.create(
                    source=source, device=device, percepxion_device_id=upd.pxid, last_seen=now
                )
            pk_by_pxid[upd.pxid] = device.pk

        types = {
            t.slug: t
            for t in DeviceType.objects.filter(slug__in={c.type_slug for c in plan.creates})
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
            device.full_clean()
            device.save()
            _check(Device, user, "add", device.pk)
            DeviceLink.objects.create(
                source=source,
                device=device,
                percepxion_device_id=c.pxid,
                type_source=c.type_source,
                last_seen=now,
            )
            pk_by_pxid[c.pxid] = device.pk
            counts["devices"]["created"] += 1

        # Ports: existing devices (diffed by the planner) and devices created above.
        targets = dict(plan.port_changes)
        for pxid, ports in plan.new_device_ports.items():
            for pname, change in ports.items():
                targets[(pk_by_pxid[pxid], pname)] = change
        if targets:
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

        # Facts also come from devices with no device or port change this run.
        for pxid, device_pk in DeviceLink.objects.filter(
            source=source, percepxion_device_id__in=plan.seen, status=LinkStatusChoices.ACTIVE
        ).values_list("percepxion_device_id", "device_id"):
            pk_by_pxid.setdefault(pxid, device_pk)
        counts["facts"]["updated"] = _apply_facts(plan, pk_by_pxid, now)

        # last_seen is bookkeeping: a queryset update writes no change record.
        DeviceLink.objects.filter(source=source, percepxion_device_id__in=plan.seen).update(
            last_seen=now
        )
        for link in DeviceLink.objects.filter(source=source, percepxion_device_id__in=plan.seen):
            ip = plan.mgmt_ips.get(link.percepxion_device_id, "")
            if ip != link.mgmt_ip:
                link.snapshot()
                link.mgmt_ip = ip
                link.save()
    return counts


IDLE_STATES = frozenset({"", "Disconnected", "Unknown"})


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
