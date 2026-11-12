# Shared code: this file is the same in netbox-lantronix and nautobot-app-ssot-lantronix.
# scripts/check_shared.py fails CI when the copies differ, so change both.
"""Console path records and the console coverage gate (report schema aoob.console-coverage/v1).

Standard library only, so the gate logic is tested without NetBox or Nautobot. The ORM reader
is in orm.py; the NetBox script and the Nautobot job that run the gate live outside this package.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

SCHEMA = "aoob.console-coverage/v1"
DEFAULT_READY_STATES = ("Managed", "Identified")
DEFAULT_SSH_BASE_PORT = 3000
PORT_NAME = re.compile(r"^Port-0*(\d+)$")

NO_CONSOLE_PORT = "no_console_port"
NO_CONSOLE_CABLE = "no_console_cable"
PEER_NOT_CONSOLE_SERVER = "peer_not_console_server"
PEER_NOT_VISIBLE = "peer_not_visible"
SLC_NOT_SYNCED = "slc_not_synced"
PORT_NAME_UNPARSED = "port_name_unparsed"
NO_SLC_HOST = "no_slc_host"
PORT_STATE_UNKNOWN = "port_state_unknown"
PORT_STATE_NOT_READY = "port_state_not_ready"

# Order a device's console ports are ranked in when none is covered: the one that got furthest
# along explains the device best.
_PROGRESS = (
    NO_CONSOLE_CABLE,
    PEER_NOT_VISIBLE,
    PEER_NOT_CONSOLE_SERVER,
    SLC_NOT_SYNCED,
    PORT_NAME_UNPARSED,
    NO_SLC_HOST,
    PORT_STATE_UNKNOWN,
    PORT_STATE_NOT_READY,
)


@dataclass
class Slc:
    """A console server as the source of truth describes it."""

    id: str
    name: str
    synced: bool
    host: str | None = None
    host_source: str | None = None  # "primary_ip" or "percepxion_mgmt_ip"
    ssh_base_port: int = DEFAULT_SSH_BASE_PORT
    manufacturer: str | None = None  # slug or name, lower case, e.g. "lantronix"
    percepxion_id: str | None = None

    @property
    def lantronix(self) -> bool:
        return (self.manufacturer or "").lower() == "lantronix"


@dataclass
class ConsoleLink:
    """One console port on a target and whatever its cable reaches."""

    console_port: str
    cabled: bool = False
    # False when the requesting user can't view the far end; its SLC details are then withheld.
    peer_visible: bool = True
    peer_is_console_server_port: bool = False
    slc: Slc | None = None
    slc_port: str | None = None
    port_state: str | None = None


@dataclass
class Target:
    id: str
    name: str
    active: bool = True
    site: str | None = None
    role: str | None = None
    platform: str | None = None
    tags: list[str] = field(default_factory=list)
    links: list[ConsoleLink] = field(default_factory=list)


@dataclass
class Scope:
    site: str | None = None
    roles: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    devices: list[str] = field(default_factory=list)
    exclude_roles: list[str] = field(default_factory=list)

    def matches(self, t: Target) -> bool:
        if not t.active:
            return False
        if self.devices and t.name not in self.devices:
            return False
        if self.site and t.site != self.site:
            return False
        if self.roles and t.role not in self.roles:
            return False
        if self.tags and not set(self.tags) & set(t.tags):
            return False
        if t.role is not None and t.role in self.exclude_roles:
            return False
        return True

    def as_dict(self) -> dict:
        return {
            "site": self.site,
            "roles": list(self.roles),
            "tags": list(self.tags),
            "devices": list(self.devices),
            "exclude_roles": list(self.exclude_roles),
        }


def port_number(name: str | None) -> int | None:
    m = PORT_NAME.match(name or "")
    return int(m.group(1)) if m else None


def _link_record(t: Target, link: ConsoleLink | None, ready_states) -> dict:
    slc = link.slc if link else None
    number = port_number(link.slc_port) if link else None
    base = slc.ssh_base_port if slc else DEFAULT_SSH_BASE_PORT
    rec = {
        "device": t.name,
        "device_id": str(t.id),
        "site": t.site,
        "role": t.role,
        "platform": t.platform,
        "console_port": link.console_port if link else None,
        "slc": slc.name if slc else None,
        "slc_id": str(slc.id) if slc else None,
        "slc_port": link.slc_port if link else None,
        "slc_port_number": number,
        "slc_host": slc.host if slc else None,
        "slc_host_source": slc.host_source if slc and slc.host else None,
        "ssh_base_port": base,
        "ssh_port": base + number if number is not None else None,
        "slc_synced": bool(slc and slc.synced),
        "port_state": link.port_state if link else None,
    }
    reasons = []
    if link is None:
        reasons.append(NO_CONSOLE_PORT)
    elif not link.cabled:
        reasons.append(NO_CONSOLE_CABLE)
    elif not link.peer_visible:
        reasons.append(PEER_NOT_VISIBLE)
    elif not link.peer_is_console_server_port or slc is None:
        reasons.append(PEER_NOT_CONSOLE_SERVER)
    else:
        if not slc.synced:
            reasons.append(SLC_NOT_SYNCED)
        if number is None:
            reasons.append(PORT_NAME_UNPARSED)
        if not slc.host:
            reasons.append(NO_SLC_HOST)
        if not link.port_state:
            reasons.append(PORT_STATE_UNKNOWN)
        elif link.port_state not in ready_states:
            reasons.append(PORT_STATE_NOT_READY)
    rec["covered"] = not reasons
    rec["reasons"] = reasons
    return rec


def _rank(rec: dict) -> tuple:
    if rec["covered"]:
        return (len(_PROGRESS) + 1, 0)
    furthest = max(_PROGRESS.index(r) for r in rec["reasons"]) if rec["reasons"] else -1
    return (furthest, -len(rec["reasons"]))


def console_path(t: Target, ready_states=DEFAULT_READY_STATES) -> dict:
    """The console path record for one target. With several console ports, the best one wins."""
    if not t.links:
        return _link_record(t, None, ready_states)
    records = [_link_record(t, link, ready_states) for link in t.links]
    return max(records, key=_rank)


def coverage_report(
    source: str,
    targets,
    scope: Scope,
    ready_states=DEFAULT_READY_STATES,
    now: datetime | None = None,
) -> dict:
    ready = list(ready_states)
    records = [console_path(t, ready) for t in targets if scope.matches(t)]
    records.sort(key=lambda r: (r["covered"], r["device"]))
    covered = sum(1 for r in records if r["covered"])
    # Named devices that matched nothing (typo, inactive, not visible, or filtered out by
    # site/role/tag) fail the gate; dropping them silently would let a typo pass.
    matched = {r["device"] for r in records}
    not_found = [n for n in dict.fromkeys(scope.devices) if n not in matched]
    when = (now or datetime.now(UTC)).astimezone(UTC)
    return {
        "schema": SCHEMA,
        "source": source,
        "generated": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope": scope.as_dict(),
        "ready_states": ready,
        "result": "pass" if covered == len(records) and not not_found else "fail",
        "summary": {
            "in_scope": len(records),
            "covered": covered,
            "uncovered": len(records) - covered,
        },
        "devices": records,
        "not_found": not_found,
    }


def split_list(text: str | None) -> list[str]:
    """Comma or newline separated input from a script/job form field."""
    return [p.strip() for p in re.split(r"[,\n]", text or "") if p.strip()]


def summary_lines(report: dict) -> list[str]:
    """Short human-readable lines for a job log."""
    s = report["summary"]
    lines = [
        f"{report['result'].upper()}: {s['covered']}/{s['in_scope']} in-scope devices "
        f"have a ready console path"
    ]
    for r in report["devices"]:
        if r["covered"]:
            lines.append(
                f"ok   {r['device']}: {r['slc']} {r['slc_port']} "
                f"(ssh port {r['ssh_port']}, {r['port_state']})"
            )
        else:
            where = f" via {r['slc']} {r['slc_port']}" if r["slc"] else ""
            state = f" state={r['port_state']}" if r["port_state"] else ""
            lines.append(f"FAIL {r['device']}{where}{state}: {', '.join(r['reasons'])}")
    for name in report.get("not_found", []):
        lines.append(f"FAIL {name}: not_found")
    return lines
