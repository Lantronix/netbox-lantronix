"""apply_plan against a real NetBox database."""

from unittest import mock

from core.models import ObjectChange
from dcim.models import ConsoleServerPort, Device, Site
from django.test import TestCase

from ..models import DeviceLink, PortFacts, SyncFinding
from ..sync.apply import (
    SyncPermissionDenied,
    apply_plan,
    build_context,
    build_snapshot,
    record_findings,
)
from ..sync.planner import DeviceCreate, Plan, plan
from .netbox_fixtures import TYPE_SLUG, grant, make_world, px_device, px_port

PORTS = [
    px_port(1, name="SLC-B"),
    px_port(
        2,
        hostname="Switch",
        make="cisco",
        model="WS-C3560CG-8TC-S",
        os="ios",
        os_version="15.2(2)E10",
        serial="",
    ),
    px_port(3),
]


class ApplyTest(TestCase):
    def setUp(self):
        self.w = make_world()
        self.source = self.w["source"]

    def run_sync(self, devices=None, ports=PORTS, user=None):
        devices = devices or [px_device()]
        ctx = build_context(self.source)
        ctx = type(ctx)(**{**ctx.__dict__, "rules": {("SLC9032", 32, 0): TYPE_SLUG}})
        nb_devices, nb_ports = build_snapshot(self.source, devices)
        p = plan(ctx, devices, ports, nb_devices, nb_ports)
        return p, apply_plan(p, self.source, user or self.w["user"])

    def test_case_variant_name_goes_to_approval_instead_of_failing_the_run(self):
        Device.objects.create(
            name="slc-a", device_type=self.w["dtype"], role=self.w["role"], site=self.w["site"]
        )
        p, counts = self.run_sync()
        self.assertEqual((counts["devices"]["created"], counts["devices"]["pending"]), (0, 1))
        self.assertEqual([f.kind for f in p.findings], ["adoption_pending"])
        self.assertEqual(Device.objects.count(), 1)

    def test_device_netbox_rejects_becomes_a_finding_and_the_rest_sync(self):
        from django.core.exceptions import ValidationError

        real_clean = Device.full_clean

        def clean(device, *args, **kwargs):
            if device.name == "SLC-BAD":
                raise ValidationError("Site policy forbids this device")
            return real_clean(device, *args, **kwargs)

        devices = [px_device(), px_device("dev-0002", "SLC-BAD", "000000000002")]
        with mock.patch.object(Device, "full_clean", clean):
            p, counts = self.run_sync(devices=devices, ports=PORTS + [px_port(1, pxid="dev-0002")])
        self.assertEqual(counts["devices"]["created"], 1)
        self.assertTrue(Device.objects.filter(name="SLC-A").exists())
        self.assertFalse(Device.objects.filter(name="SLC-BAD").exists())
        self.assertFalse(DeviceLink.objects.filter(percepxion_device_id="dev-0002").exists())
        (finding,) = p.findings
        self.assertEqual((finding.kind, finding.device_name), ("conflict", "SLC-BAD"))
        self.assertIn("NetBox rejected the device: Site policy forbids", finding.detail)

    def test_create_with_ports_facts_link_and_attribution(self):
        _, counts = self.run_sync()
        device = Device.objects.get(name="SLC-A")
        self.assertEqual(device.device_type.slug, TYPE_SLUG)
        self.assertEqual(device.platform.slug, "lantronix_itmgos")
        self.assertEqual(device.custom_field_data["percepxion_device_id"], "dev-0001")
        link = DeviceLink.objects.get(device=device)
        self.assertEqual((link.status, link.type_source), ("active", "rule"))
        port1 = ConsoleServerPort.objects.get(device=device, name="Port-01")
        port2 = ConsoleServerPort.objects.get(device=device, name="Port-02")
        self.assertEqual(port1.label, "SLC-B")
        self.assertEqual(port2.description, "Switch")
        self.assertEqual(PortFacts.objects.get(port=port2).model, "WS-C3560CG-8TC-S")
        self.assertEqual(counts["devices"]["created"], 1)
        users = set(
            ObjectChange.objects.filter(changed_object_id=device.pk).values_list(
                "user_name", flat=True
            )
        )
        self.assertEqual(users, {"percepxion-sync"})

    def test_second_run_writes_no_change_records(self):
        self.run_sync()
        before = ObjectChange.objects.count()
        _, counts = self.run_sync()
        self.assertEqual(ObjectChange.objects.count(), before)
        self.assertEqual(
            counts["devices"], {"created": 0, "updated": 0, "unchanged": 1, "pending": 0}
        )

    def test_failure_mid_apply_rolls_back_everything(self):
        bad = Plan(
            creates=[
                DeviceCreate("dev-0001", "SLC-A", "000000000001", TYPE_SLUG, "rule"),
                DeviceCreate("dev-0002", "SLC-B", "000000000002", "lantronix-missing", "rule"),
            ]
        )
        with self.assertRaises(KeyError):
            apply_plan(bad, self.source, self.w["user"])
        self.assertFalse(Device.objects.exists())
        self.assertFalse(DeviceLink.objects.exists())

    def test_sync_user_without_permission_changes_nothing(self):
        self.w["user"].is_superuser = False
        self.w["user"].save()
        with self.assertRaises(SyncPermissionDenied):
            self.run_sync()
        self.assertFalse(Device.objects.exists())

    def test_sync_user_limited_to_site(self):
        user = self.w["user"]
        user.is_superuser = False
        user.save()
        grant(user, [Device], ["view", "add", "change"], {"site__slug": "lab"})
        grant(user, [ConsoleServerPort], ["view", "change"], {"device__site__slug": "lab"})
        self.run_sync(user=user)
        self.assertTrue(Device.objects.filter(name="SLC-A").exists())
        # A linked device moved to another site is out of scope: the next write fails whole.
        Device.objects.filter(name="SLC-A").update(site=Site.objects.get(slug="other"))
        with self.assertRaises(SyncPermissionDenied):
            self.run_sync(devices=[px_device(name="SLC-RENAMED")], user=user)
        self.assertTrue(Device.objects.filter(name="SLC-A").exists())

    def site_limited_user(self):
        user = self.w["user"]
        user.is_superuser = False
        user.save()
        grant(user, [Device], ["view", "add", "change"], {"site__slug": "lab"})
        grant(user, [ConsoleServerPort], ["view", "change"], {"device__site__slug": "lab"})
        return user

    def test_no_pending_link_on_a_device_the_sync_user_cannot_change(self):
        user = self.site_limited_user()
        elsewhere = Device.objects.create(
            name="OLD-NAME",
            device_type=self.w["dtype"],
            role=self.w["role"],
            site=Site.objects.get(slug="other"),
            serial="000000000001",
        )
        p, counts = self.run_sync(user=user)
        self.assertEqual(counts["devices"]["pending"], 0)
        self.assertFalse(DeviceLink.objects.filter(device=elsewhere).exists())
        details = [f.detail for f in p.findings if f.kind == "conflict"]
        self.assertEqual(
            details, ["A matching NetBox device is outside the sync user's permissions"]
        )

    def test_pending_link_still_made_when_the_sync_user_can_change_the_device(self):
        elsewhere = Device.objects.create(
            name="OLD-NAME",
            device_type=self.w["dtype"],
            role=self.w["role"],
            site=Site.objects.get(slug="other"),
            serial="000000000001",
        )
        _, counts = self.run_sync()
        self.assertEqual(counts["devices"]["pending"], 1)
        self.assertEqual(DeviceLink.objects.get(device=elsewhere).status, "pending")

    def test_no_facts_or_link_details_for_a_device_moved_out_of_scope(self):
        user = self.site_limited_user()
        self.run_sync(user=user)
        device = Device.objects.get(name="SLC-A")
        link = DeviceLink.objects.get(device=device)
        PortFacts.objects.filter(port__device=device).delete()
        DeviceLink.objects.filter(pk=link.pk).update(mgmt_ip="", firmware_version="")
        Device.objects.filter(pk=device.pk).update(site=Site.objects.get(slug="other"))
        _, counts = self.run_sync(user=user)  # nothing on the device or its ports changed
        link.refresh_from_db()
        self.assertEqual((counts["facts"]["updated"], link.mgmt_ip), (0, ""))
        self.assertFalse(PortFacts.objects.filter(port__device=device).exists())

    def test_findings_are_kept_for_the_last_runs_only(self):
        import uuid
        from datetime import timedelta

        from core.models import Job, ObjectType
        from django.utils import timezone

        from ..sync.apply import FINDINGS_KEEP_RUNS
        from ..sync.planner import Finding

        def job():
            return Job.objects.create(
                object_type=ObjectType.objects.get_for_model(self.source),
                object_id=self.source.pk,
                name="Percepxion sync",
                job_id=uuid.uuid4(),
            )

        one = Plan(findings=[Finding("missing", "SLC-X", "dev-0009", "Not returned")])
        jobs = [job() for _ in range(FINDINGS_KEEP_RUNS + 2)]
        for j in jobs:
            record_findings(one, self.source, j)
        kept = set(SyncFinding.objects.filter(source=self.source).values_list("job_id", flat=True))
        self.assertEqual(kept, {j.pk for j in jobs[2:]})
        # Findings whose job row is gone are kept for 30 days, then removed.
        old = SyncFinding.objects.create(source=self.source, job=None, kind="missing")
        recent = SyncFinding.objects.create(source=self.source, job=None, kind="missing")
        SyncFinding.objects.filter(pk=old.pk).update(created=timezone.now() - timedelta(days=31))
        counts = record_findings(one, self.source, jobs[-1])
        self.assertEqual(counts, {"missing": 1})
        self.assertFalse(SyncFinding.objects.filter(pk=old.pk).exists())
        self.assertTrue(SyncFinding.objects.filter(pk=recent.pk).exists())

    def test_never_moves_or_retypes_existing_device(self):
        self.run_sync()
        device = Device.objects.get(name="SLC-A")
        Device.objects.filter(pk=device.pk).update(site=Site.objects.get(slug="other"))
        self.run_sync(devices=[px_device(name="SLC-A2")])
        device.refresh_from_db()
        self.assertEqual(
            (device.name, device.site.slug, device.device_type.slug), ("SLC-A2", "other", TYPE_SLUG)
        )

    def test_findings_recorded_without_job(self):
        p, _ = self.run_sync(devices=[px_device(), px_device("dev-0002", "bad name", "x2")])
        record_findings(p, self.source, None)
        self.assertEqual(
            list(SyncFinding.objects.values_list("kind", flat=True)), ["rejected_name"]
        )

    def test_facts_kept_when_absent_and_unchanged_facts_write_no_change_record(self):
        self.run_sync()
        port2 = ConsoleServerPort.objects.get(device__name="SLC-A", name="Port-02")
        first = PortFacts.objects.get(port=port2)
        before = ObjectChange.objects.count()
        self.run_sync()  # same facts: last_reported moves, no change record
        self.assertEqual(ObjectChange.objects.count(), before)
        no_facts = [px_port(1, name="SLC-B"), px_port(2, hostname="Switch"), px_port(3)]
        self.run_sync(ports=no_facts)  # device stops reporting: facts stay as last reported
        kept = PortFacts.objects.get(port=port2)
        self.assertEqual(
            (kept.model, kept.last_reported >= first.last_reported), ("WS-C3560CG-8TC-S", True)
        )

    def test_unplugged_port_keeps_facts_as_history_and_shows_not_attached(self):
        self.run_sync()
        port2 = ConsoleServerPort.objects.get(device__name="SLC-A", name="Port-02")
        first = PortFacts.objects.get(port=port2)
        unplugged = [PORTS[0], px_port(2, state="Disconnected", attached="no"), px_port(3)]
        self.run_sync(ports=unplugged)
        kept = PortFacts.objects.get(port=port2)
        port2.refresh_from_db()
        self.assertEqual(
            (kept.attached, kept.state, kept.model), (False, "Disconnected", "WS-C3560CG-8TC-S")
        )
        self.assertEqual(kept.last_reported, first.last_reported)
        self.assertEqual(port2.description, "")

    def test_idle_port_with_no_history_gets_no_facts_row(self):
        idle = [PORTS[0], PORTS[1], px_port(3, state="Disconnected", attached="no")]
        self.run_sync(ports=idle)
        port3 = ConsoleServerPort.objects.get(device__name="SLC-A", name="Port-03")
        self.assertFalse(PortFacts.objects.filter(port=port3).exists())

    def test_facts_follow_attached_device_on_unchanged_console_server(self):
        self.run_sync()
        swapped = [
            PORTS[0],
            px_port(
                2,
                hostname="Switch",
                make="cisco",
                model="C9300-24T",
                os="ios-xe",
                os_version="17.9.4",
                serial="",
            ),
            PORTS[2],
        ]
        _, counts = self.run_sync(ports=swapped)
        self.assertEqual(counts["devices"]["unchanged"], 1)
        port2 = ConsoleServerPort.objects.get(device__name="SLC-A", name="Port-02")
        self.assertEqual(PortFacts.objects.get(port=port2).model, "C9300-24T")
