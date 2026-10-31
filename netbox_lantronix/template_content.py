"""Panels and buttons on NetBox device and console server port pages.

Rendering never contacts Percepxion: both console buttons are plain links.
"""

import ipaddress
import re
from urllib.parse import quote

from netbox.plugins import PluginTemplateExtension

from .models import LinkStatusChoices
from .sanitize import safe_ip

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


def console_urls(port):
    """Return (ssh_url, percepxion_url) for a console server port, either may be None."""
    device = port.device
    link = _link(device)
    match = _PORT.match(port.name or "")
    if link is None or match is None:
        return None, None
    number = int(match.group(1))
    ssh_url = None
    raw = str(device.primary_ip.address.ip) if device.primary_ip else link.mgmt_ip
    clean = safe_ip(raw)
    ip = ipaddress.ip_address(clean) if clean else None
    if ip is not None:
        host = f"[{ip}]" if ip.version == 6 else str(ip)
        ssh_url = f"ssh://{host}:{link.source.ssh_base_port + number}"
    px_url = None
    template = (link.source.endpoint_config or {}).get("web_port_url")
    if link.source.percepxion_link and template and template.lower().startswith("https://"):
        px_url = template.format(
            device_id=quote(link.percepxion_device_id, safe=""),
            device_name=quote(device.name or "", safe=""),
            port_number=number,
        )
    return ssh_url, px_url


class ConsoleServerPortExtension(PluginTemplateExtension):
    models = ["dcim.consoleserverport"]

    def buttons(self):
        port = self.context["object"]
        link = _link(port.device)
        if link is None or not _can_view(self.context, link):
            return ""
        ssh_url, px_url = console_urls(port)
        if not (ssh_url or px_url):
            return ""
        return self.render(
            "netbox_lantronix/inc/port_buttons.html",
            extra_context={"ssh_url": ssh_url, "px_url": px_url},
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
