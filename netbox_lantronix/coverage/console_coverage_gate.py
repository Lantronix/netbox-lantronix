"""Console coverage gate for NetBox. Read only.

Upload this file under Customization > Scripts > Add (or sync it from a data source, or copy it
into SCRIPTS_ROOT). It needs the netbox-lantronix plugin installed: the gate logic and the NetBox
reader come from netbox_lantronix.coverage. The lantronix.oob Ansible role oob_coverage_gate
finds this script by its class name, ConsoleCoverageGate.
"""

import json

from dcim.models import DeviceRole, Site
from extras.models import Tag
from extras.scripts import BooleanVar, MultiObjectVar, ObjectVar, Script, StringVar
from utilities.exceptions import AbortScript

from netbox_lantronix.coverage.core import (
    DEFAULT_READY_STATES,
    Scope,
    coverage_report,
    split_list,
    summary_lines,
)
from netbox_lantronix.coverage.orm import netbox_console_server_roles, netbox_targets


def _slugs(model, value, user):
    """Slugs for an ObjectVar/MultiObjectVar value.

    The UI form hands the script model instances; the REST API hands it raw IDs (or slugs).
    IDs are resolved through the requesting user's view permission, and anything that doesn't
    resolve aborts the run rather than silently widening the scope.
    """
    if value in (None, "", []):
        return []
    items = value if isinstance(value, (list, tuple)) or hasattr(value, "model") else [value]
    out = []
    for item in items:
        if hasattr(item, "slug"):
            out.append(item.slug)
            continue
        qs = model.objects.restrict(user, "view")
        key = str(item)
        obj = (qs.filter(pk=int(key)) if key.isdigit() else qs.filter(slug=key)).first()
        if obj is None:
            raise AbortScript(f"{model.__name__} {key!r} not found or not visible")
        out.append(obj.slug)
    return out


class ConsoleCoverageGate(Script):
    site = ObjectVar(model=Site, required=False)
    roles = MultiObjectVar(model=DeviceRole, required=False, label="Roles")
    tags = MultiObjectVar(model=Tag, required=False)
    devices = StringVar(
        required=False,
        label="Device names",
        description="Comma separated. Empty means every active device in scope",
    )
    ready_states = StringVar(
        required=False,
        default="Managed, Identified",
        description="Port states that count as a ready console path",
    )
    include_console_servers = BooleanVar(
        required=False,
        default=False,
        description="Also grade the SLCs themselves (their own console cables)",
    )

    class Meta:
        name = "Console coverage gate"
        description = (
            "Devices in scope with no console cable to a synced SLC port, or whose "
            "port isn't Managed or Identified. Returns JSON (aoob.console-coverage/v1)"
        )
        commit_default = False
        scheduling_enabled = True

    def run(self, data, commit):
        user = getattr(getattr(self, "request", None), "user", None)
        if user is None:
            raise AbortScript("No requesting user; refusing to report unrestricted data")
        scope = Scope(
            site=next(iter(_slugs(Site, data.get("site"), user)), None),
            roles=_slugs(DeviceRole, data.get("roles"), user),
            tags=_slugs(Tag, data.get("tags"), user),
            devices=split_list(data.get("devices")),
            exclude_roles=[]
            if data.get("include_console_servers")
            else netbox_console_server_roles(),
        )
        ready = split_list(data.get("ready_states")) or list(DEFAULT_READY_STATES)
        report = coverage_report("netbox", netbox_targets(user), scope, ready)
        for line in summary_lines(report):
            if line.startswith("FAIL"):
                self.log_failure(line)
            elif line.startswith("ok"):
                self.log_success(line)
            else:
                self.log_info(line)
        return json.dumps(report, indent=2)
