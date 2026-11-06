"""Prepare NetBox for netbox-lantronix. Safe to run again; existing objects aren't changed.

Imports the device types in docker/device-types, creates the site and role new console
servers go in, and creates the percepxion-sync user with permissions limited to that site.

Run from the netbox-docker checkout:
    docker compose exec netbox /opt/netbox/netbox/manage.py nbshell \
      -c "exec(open('/opt/netbox-lantronix-docker/setup.py').read())"

Optional environment variables, with defaults in brackets:
    NBL_SITE_SLUG [console-servers]  NBL_SITE_NAME [Console Servers]
    NBL_ROLE_SLUG [console-server]   NBL_ROLE_NAME [Console Server]
"""

import glob
import os
from decimal import Decimal

import yaml
from core.models import ObjectType
from dcim.models import (
    ConsolePortTemplate,
    ConsoleServerPort,
    ConsoleServerPortTemplate,
    Device,
    DeviceRole,
    DeviceType,
    InterfaceTemplate,
    Manufacturer,
    Platform,
    PowerPortTemplate,
    Site,
)
from users.models import ObjectPermission, User

from netbox_lantronix.bootstrap import ensure_objects

env = os.environ.get
site_slug = env("NBL_SITE_SLUG", "console-servers")
site, _ = Site.objects.get_or_create(
    slug=site_slug, defaults={"name": env("NBL_SITE_NAME", "Console Servers")}
)
role, _ = DeviceRole.objects.get_or_create(
    slug=env("NBL_ROLE_SLUG", "console-server"),
    defaults={"name": env("NBL_ROLE_NAME", "Console Server"), "color": "2196f3"},
)
manufacturer, platform, _ = ensure_objects()

COMPONENTS = {
    "console-ports": ConsolePortTemplate,
    "console-server-ports": ConsoleServerPortTemplate,
    "power-ports": PowerPortTemplate,
    "interfaces": InterfaceTemplate,
}
added = 0
for path in sorted(glob.glob("/opt/netbox-lantronix-docker/device-types/*.yaml")):
    with open(path) as handle:
        data = yaml.safe_load(handle)
    if DeviceType.objects.filter(manufacturer=manufacturer, model=data["model"]).exists():
        continue
    keys = ("model", "slug", "part_number", "u_height", "is_full_depth", "airflow", "weight",
            "weight_unit", "comments")
    fields = {key: data[key] for key in keys if key in data}
    if "weight" in fields:
        fields["weight"] = Decimal(str(fields["weight"]))
    device_type = DeviceType(manufacturer=manufacturer, **fields)
    device_type.full_clean()
    device_type.save()
    for key, model in COMPONENTS.items():
        for item in data.get(key, []):
            model.objects.create(device_type=device_type, **item)
    added += 1

user, _ = User.objects.get_or_create(username="percepxion-sync")
if user.has_usable_password():
    user.set_unusable_password()
    user.save()

PERMISSIONS = [
    ("netbox-lantronix sync: devices", [Device], ["view", "add", "change"],
     {"site__slug": site_slug}),
    ("netbox-lantronix sync: console server ports", [ConsoleServerPort], ["view", "change"],
     {"device__site__slug": site_slug}),
    ("netbox-lantronix sync: lookups", [DeviceType, Site, DeviceRole, Platform, Manufacturer],
     ["view"], None),
]
for name, models, actions, constraints in PERMISSIONS:
    permission, created = ObjectPermission.objects.get_or_create(
        name=name, defaults={"actions": actions, "constraints": constraints}
    )
    if created:
        permission.object_types.set([ObjectType.objects.get_for_model(m) for m in models])
    permission.users.add(user)

print(f"Setup complete: site={site.slug} role={role.slug} device types added={added}")
