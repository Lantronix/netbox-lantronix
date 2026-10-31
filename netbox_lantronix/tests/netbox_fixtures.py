"""Shared setup for tests that run under the NetBox test runner (manage.py test)."""

from core.models import ObjectType
from dcim.models import (
    ConsoleServerPortTemplate,
    DeviceRole,
    DeviceType,
    Manufacturer,
    Site,
)
from users.models import ObjectPermission, User

from ..models import PercepxionSource
from ..percepxion.records import PxDevice, PxPort

ORG = "org-0001"
TYPE_SLUG = "lantronix-slc9032"


def make_world(sync_superuser=True):
    """Lantronix manufacturer, one SLC9032 type with 3 console ports, site, role, source, users."""
    mfr = Manufacturer.objects.create(name="Lantronix", slug="lantronix")
    dtype = DeviceType.objects.create(manufacturer=mfr, model="SLC9032", slug=TYPE_SLUG)
    for i in (1, 2, 3):
        ConsoleServerPortTemplate.objects.create(device_type=dtype, name=f"Port-{i:02d}")
    site = Site.objects.create(name="Lab", slug="lab")
    other_site = Site.objects.create(name="Other", slug="other")
    role = DeviceRole.objects.create(name="Console server", slug="console-server")
    sync_user = User.objects.create(username="percepxion-sync", is_superuser=sync_superuser)
    source = PercepxionSource.objects.create(
        name="test",
        endpoint="percepxion-sandbox",
        organization_id=ORG,
        site=site,
        role=role,
        sync_interval=0,
    )
    return {
        "mfr": mfr,
        "dtype": dtype,
        "site": site,
        "other_site": other_site,
        "role": role,
        "user": sync_user,
        "source": source,
    }


def grant(user, models, actions, constraints=None, name=None):
    perm = ObjectPermission.objects.create(
        name=name or f"{user.username} {'-'.join(actions)} {len(models)}",
        actions=list(actions),
        constraints=constraints,
    )
    perm.object_types.set([ObjectType.objects.get_for_model(m) for m in models])
    perm.users.add(user)
    return perm


def px_device(pxid="dev-0001", name="SLC-A", serial="000000000001"):
    return PxDevice(pxid, name, serial, "SLC9032", 32, 0, frozenset({ORG}), "192.0.2.10", "SLC")


def px_port(number, name=None, hostname="", pxid="dev-0001", **facts):
    return PxPort(pxid, number, name or f"Port-{number:02d}", hostname, facts, frozenset({ORG}))
