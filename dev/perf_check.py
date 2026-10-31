"""Performance check: synthetic 500-device / 24,000-port fleet through planner + apply.

Run inside dev NetBox (never a production instance):
    manage.py nbshell < dev/perf_check.py
Everything runs in one transaction that is rolled back at the end, so the database is unchanged.
"""

import time

from dcim.models import DeviceRole, DeviceType, Site
from django.db import transaction
from users.models import User

from netbox_lantronix.models import ModelRule, PercepxionSource
from netbox_lantronix.percepxion.records import PxDevice, PxPort
from netbox_lantronix.sync.apply import apply_plan, build_context, build_snapshot
from netbox_lantronix.sync.planner import plan

DEVICES, PORTS, ORG = 500, 48, "perf-org"


class Rollback(Exception):
    pass


def fleet(rename=False):
    devices, ports = [], []
    for d in range(DEVICES):
        pxid = f"perf-{d:04d}"
        devices.append(
            PxDevice(pxid, f"PERF-{d:04d}", f"PERF{d:08d}", "SLC9048", PORTS, 0,
                     frozenset({ORG}), f"10.{d // 250}.{d % 250}.1", "SLC")
        )
        for n in range(1, PORTS + 1):
            attached = n % 4 == 0  # a quarter of ports report an attached device
            facts = {"make": "cisco", "model": "C9300-48P", "serial": f"FOC{d:04d}{n:02d}",
                     "os": "ios-xe", "os_version": "17.9.4"} if attached else {}
            name = f"sw-{d}-{n}" + ("-new" if rename and n == 1 else "")
            ports.append(PxPort(pxid, n, name if attached or rename else f"Port-{n:02d}",
                                name if attached else "", facts, frozenset({ORG})))
    return devices, ports


def run(source, user, devices, ports):
    t0 = time.monotonic()
    ctx = build_context(source)
    nb_devices, nb_ports = build_snapshot(source, devices)
    p = plan(ctx, devices, ports, nb_devices, nb_ports)
    t1 = time.monotonic()
    counts = apply_plan(p, source, user)
    t2 = time.monotonic()
    return t1 - t0, t2 - t1, counts


candidates = DeviceType.objects.filter(manufacturer__slug="lantronix", model__startswith="SLC9048")
dtype = next(t for t in candidates if t.consoleserverporttemplates.count() == PORTS)
user = User.objects.get(username="percepxion-sync")
results = []
try:
    with transaction.atomic():
        user.is_superuser = True  # rolled back with everything else
        user.save()
        site = Site.objects.create(name="perf-check", slug="perf-check")
        role = DeviceRole.objects.get_or_create(slug="console-server",
                                                defaults={"name": "Console server"})[0]
        source = PercepxionSource.objects.create(
            name="perf-check", endpoint=next(iter(PercepxionSource.objects.values_list(
                "endpoint", flat=True))), organization_id=ORG, site=site, role=role,
            sync_interval=0, enabled=False)
        ModelRule.objects.create(source=source, model="SLC9048", rj45_ports=PORTS, usb_ports=0,
                                 device_type=dtype)
        devices, ports = fleet()
        results.append(("first run", *run(source, user, devices, ports)))
        results.append(("no-change run", *run(source, user, devices, ports)))
        devices, ports = fleet(rename=True)
        results.append(("18,000 label changes", *run(source, user, devices, ports)))
        raise Rollback
except Rollback:
    pass

print(f"type={dtype.slug} devices={DEVICES} ports={DEVICES * PORTS}")
for label, plan_s, apply_s, counts in results:
    print(f"{label:18} plan {plan_s:6.1f}s  apply {apply_s:6.1f}s  total {plan_s + apply_s:6.1f}s  "
          f"devices={counts.get('devices')} ports={counts.get('ports')}")
