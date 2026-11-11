"""The coverage gate reports only what the user may view (fake ORM, user limited to one site)."""

import sys
import types

import pytest


class QS:
    def __init__(self, items):
        self.items = list(items)

    def all(self):
        return QS(self.items)

    def select_related(self, *a):
        return self

    def prefetch_related(self, *a):
        return self

    def filter(self, **kw):
        items = self.items
        for key, val in kw.items():
            field, _, op = key.partition("__")
            vals = {o.pk for o in val} if isinstance(val, QS) else set(val)
            attr = {"device": "device_id", "pk": "pk"}.get(field, field)
            items = [o for o in items if getattr(o, attr) in vals]
        return QS(items)

    def restrict(self, user, action):
        assert action == "view"
        return QS(o for o in self.items if user.can_view(o))

    def values_list(self, field, flat=False):
        return [getattr(o, field) for o in self.items]

    def __iter__(self):
        return iter(self.items)


class Obj:
    registry: dict = {}

    def __init_subclass__(cls):
        cls.rows = []
        cls.objects = types.SimpleNamespace(
            all=lambda: QS(cls.rows), filter=lambda **kw: QS(cls.rows).filter(**kw)
        )

    def __init__(self, **kw):
        self.__dict__.update(kw)
        type(self).rows.append(self)


class Device(Obj):
    pass


class ConsolePort(Obj):
    pass


class ConsoleServerPort(Obj):
    pass


class DeviceLink(Obj):
    pass


class PortFacts(Obj):
    pass


class Cable(Obj):
    pass


class IPAddress(Obj):
    pass


class User:
    def __init__(self, site):
        self.site = site

    def can_view(self, o):
        dev = o if isinstance(o, Device) else getattr(o, "device", None)
        if dev is None and hasattr(o, "port"):
            dev = o.port.device
        return dev.site.slug == self.site


def ns(**kw):
    return types.SimpleNamespace(**kw)


@pytest.fixture
def netbox(monkeypatch):
    for cls in (Device, ConsolePort, ConsoleServerPort, DeviceLink, PortFacts, Cable, IPAddress):
        cls.rows.clear()
    monkeypatch.setitem(sys.modules, "dcim", types.ModuleType("dcim"))
    monkeypatch.setitem(
        sys.modules,
        "dcim.models",
        ns(
            Device=Device, ConsolePort=ConsolePort, ConsoleServerPort=ConsoleServerPort, Cable=Cable
        ),
    )
    monkeypatch.setitem(sys.modules, "ipam", types.ModuleType("ipam"))
    monkeypatch.setitem(sys.modules, "ipam.models", ns(IPAddress=IPAddress))
    monkeypatch.setitem(
        sys.modules, "netbox_lantronix.models", ns(DeviceLink=DeviceLink, PortFacts=PortFacts)
    )
    a, b = ns(slug="site-a"), ns(slug="site-b")
    common = dict(
        status="active",
        role_id=1,
        role=ns(slug="sw"),
        platform_id=1,
        platform=ns(slug="cisco_ios"),
        tags=QS([]),
        primary_ip=None,
        primary_ip_id=None,
        site_id=1,
        device_type=ns(manufacturer=ns(slug="lantronix")),
    )
    slc_a = Device(pk=1, name="slc-a", site=a, **common)
    slc_b = Device(pk=2, name="slc-b", site=b, **common)
    t_a = Device(pk=3, name="target-a", site=a, **common)
    t_b = Device(pk=4, name="target-b", site=b, **common)
    t_a_cross = Device(pk=5, name="target-a-on-slc-b", site=a, **common)
    t_a_hidden = Device(pk=6, name="target-a-hidden-cable", site=a, **common)
    # slc-a's primary IP record sits where the user can't view it.
    slc_a.primary_ip_id = 50
    slc_a.primary_ip = ns(address=ns(ip="198.51.100.50"))
    IPAddress(pk=50, device=slc_b)
    src = ns(ssh_base_port=3000)
    for slc in (slc_a, slc_b):
        DeviceLink(
            pk=slc.pk,
            device_id=slc.pk,
            device=slc,
            status="active",
            percepxion_device_id=f"px-{slc.pk}",
            mgmt_ip=f"192.0.2.{slc.pk}",
            source=src,
        )
    for pk, target, slc, port in (
        (10, t_a, slc_a, 14),
        (11, t_b, slc_b, 2),
        (12, t_a_cross, slc_b, 3),
        (13, t_a_hidden, slc_a, 5),
    ):
        csp = ConsoleServerPort(pk=pk, name=f"Port-{port:02d}", device=slc, device_id=slc.pk)
        PortFacts(pk=pk, port_id=pk, port=csp, state="Managed")
        ConsolePort(
            pk=pk + 100,
            name="Console",
            device=target,
            device_id=target.pk,
            cable_id=pk,
            link_peers=[csp],
        )
        # The cable record for target-a-hidden-cable is outside the user's view.
        Cable(pk=pk, device=slc_b if target is t_a_hidden else target)
    return User("site-a")


def test_netbox_user_limited_to_one_site(netbox):
    from netbox_lantronix.coverage.core import Scope, coverage_report
    from netbox_lantronix.coverage.orm import netbox_targets

    rep = coverage_report("netbox", netbox_targets(netbox), Scope(exclude_roles=[]))
    by = {d["device"]: d for d in rep["devices"]}
    assert "target-b" not in by and "slc-b" not in by
    assert by["target-a"]["covered"] and by["target-a"]["ssh_port"] == 3014
    hidden = by["target-a-on-slc-b"]
    assert hidden["reasons"] == ["peer_not_visible"]
    assert hidden["slc"] is None and hidden["slc_host"] is None and hidden["port_state"] is None


def test_netbox_unrestricted_sees_everything(netbox):
    from netbox_lantronix.coverage.orm import netbox_targets

    names = {t.name for t in netbox_targets(None)}
    assert {"target-b", "slc-b", "target-a-on-slc-b"} <= names


def test_netbox_hidden_cable_reveals_no_slc(netbox):
    from netbox_lantronix.coverage.core import Scope, coverage_report
    from netbox_lantronix.coverage.orm import netbox_targets

    rep = coverage_report("netbox", netbox_targets(netbox), Scope(exclude_roles=[]))
    hidden = {d["device"]: d for d in rep["devices"]}["target-a-hidden-cable"]
    assert hidden["reasons"] == ["peer_not_visible"]
    assert hidden["slc"] is None and hidden["slc_port"] is None


def test_netbox_slc_primary_ip_needs_view_permission(netbox):
    from netbox_lantronix.coverage.core import Scope, coverage_report
    from netbox_lantronix.coverage.orm import netbox_targets

    rep = coverage_report("netbox", netbox_targets(netbox), Scope(exclude_roles=[]))
    covered = {d["device"]: d for d in rep["devices"]}["target-a"]
    assert covered["slc_host"] == "192.0.2.1"
    assert "198.51.100.50" not in str(rep)

    full = coverage_report("netbox", netbox_targets(None), Scope(exclude_roles=[]))
    assert {d["device"]: d for d in full["devices"]}["target-a"]["slc_host"] == "198.51.100.50"
