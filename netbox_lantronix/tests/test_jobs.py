"""SyncJob under the NetBox test runner."""

import uuid
from unittest import mock

from core.choices import JobStatusChoices
from core.exceptions import JobFailed
from core.models import Job, ObjectType
from dcim.models import Device
from django.test import TestCase

from ..jobs import SyncJob, apply_schedule_for, schedule, suspend, sync_is_active
from ..models import ModelRule, PercepxionSource
from ..percepxion.client import AuthError, PercepxionUnavailable
from .netbox_fixtures import make_world, px_device, px_port

SECRET = "sup3r-secret-pw"


class FakeClient:
    devices = [px_device()]
    ports = [px_port(1, name="SLC-B")]
    fail_login = False

    def __init__(self, url, username, password, **kw):
        self.password = password

    def login(self):
        if self.fail_login:
            raise AuthError("POST /v2/user/login returned 401")

    def iter_devices(self):
        return iter(self.devices)

    def iter_ports(self):
        return iter(self.ports)

    def latest_sysconfig_jobs(self):
        return {}

    def cli_output(self, *a):
        return None

    port_state_error = None

    def port_states(self, device_id, org_id=None):
        if self.port_state_error:
            raise self.port_state_error
        return {}


def make_job(source, status=JobStatusChoices.STATUS_RUNNING, interval=None):
    return Job.objects.create(
        object_type=ObjectType.objects.get_for_model(source),
        object_id=source.pk,
        name=SyncJob.name,
        job_id=uuid.uuid4(),
        status=status,
        interval=interval,
    )


ENDPOINTS = {
    "percepxion-sandbox": {
        "url": "https://px.example.com/api",
        "username": "svc",
        "password": SECRET,
    }
}


@mock.patch("netbox_lantronix.jobs.PercepxionClient", FakeClient)
@mock.patch(
    "netbox_lantronix.models.get_plugin_config",
    lambda p, k: ENDPOINTS if k == "endpoints" else None,
)
class SyncJobTest(TestCase):
    def setUp(self):
        self.w = make_world()
        self.source = self.w["source"]
        ModelRule.objects.create(
            source=self.source,
            model="SLC9032",
            rj45_ports=32,
            usb_ports=0,
            device_type=self.w["dtype"],
        )
        FakeClient.fail_login = False

    def test_run_creates_and_records_counts(self):
        job = make_job(self.source)
        SyncJob(job).run()
        self.assertTrue(Device.objects.filter(name="SLC-A").exists())
        self.assertEqual(job.data["devices"]["created"], 1)

    def test_port_state_failure_keeps_the_run_going(self):
        with mock.patch.object(FakeClient, "port_state_error", PercepxionUnavailable("HTTP 500")):
            SyncJob(make_job(self.source)).run()
        self.assertTrue(Device.objects.filter(name="SLC-A").exists())

    def test_rejected_login_fails_backs_off_and_hides_password(self):
        FakeClient.fail_login = True
        job = make_job(self.source)
        with self.assertRaises(JobFailed) as exc:
            SyncJob(job).run()
        self.source.refresh_from_db()
        self.assertIsNotNone(self.source.backoff_until)
        self.assertNotIn(SECRET, str(exc.exception))
        self.assertNotIn(SECRET, job.error or "")
        self.assertNotIn(SECRET, str(job.log_entries))
        Job.objects.filter(pk=job.pk).update(status=JobStatusChoices.STATUS_FAILED)
        # Backoff blocks the next run without contacting Percepxion.
        FakeClient.fail_login = False
        with self.assertRaises(JobFailed):
            SyncJob(make_job(self.source, JobStatusChoices.STATUS_COMPLETED)).run()
        self.assertFalse(Device.objects.exists())
        # Sync now with the same rejected credentials stays blocked.
        with self.assertRaises(JobFailed):
            SyncJob(make_job(self.source, JobStatusChoices.STATUS_COMPLETED)).run(manual=True)
        self.assertFalse(Device.objects.exists())
        # Sync now after the credentials are fixed tries at once and clears the backoff.
        fixed = {"percepxion-sandbox": {**ENDPOINTS["percepxion-sandbox"], "password": "new-pw"}}
        with mock.patch(
            "netbox_lantronix.models.get_plugin_config",
            lambda p, k: fixed if k == "endpoints" else None,
        ):
            SyncJob(make_job(self.source, JobStatusChoices.STATUS_COMPLETED)).run(manual=True)
        self.source.refresh_from_db()
        self.assertTrue(Device.objects.exists())
        self.assertIsNone(self.source.backoff_until)

    def test_refuses_while_another_run_is_running(self):
        make_job(self.source)
        with self.assertRaises(JobFailed):
            SyncJob(make_job(self.source)).run()

    def test_disabled_source_fails(self):
        PercepxionSource.objects.filter(pk=self.source.pk).update(enabled=False)
        self.source.refresh_from_db()
        with self.assertRaises(JobFailed):
            SyncJob(make_job(self.source)).run()

    def test_schedule_and_suspend(self):
        PercepxionSource.objects.filter(pk=self.source.pk).update(sync_interval=15)
        self.source.refresh_from_db()
        with mock.patch.object(SyncJob, "enqueue") as enqueue:
            schedule(self.source)
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.kwargs["interval"], 15)
        scheduled = make_job(self.source, JobStatusChoices.STATUS_SCHEDULED)
        Job.objects.filter(pk=scheduled.pk).update(interval=15)
        suspend(self.source)
        self.assertFalse(SyncJob.get_jobs(self.source).filter(interval__isnull=False).exists())

    def test_apply_schedule_for_requires_sync_action(self):
        from users.models import User

        PercepxionSource.objects.filter(pk=self.source.pk).update(sync_interval=15)
        self.source.refresh_from_db()
        plain = User.objects.create(username="plain")
        with mock.patch("netbox_lantronix.jobs.schedule") as sched:
            self.assertFalse(apply_schedule_for(self.source, plain))
            sched.assert_not_called()
            self.assertTrue(apply_schedule_for(self.source, self.w["user"]))
            sched.assert_called_once()

    def test_sync_is_active(self):
        self.assertFalse(sync_is_active(self.source))
        make_job(self.source, JobStatusChoices.STATUS_PENDING)
        self.assertTrue(sync_is_active(self.source))

    def periodic(self):
        return SyncJob.get_jobs(self.source).filter(interval__isnull=False)

    def test_schedule_keeps_sync_now_and_never_doubles_the_chain(self):
        PercepxionSource.objects.filter(pk=self.source.pk).update(sync_interval=15)
        self.source.refresh_from_db()
        sync_now = make_job(self.source, JobStatusChoices.STATUS_PENDING)
        make_job(self.source, JobStatusChoices.STATUS_SCHEDULED, interval=15)
        make_job(self.source, JobStatusChoices.STATUS_SCHEDULED, interval=15)
        make_job(self.source, JobStatusChoices.STATUS_SCHEDULED, interval=60)
        schedule(self.source)
        schedule(self.source)
        self.assertTrue(Job.objects.filter(pk=sync_now.pk).exists())
        self.assertEqual(list(self.periodic().values_list("interval", flat=True)), [15])

    def test_schedule_while_periodic_run_in_progress_adds_nothing(self):
        PercepxionSource.objects.filter(pk=self.source.pk).update(sync_interval=15)
        self.source.refresh_from_db()
        running = make_job(self.source, JobStatusChoices.STATUS_RUNNING, interval=15)
        with mock.patch.object(SyncJob, "enqueue") as enqueue:
            schedule(self.source)
            suspend(self.source)
        enqueue.assert_not_called()
        self.assertTrue(Job.objects.filter(pk=running.pk).exists())

    def test_stale_or_duplicate_chain_stops_without_syncing(self):
        PercepxionSource.objects.filter(pk=self.source.pk).update(sync_interval=15)
        self.source.refresh_from_db()
        for interval, duplicate in ((60, False), (15, True)):
            other = duplicate and make_job(self.source, JobStatusChoices.STATUS_SCHEDULED, 15)
            job = make_job(self.source, interval=interval)
            with mock.patch("netbox_lantronix.jobs.PercepxionClient") as client:
                SyncJob(job).run()
            client.assert_not_called()
            self.assertIsNone(job.interval)
            Job.objects.filter(pk=job.pk).update(status=JobStatusChoices.STATUS_COMPLETED)
            if other:
                other.delete()
        job = make_job(self.source, interval=15)  # the one live chain keeps going
        SyncJob(job).run()
        self.assertEqual(job.interval, 15)
        self.assertTrue(Device.objects.filter(name="SLC-A").exists())
