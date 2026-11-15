"""Panels and buttons on NetBox device and console server port pages.

Rendering never contacts Percepxion: both console buttons are plain links.
"""

import ipaddress
import re
from urllib.parse import quote

from netbox.plugins import PluginTemplateExtension

from .models import LinkStatusChoices
from .sanitize import host_ip

_PORT = re.compile(r"^Port-0*(\d+)$")


def _link(device):
    link = getattr(device, "percepxion_link", None)
    if link is None or link.status != LinkStatusChoices.ACTIVE:
        return None
    return link


def _can_view(context, obj):
    request = context.get("request")
    user = getattr(request, "user", None)
    return bool(user) and user.has_perm(f"{obj._meta.app_label}.view_{obj._meta.model_name}", obj)


def ssh_port(link, number):
    """The TCP port that opens an SSH session to device port ``number``, or None.

    The unit's own setting for the port, as last read from Percepxion, wins. None when SSH
    to the port is turned off on the unit. A port with no setting on record falls back to
    the source's base port plus the port number, the unit's factory numbering.
    """
    stored = link.ssh_ports if isinstance(link.ssh_ports, dict) else {}
    tcp_port = stored.get(str(number))
    if tcp_port is None:
        tcp_port = link.source.ssh_base_port + number
    if isinstance(tcp_port, bool) or not isinstance(tcp_port, int) or not 1 <= tcp_port <= 65535:
        return None
    return tcp_port


def console_urls(port):
    """Return (ssh_url, percepxion_url, ssh_target) for a console server port.

    Any of them may be None. ``ssh_target`` is the host and port the SSH link opens, shown
    on the button so the user sees where it goes before they sign in."""
    device = port.device
    link = _link(device)
    match = _PORT.match(port.name or "")
    if link is None or match is None:
        return None, None, None
    number = int(match.group(1))
    ssh_url = ssh_target = None
    raw = str(device.primary_ip.address.ip) if device.primary_ip else link.mgmt_ip
    clean = host_ip(raw)
    ip = ipaddress.ip_address(clean) if clean else None
    tcp_port = ssh_port(link, number)
    if ip is not None and tcp_port is not None:
        host = f"[{ip}]" if ip.version == 6 else str(ip)
        ssh_target = f"{host}:{tcp_port}"
        ssh_url = f"ssh://{ssh_target}"
    px_url = None
    template = (link.source.endpoint_config or {}).get("web_port_url")
    if link.source.percepxion_link and template and template.lower().startswith("https://"):
        try:
            px_url = template.format(
                device_id=quote(link.percepxion_device_id, safe=""),
                device_name=quote(device.name or "", safe=""),
                port_number=number,
            )
        except (KeyError, IndexError, ValueError):
            px_url = None  # a mistyped web_port_url must not break the port page
    return ssh_url, px_url, ssh_target


def _viewable(user, model, pk) -> bool:
    return model.objects.restrict(user, "view").filter(pk=pk).exists()


def server_port_urls(user, port):
    """``console_urls`` for a console server port, or all None unless ``user`` may view the
    port and the console server's Percepxion link."""
    link = _link(port.device)
    if link is None or not user.has_perm("netbox_lantronix.view_devicelink", link):
        return None, None, None
    if not _viewable(user, type(port), port.pk):
        return None, None, None
    return console_urls(port)


def device_console_paths(user, device) -> list[dict]:
    """Console access to ``device`` itself: one entry per console port of the device that is
    cabled to a synced console server port the user may view.

    The cable, the far port and the console server's link are each checked against the
    user's view permissions; anything they can't see gives no entry.
    """
    from dcim.models import Cable, ConsolePort, ConsoleServerPort

    paths = []
    ports = ConsolePort.objects.restrict(user, "view").filter(device=device, cable__isnull=False)
    for console_port in ports.order_by("name"):
        if not _viewable(user, Cable, console_port.cable_id):
            continue
        for peer in console_port.link_peers:
            if not isinstance(peer, ConsoleServerPort):
                continue
            ssh_url, px_url, ssh_target = server_port_urls(user, peer)
            if ssh_url or px_url:
                paths.append(
                    {
                        "console_port": console_port,
                        "server_port": peer,
                        "ssh_url": ssh_url,
                        "px_url": px_url,
                        "ssh_target": ssh_target,
                    }
                )
    return paths


def served_ports(user, device) -> list[dict]:
    """The console server's ports worth listing on its own page: those with a cable in NetBox
    or an attached device reported by Percepxion, each with its console links."""
    from dcim.models import ConsoleServerPort

    rows = []
    ports = ConsoleServerPort.objects.restrict(user, "view").filter(device=device)
    for port in ports.select_related("percepxion_facts", "cable"):
        facts = getattr(port, "percepxion_facts", None)
        if not port.cable_id and not (facts is not None and facts.attached):
            continue
        ssh_url, px_url, ssh_target = server_port_urls(user, port)
        peers = (
            [p for p in port.link_peers if _viewable(user, type(p), p.pk)] if port.cable_id else []
        )
        rows.append(
            {
                "port": port,
                "peer": peers[0] if peers else None,
                "ssh_url": ssh_url,
                "px_url": px_url,
                "ssh_target": ssh_target,
            }
        )
    return rows


class ConsoleServerPortExtension(PluginTemplateExtension):
    models = ["dcim.consoleserverport"]

    def buttons(self):
        port = self.context["object"]
        link = _link(port.device)
        if link is None or not _can_view(self.context, link):
            return ""
        ssh_url, px_url, ssh_target = console_urls(port)
        if not (ssh_url or px_url):
            return ""
        return self.render(
            "netbox_lantronix/inc/port_buttons.html",
            extra_context={"ssh_url": ssh_url, "px_url": px_url, "ssh_target": ssh_target},
        )

    def right_page(self):
        facts = getattr(self.context["object"], "percepxion_facts", None)
        if facts is None or not _can_view(self.context, facts):
            return ""
        return self.render("netbox_lantronix/inc/port_facts.html", extra_context={"facts": facts})


class DeviceExtension(PluginTemplateExtension):
    models = ["dcim.device"]

    def buttons(self):
        """Open console on the page of a device whose console port is cabled to a synced
        console server port, so nobody has to find that port first."""
        user = getattr(self.context.get("request"), "user", None)
        if not user:
            return ""
        paths = device_console_paths(user, self.context["object"])
        if not paths:
            return ""
        return self.render(
            "netbox_lantronix/inc/device_console_buttons.html",
            extra_context={"paths": paths, "several": len(paths) > 1},
        )

    def right_page(self):
        device = self.context["object"]
        link = getattr(device, "percepxion_link", None)
        if link is None or not _can_view(self.context, link):
            return ""
        rows = []
        if link.status == LinkStatusChoices.ACTIVE:
            rows = served_ports(self.context["request"].user, device)
        return self.render(
            "netbox_lantronix/inc/device_panel.html", extra_context={"link": link, "rows": rows}
        )


template_extensions = [ConsoleServerPortExtension, DeviceExtension]
