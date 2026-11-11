"""The console coverage gate script, run inside NetBox against real cables and port facts."""

import json
from types import SimpleNamespace

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
from django.utils import timezone
from users.models import User

from ..coverage.console_coverage_gate import ConsoleCoverageGate
from ..models import DeviceLink, LinkStatusChoices, PortFacts
from .netbox_fixtures import make_world


class CoverageGateScriptTests(TestCase):
    def setUp(self):
        w = make_world()
        self.slc = Device.objects.create(
            name="slc-01", device_type=w["dtype"], site=w["site"], role=w["role"]
        )
        DeviceLink.objects.create(
            source=w["source"],
            device=self.slc,
            percepxion_device_id="dev-0001",
            status=LinkStatusChoices.ACTIVE,
            mgmt_ip="192.0.2.10",
        )
        port = ConsoleServerPort.objects.get(device=self.slc, name="Port-02")
        PortFacts.objects.create(port=port, state="Managed", last_reported=timezone.now())

        vendor = Manufacturer.objects.create(name="Vendor", slug="vendor")
        switch_type = DeviceType.objects.create(manufacturer=vendor, model="Switch", slug="switch")
        switch_role = DeviceRole.objects.create(name="Switch", slug="switch")
        self.covered = Device.objects.create(
            name="sw-01", device_type=switch_type, site=w["site"], role=switch_role
        )
        console = ConsolePort.objects.create(device=self.covered, name="Console")
        Cable(a_terminations=[console], b_terminations=[port]).save()
        self.uncabled = Device.objects.create(
            name="sw-02", device_type=switch_type, site=w["site"], role=switch_role
        )
        ConsolePort.objects.create(device=self.uncabled, name="Console")

        self.user = User.objects.create(username="gate", is_superuser=True)

    def run_gate(self, **data):
        script = ConsoleCoverageGate()
        script.request = SimpleNamespace(user=self.user)
        return json.loads(script.run(data, commit=False))

    def test_reports_covered_and_uncovered_devices(self):
        report = self.run_gate(devices="sw-01, sw-02")
        self.assertEqual(report["schema"], "aoob.console-coverage/v1")
        self.assertEqual(report["result"], "fail")
        by = {d["device"]: d for d in report["devices"]}
        self.assertTrue(by["sw-01"]["covered"], by["sw-01"]["reasons"])
        self.assertEqual(by["sw-01"]["slc"], "slc-01")
        self.assertEqual(by["sw-01"]["ssh_port"], 3002)
        self.assertEqual(by["sw-02"]["reasons"], ["no_console_cable"])

    def test_passes_when_every_device_in_scope_is_covered(self):
        report = self.run_gate(devices="sw-01")
        self.assertEqual(report["result"], "pass", report["devices"])
        self.assertEqual(report["devices"][0]["slc_host"], "192.0.2.10")

    def test_console_servers_are_left_out_by_default(self):
        report = self.run_gate()
        self.assertNotIn("slc-01", {d["device"] for d in report["devices"]})

    def test_refuses_to_run_without_a_requesting_user(self):
        from utilities.exceptions import AbortScript

        script = ConsoleCoverageGate()
        with self.assertRaises(AbortScript):
            script.run({}, commit=False)

    def test_role_finds_the_script_by_class_name(self):
        # lantronix.oob's oob_coverage_gate role looks the script up by this name.
        self.assertEqual(ConsoleCoverageGate.__name__, "ConsoleCoverageGate")
