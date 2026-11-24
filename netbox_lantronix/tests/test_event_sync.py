"""Focused sync, sync requests and the change watch, under the NetBox test runner."""

import uuid
from datetime import timedelta
from unittest import mock

from core.choices import JobStatusChoices
from core.models import Job, ObjectType
from dcim.models import ConsoleServerPort, Device
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from ..jobs import (
    REQUEUE_DELAY,
    SyncJob,
    WatchJob,
    apply_schedule_for,
    request_sync,
    schedule_watch,
    suspend,
    sync_requests,
    waiting_watch,
)
from ..models import ModelRule, SyncFinding
from ..percepxion.client import PercepxionUnavailable
from ..percepxion.telemetry import PxPortState
from .netbox_fixtures import make_world, px_device, px_port
from .test_jobs import ENDPOINTS, FakeClient, make_job

A, B = "dev-0001", "dev-0002"


def state(number, attached=True, hostname="sw1", state="Managed"):
    return PxPortState(number, state, attached, make="Cisco", hostname=hostname, serial="S1")


class TwoUnitClient(FakeClient):
    devices = [px_device(A, "SLC-A", "000000000001"), px_device(B, "SLC-B", "000000000002")]
    ports = [px_port(1, name="a1", pxid=A), px_port(1, name="b1", pxid=B)]
    states = {A: {1: state(1)}, B: {1: state(1)}}
    logins = 0

    def login(self):
        TwoUnitClient.logins += 1
        super().login()

    def port_states(self, device_id, org_id=None):
        FakeClient.port_state_calls.append(device_id)
        if self.port_state_error:
            raise self.port_state_error
        return dict(TwoUnitClient.states.get(device_id, {}))


def fake_percepxion(test):
    """Patch the client and endpoints for the whole test, setUp included (a class decorator
    covers only the test methods)."""
    for patcher in (
        mock.patch("netbox_lantronix.jobs.PercepxionClient", TwoUnitClient),
        mock.patch(
            "netbox_lantronix.models.get_plugin_config",
            lambda p, k: ENDPOINTS if k == "endpoints" else None,
        ),
    ):
        patcher.start()
        test.addCleanup(patcher.stop)


class FocusedSyncTest(TestCase):
    def setUp(self):
        fake_percepxion(self)
        cache.clear()
        self.w = make_world()
        self.source = self.w["source"]
        ModelRule.objects.create(
            source=self.source,
            model="SLC9032",
            rj45_ports=32,
            usb_ports=0,
            device_type=self.w["dtype"],
        )
        TwoUnitClient.ports = [px_port(1, name="a1", pxid=A), px_port(1, name="b1", pxid=B)]
        TwoUnitClient.states = {A: {1: state(1)}, B: {1: state(1)}}
        TwoUnitClient.port_state_error = None
        TwoUnitClient.logins = 0
        # One full run links both units.
        job = make_job(self.source)
        SyncJob(job).run()
        Job.objects.filter(pk=job.pk).update(status=JobStatusChoices.STATUS_COMPLETED)
        FakeClient.port_state_calls = []
        FakeClient.port_ssh_calls = []

    def focused(self, *ids):
        sync_requests(self.source).add(ids)
        job = make_job(self.source)
        SyncJob(job).run(focus=True)
        return job

    def label(self, device, port="Port-01"):
        return ConsoleServerPort.objects.get(device__name=device, name=port).label

    def test_reads_port_state_and_ssh_only_for_requested_units(self):
        job = self.focused(B)
        self.assertEqual(FakeClient.port_state_calls, [B])
        self.assertEqual([d for d, _ in FakeClient.port_ssh_calls], [B])
        self.assertEqual(job.data["focus"], 1)

    def test_only_requested_units_ports_change(self):
        TwoUnitClient.ports = [px_port(1, name="a1-new", pxid=A), px_port(1, name="b1-new", pxid=B)]
        self.focused(B)
        self.assertEqual(self.label("SLC-B"), "b1-new")
        self.assertEqual(self.label("SLC-A"), "a1")

    def test_focused_runs_record_no_findings(self):
        bad = px_device("dev-9", "bad name!", "9")
        with mock.patch.object(TwoUnitClient, "devices", [*TwoUnitClient.devices, bad]):
            job = self.focused(A)
        self.assertFalse(SyncFinding.objects.filter(job=job).exists())
        self.assertEqual(job.data["findings"].get("rejected_name"), 1)

    def test_nothing_requested_means_no_percepxion_call(self):
        logins = TwoUnitClient.logins
        SyncJob(make_job(self.source)).run(focus=True)
        self.assertEqual(TwoUnitClient.logins, logins)

    def test_running_sync_puts_the_request_back_for_later(self):
        make_job(self.source)  # running
        with mock.patch("netbox_lantronix.jobs.SyncJob.enqueue") as enqueue:
            sync_requests(self.source).add([B])
            SyncJob(make_job(self.source, status=JobStatusChoices.STATUS_PENDING)).run(focus=True)
        self.assertEqual(FakeClient.port_state_calls, [])
        enqueue.assert_called_once()
        kwargs = enqueue.call_args.kwargs
        self.assertTrue(kwargs["focus"])
        self.assertGreater(
            kwargs["schedule_at"], timezone.now() + timedelta(seconds=REQUEUE_DELAY - 5)
        )
        self.assertEqual(sync_requests(self.source).take(), {B})

    def test_requests_join_one_waiting_job(self):
        with mock.patch("netbox_lantronix.jobs.SyncJob.enqueue") as enqueue:
            self.assertTrue(request_sync(self.source, [A]))
            self.assertFalse(request_sync(self.source, [B, A]))
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.kwargs["user"], self.w["user"])
        self.assertEqual(sync_requests(self.source).take(), {A, B})

    def test_failed_enqueue_doesnt_block_the_next_request(self):
        with mock.patch("netbox_lantronix.jobs.SyncJob.enqueue", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                request_sync(self.source, [A])
        with mock.patch("netbox_lantronix.jobs.SyncJob.enqueue") as enqueue:
            self.assertTrue(request_sync(self.source, [B]))
        enqueue.assert_called_once()
        self.assertEqual(sync_requests(self.source).take(), {A, B})

    def test_full_sync_is_unchanged(self):
        job = make_job(self.source)
        SyncJob(job).run()
        self.assertEqual(sorted(FakeClient.port_state_calls), [A, B])
        self.assertNotIn("focus", job.data)


class WatchTest(TestCase):
    def setUp(self):
        fake_percepxion(self)
        cache.clear()
        self.w = make_world()
        self.source = self.w["source"]
        ModelRule.objects.create(
            source=self.source,
            model="SLC9032",
            rj45_ports=32,
            usb_ports=0,
            device_type=self.w["dtype"],
        )
        TwoUnitClient.ports = [px_port(1, name="a1", pxid=A), px_port(1, name="b1", pxid=B)]
        TwoUnitClient.states = {A: {1: state(1)}, B: {1: state(1)}}
        TwoUnitClient.port_state_error = None
        job = make_job(self.source)
        SyncJob(job).run()
        Job.objects.filter(pk=job.pk).update(status=JobStatusChoices.STATUS_COMPLETED)

    def watch_job(self, interval=None):
        return Job.objects.create(
            object_type=ObjectType.objects.get_for_model(self.source),
            object_id=self.source.pk,
            name=WatchJob.name,
            job_id=uuid.uuid4(),
            status=JobStatusChoices.STATUS_RUNNING,
            interval=interval,
        )

    def watch(self):
        job = self.watch_job()
        with mock.patch("netbox_lantronix.jobs.request_sync") as req:
            WatchJob(job).run()
        return job, req

    def test_first_check_is_a_baseline(self):
        job, req = self.watch()
        req.assert_not_called()
        self.assertEqual(job.data, {"watched": 2, "changed": 0, "unread": 0})

    def test_unplugged_port_requests_a_focused_sync_of_that_unit(self):
        self.watch()
        TwoUnitClient.states = {A: {1: state(1)}, B: {1: state(1, attached=False, hostname="")}}
        job, req = self.watch()
        req.assert_called_once()
        self.assertEqual(set(req.call_args.args[1]), {B})
        self.assertEqual(job.data["changed"], 1)

    def test_unreadable_unit_is_no_change(self):
        self.watch()
        TwoUnitClient.port_state_error = PercepxionUnavailable("timeout")
        job, req = self.watch()
        req.assert_not_called()
        self.assertEqual(job.data["unread"], 2)

    def test_only_linked_units_are_read(self):
        Device.objects.get(name="SLC-B").delete()
        FakeClient.port_state_calls = []
        self.watch()
        self.assertEqual(FakeClient.port_state_calls, [A])

    def test_watch_interval_limits(self):
        self.source.watch_interval = 61
        with self.assertRaises(ValidationError):
            self.source.full_clean()
        self.source.watch_interval = 1
        self.source.full_clean()

    def test_schedule_and_suspend_the_watch(self):
        self.source.watch_interval = 0
        self.assertIsNone(schedule_watch(self.source))
        self.source.watch_interval = 2
        with mock.patch("netbox_lantronix.jobs.WatchJob.enqueue") as enqueue:
            schedule_watch(self.source)
        enqueue.assert_called_once_with(instance=self.source, interval=2, user=None)
        WatchJob.enqueue(instance=self.source, interval=2)
        self.assertEqual(waiting_watch(self.source).count(), 1)
        schedule_watch(self.source)  # idempotent
        self.assertEqual(waiting_watch(self.source).count(), 1)
        suspend(self.source)
        self.assertEqual(waiting_watch(self.source).count(), 0)

    def test_apply_schedule_for_schedules_the_watch_too(self):
        self.source.watch_interval = 3
        with (
            mock.patch("netbox_lantronix.jobs.schedule") as sched,
            mock.patch("netbox_lantronix.jobs.schedule_watch") as watch,
        ):
            self.assertTrue(apply_schedule_for(self.source, self.w["user"]))
        sched.assert_called_once()
        watch.assert_called_once()

    def test_changed_interval_stops_the_old_chain(self):
        job = self.watch_job(interval=5)
        FakeClient.port_state_calls = []
        WatchJob(job).run()
        self.assertIsNone(job.interval)
        self.assertEqual(FakeClient.port_state_calls, [])
