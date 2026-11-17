"""Console cable management against a real NetBox database."""

from dcim.models import (
    Cable,
    ConsolePort,
    ConsoleServerPort,
    Device,
    DeviceRole,
    DeviceType,
    Manufacturer,
)
from django.test import TestCase
from extras.models import JournalEntry

from ..sync.apply import CABLE_TAG, apply_plan, build_context, build_snapshot
from ..sync.planner import plan
from .netbox_fixtures import TYPE_SLUG, make_world, px_device, px_port

MANAGED = {
    "make": "Cisco",
    "model": "WS-C3560CG-8TC-S",
    "serial": "FOC1234X0AB",
    "os": "IOS",
    "os_version": "15.2(2)E10",
    "state": "Managed",
    "attached": "yes",
}


def gone(state="Disconnected"):
    return {"state": state, "attached": "no"}


class CableTest(TestCase):
    def setUp(self):
        self.w = make_world()
        self.source = self.w["source"]
        self.run_sync([px_port(1), px_port(2), px_port(3)])  # creates SLC-A and its ports
        self.slc = Device.objects.get(name="SLC-A")
        cisco_mfr = Manufacturer.objects.create(name="Cisco", slug="cisco")
        other = DeviceType.objects.create(manufacturer=cisco_mfr, model="Generic", slug="generic")
        role = DeviceRole.objects.create(name="Switch", slug="switch")
        self.cisco = self._device("edge-switch-01", "FOC1234X0AB", other, role)
        self.emg = self._device("emg-01", "0080A3EMG", other, role)
        self.firewall = self._device("firewall-01", "", other, role)

    def _device(self, name, serial, dtype: DeviceType, role):
        d = Device.objects.create(
            name=name, serial=serial, device_type=dtype, role=role, site=self.w["site"]
        )
        ConsolePort.objects.create(device=d, name="Console")
        return d

    def port(self, n):
        return ConsoleServerPort.objects.get(device=self.slc, name=f"Port-{n:02d}")

    def cable(self, n, device):
        Cable(
            a_terminations=[self.port(n)],
            b_terminations=[device.consoleports.get()],
            status="connected",
        ).save()

    def peer(self, n):
        p = self.port(n)
        if not p.cable_id:
            return None
        (far,) = p.link_peers
        return far.device.name

    def run_sync(self, ports):
        devices = [px_device()]
        ctx = build_context(self.source)
        ctx = type(ctx)(**{**ctx.__dict__, "rules": {("SLC9032", 32, 0): TYPE_SLUG}})
        nb_devices, nb_ports = build_snapshot(self.source, devices)
        p = plan(ctx, devices, ports, nb_devices, nb_ports)
        if self.source.manage_cables:
            p.observed = [x for x in ports if "attached" in x.facts]  # what the job does
        return p, apply_plan(p, self.source, self.w["user"])

    def recabled(self):
        """Cisco moved from Port-01 to Port-03; the EMG on Port-02 isn't identified yet."""
        return [
            px_port(1, **gone()),
            px_port(2, **gone("Unidentified")),
            px_port(3, hostname="edge-switch-01", **MANAGED),
        ]

    def test_recabling_moves_the_cisco_and_keeps_the_unidentified_emg(self):
        self.cable(1, self.cisco)
        self.cable(2, self.emg)
        self.cable(3, self.firewall)
        self.source.manage_cables = True
        self.source.save()
        p, counts = self.run_sync(self.recabled())
        self.assertEqual(counts["cables"]["created"], 1)
        self.assertEqual(counts["cables"]["deleted"], 2)  # Port-01 moved, Port-03 replaced
        self.assertEqual(
            [self.peer(1), self.peer(2), self.peer(3)], [None, "emg-01", "edge-switch-01"]
        )
        new = self.port(3).cable
        self.assertEqual(list(new.tags.values_list("slug", flat=True)), [CABLE_TAG])
        notes = JournalEntry.objects.filter(assigned_object_id=self.cisco.pk)
        self.assertTrue(any("created" in j.comments for j in notes))
        self.assertTrue(
            JournalEntry.objects.filter(
                assigned_object_id=self.firewall.pk, comments__contains="removed"
            ).exists()
        )

    def test_second_run_changes_nothing(self):
        self.source.manage_cables = True
        self.source.save()
        self.run_sync(self.recabled())
        _, counts = self.run_sync(self.recabled())
        self.assertEqual((counts["cables"]["created"], counts["cables"]["deleted"]), (0, 0))

    def test_off_by_default_writes_no_cables(self):
        self.cable(1, self.cisco)
        _, counts = self.run_sync(self.recabled())
        self.assertNotIn("cables", counts)
        self.assertEqual(self.peer(1), "edge-switch-01")
        self.assertIsNone(self.peer(3))

    def test_ports_without_telemetry_state_are_left_alone(self):
        self.cable(1, self.cisco)
        self.source.manage_cables = True
        self.source.save()
        _, counts = self.run_sync([px_port(1, state="Disconnected"), px_port(2), px_port(3)])
        self.assertEqual(self.peer(1), "edge-switch-01")
        self.assertEqual(counts["cables"]["deleted"], 0)

    def test_unknown_attached_device_is_a_finding(self):
        self.source.manage_cables = True
        self.source.save()
        ports = [px_port(3, hostname="nobody", **{**MANAGED, "serial": "NOPE"})]
        p, counts = self.run_sync(ports)
        self.assertEqual(counts["cables"]["created"], 0)
        self.assertIn("attached_unmatched", [f.kind for f in p.findings])

    def test_same_serial_in_another_site_is_out_of_scope(self):
        self.cisco.site = self.w["other_site"]
        self.cisco.save()
        self.source.manage_cables = True
        self.source.save()
        p, counts = self.run_sync(self.recabled())
        self.assertEqual(counts["cables"]["created"], 0)
        self.assertIsNone(self.peer(3))
        self.assertIn("attached_unmatched", [f.kind for f in p.findings])
