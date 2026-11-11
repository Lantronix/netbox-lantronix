"""Read console paths from NetBox for the coverage gate (ORM, inside NetBox).

Every query is restricted to what `user` may view: devices, console ports, cables, the SLC's
port and device, its Percepxion link and port facts, and its primary IP. A cable the user can't
view, or whose far end they can't view, is reported as peer_not_visible with no SLC details. An
SLC primary IP the user can't view is not used. user=None means unrestricted (shell use only).
"""

from .core import DEFAULT_SSH_BASE_PORT, ConsoleLink, Slc, Target


def _restrict(qs, user):
    return qs.restrict(user, "view") if user is not None else qs


def _visible_ids(model, ids, user) -> set:
    if not ids:
        return set()
    return set(_restrict(model.objects.filter(pk__in=ids), user).values_list("pk", flat=True))


def _primary_ip_pk(device):
    ip = device.primary_ip
    return ip.pk if ip is not None else None


def netbox_slc(device, cache: dict, link, ip_visible: bool = True) -> Slc:
    """`link` is the device's DeviceLink if the user may view it, else None. `ip_visible` says
    whether the user may view the device's primary IP."""
    if device.pk in cache:
        return cache[device.pk]
    synced = link is not None and link.status == "active"
    host, source = None, None
    if device.primary_ip is not None and ip_visible:
        host, source = str(device.primary_ip.address.ip), "primary_ip"
    elif link is not None and link.mgmt_ip:
        host, source = link.mgmt_ip, "percepxion_mgmt_ip"
    base = link.source.ssh_base_port if link is not None else DEFAULT_SSH_BASE_PORT
    slc = Slc(
        id=str(device.pk),
        name=device.name,
        synced=synced,
        host=host,
        host_source=source,
        ssh_base_port=base,
        manufacturer=device.device_type.manufacturer.slug,
        percepxion_id=link.percepxion_device_id if link is not None else None,
    )
    cache[device.pk] = slc
    return slc


def netbox_targets(user=None):
    """Targets for every device `user` may view, with console cables resolved."""
    from dcim.models import Cable, ConsolePort, ConsoleServerPort, Device
    from ipam.models import IPAddress

    from netbox_lantronix.models import DeviceLink, PortFacts

    qs = _restrict(Device.objects.all(), user)
    qs = qs.select_related("site", "role", "platform").prefetch_related("tags")
    ports: dict = {}
    for cp in _restrict(ConsolePort.objects.filter(device__in=qs), user):
        ports.setdefault(cp.device_id, []).append(cp)

    cable_ids = [cp.cable_id for cps in ports.values() for cp in cps if cp.cable_id]
    ok_cables = _visible_ids(Cable, cable_ids, user)
    peers = {}
    for cps in ports.values():
        for cp in cps:
            found = list(cp.link_peers or []) if cp.cable_id in ok_cables else []
            peers[cp.pk] = found[0] if found else None
    csps = [p for p in peers.values() if isinstance(p, ConsoleServerPort)]
    ok_ports = _visible_ids(ConsoleServerPort, [p.pk for p in csps], user)
    ok_devices = _visible_ids(Device, list({p.device_id for p in csps}), user)
    slc_ips = [_primary_ip_pk(p.device) for p in csps if p.device_id in ok_devices]
    ok_ips = _visible_ids(IPAddress, [i for i in slc_ips if i], user)
    links = {
        ln.device_id: ln
        for ln in _restrict(
            DeviceLink.objects.filter(device_id__in=ok_devices).select_related("source"), user
        )
    }
    facts = {f.port_id: f for f in _restrict(PortFacts.objects.filter(port_id__in=ok_ports), user)}

    cache: dict = {}
    out = []
    for d in qs:
        result = []
        for cp in sorted(ports.get(d.pk, []), key=lambda p: p.name):
            peer = peers.get(cp.pk)
            link = ConsoleLink(console_port=cp.name, cabled=bool(cp.cable_id))
            if cp.cable_id and cp.cable_id not in ok_cables:
                link.peer_visible = False
            elif isinstance(peer, ConsoleServerPort):
                if peer.pk in ok_ports and peer.device_id in ok_devices:
                    f = facts.get(peer.pk)
                    link.peer_is_console_server_port = True
                    ip_ok = _primary_ip_pk(peer.device) in ok_ips
                    link.slc = netbox_slc(peer.device, cache, links.get(peer.device_id), ip_ok)
                    link.slc_port = peer.name
                    link.port_state = (f.state or None) if f is not None else None
                else:
                    link.peer_visible = False
            result.append(link)
        out.append(
            Target(
                id=str(d.pk),
                name=d.name or f"device-{d.pk}",
                active=d.status == "active",
                site=d.site.slug if d.site_id else None,
                role=d.role.slug if d.role_id else None,
                platform=d.platform.slug if d.platform_id else None,
                tags=[t.slug for t in d.tags.all()],
                links=result,
            )
        )
    return out


def netbox_console_server_roles() -> list[str]:
    """Roles the plugin assigns to synced SLCs: the default exclusion for the gate."""
    from netbox_lantronix.models import PercepxionSource

    return sorted({s.role.slug for s in PercepxionSource.objects.select_related("role")})
