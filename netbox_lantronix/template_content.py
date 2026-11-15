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

    def right_page(self):
        link = getattr(self.context["object"], "percepxion_link", None)
        if link is None or not _can_view(self.context, link):
            return ""
        return self.render("netbox_lantronix/inc/device_panel.html", extra_context={"link": link})


template_extensions = [ConsoleServerPortExtension, DeviceExtension]
