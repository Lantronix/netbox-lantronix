"""Per-source background sync."""

from __future__ import annotations

import hashlib
import hmac
from datetime import timedelta

from core.choices import JobStatusChoices
from core.exceptions import JobFailed
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from netbox.jobs import JobRunner
from netbox.plugins import get_plugin_config

from .percepxion.client import AuthError, PartialData, PercepxionClient, PercepxionUnavailable
from .percepxion.resolver import parse_sysconfig, sku_from_sysconfig
from .percepxion.telemetry import merge_state

BACKOFF_MIN = 30
ACTIVE_STATES = (
    *JobStatusChoices.ENQUEUED_STATE_CHOICES,
    JobStatusChoices.STATUS_RUNNING,
)


WAITING_STATES = (JobStatusChoices.STATUS_PENDING, JobStatusChoices.STATUS_SCHEDULED)


def waiting_periodic(source, exclude_pk=None):
    """Periodic runs that haven't started. A running job is never deleted: NetBox would still
    reschedule it when it finishes."""
    jobs = SyncJob.get_jobs(source).filter(status__in=WAITING_STATES, interval__isnull=False)
    return jobs.exclude(pk=exclude_pk) if exclude_pk else jobs


def running_jobs(source, exclude_pk=None):
    jobs = SyncJob.get_jobs(source).filter(status=JobStatusChoices.STATUS_RUNNING)
    return jobs.exclude(pk=exclude_pk) if exclude_pk else jobs


def sync_is_active(source) -> bool:
    """True if a run for this source is pending to start now or running (not a future schedule)."""
    now = timezone.now()
    return (
        SyncJob.get_jobs(source)
        .filter(status__in=ACTIVE_STATES)
        .exclude(scheduled__gt=now + timedelta(seconds=30))
        .exists()
    )


class SyncJob(JobRunner):
    class Meta:
        name = "Percepxion sync"

    def _with_port_states(self, client, source, px_devices, px_ports):
        """Attached-device state from the telemetry view, one call per device.

        A device whose view fails keeps its port-search data and the run goes on; the device
        is named in the job log.
        """
        wanted = {p.device_id for p in px_ports}
        states = {}
        for dev in px_devices:
            if dev.device_id not in wanted:
                continue
            try:
                states[dev.device_id] = client.port_states(dev.device_id, source.organization_id)
            except PercepxionUnavailable as exc:
                self.logger.warning(f"Port state unavailable for {dev.name}: {exc}")
        return [merge_state(p, states.get(p.device_id, {}).get(p.number)) for p in px_ports]

    def run(self, *args, manual=False, **kwargs):
        if self.job.interval and self._schedule_superseded():
            # Clearing the interval stops NetBox from scheduling the next run of this chain.
            self.job.interval = None
            self.logger.info("Schedule changed or duplicated; this periodic run stops its chain")
            return
        try:
            self._sync(manual)
        except JobFailed as exc:
            # NetBox doesn't store a JobFailed message; log it so the status page can show why.
            self.logger.error(str(exc))
            self.job.error = str(exc)[:500]
            raise

    def _schedule_superseded(self) -> bool:
        source = self.job.object
        return (
            source is None
            or not source.enabled
            or self.job.interval != source.sync_interval
            or waiting_periodic(source, exclude_pk=self.job.pk).exists()
        )

    def _sync(self, manual=False):
        from .sync.apply import (
            SyncPermissionDenied,
            apply_plan,
            build_context,
            build_snapshot,
            record_findings,
        )
        from .sync.planner import plan as make_plan

        source = self.job.object
        if source is None or not source.enabled:
            raise JobFailed("Source is disabled or deleted")
        cfg = source.endpoint_config
        if not cfg:
            raise JobFailed(f"Endpoint '{source.endpoint}' is not in plugin configuration")
        # During a login backoff only "Sync now" with changed credentials may try again, so
        # repeated clicks can't hammer the Percepxion login with the rejected password.
        if source.backoff_until and source.backoff_until > timezone.now():
            if not (manual and cache.get(_backoff_key(source)) != _credential_tag(cfg)):
                raise JobFailed(
                    f"Login backoff until {source.backoff_until:%Y-%m-%d %H:%M} UTC; "
                    "fix the endpoint credentials and restart NetBox, then Sync now"
                )
        if running_jobs(source, exclude_pk=self.job.pk).exists():
            raise JobFailed("Another sync for this source is running")

        user = _sync_user()

        client = PercepxionClient(
            cfg["url"],
            cfg["username"],
            cfg["password"],
            verify=cfg.get("verify", True),
            ca_bundle=cfg.get("ca_bundle"),
            proxy=cfg.get("proxy"),
        )
        try:
            client.login()
            px_devices = list(client.iter_devices())
            px_ports = list(client.iter_ports())
            px_ports = self._with_port_states(client, source, px_devices, px_ports)
            sysconfig_jobs = None

            def sysconfig(pxid):
                nonlocal sysconfig_jobs
                if sysconfig_jobs is None:
                    sysconfig_jobs = client.latest_sysconfig_jobs()
                job = sysconfig_jobs.get(pxid)
                if not job:
                    return None
                return sku_from_sysconfig(parse_sysconfig(client.cli_output(job[0], pxid)))

            nb_devices, nb_ports = build_snapshot(source, px_devices)
            plan = make_plan(
                build_context(source), px_devices, px_ports, nb_devices, nb_ports, sysconfig
            )
        except AuthError:
            minutes = max(BACKOFF_MIN, source.sync_interval)
            source.backoff_until = timezone.now() + timedelta(minutes=minutes)
            cache.set(_backoff_key(source), _credential_tag(cfg), timeout=minutes * 60)
            type(source).objects.filter(pk=source.pk).update(backoff_until=source.backoff_until)
            raise JobFailed("Percepxion rejected the login for this endpoint") from None
        except (PercepxionUnavailable, PartialData) as exc:
            raise JobFailed(f"Percepxion read failed: {exc}") from None

        try:
            counts = apply_plan(plan, source, user)
        except SyncPermissionDenied as exc:
            raise JobFailed(f"Sync user lacks NetBox permission: {exc}") from None
        counts["findings"] = record_findings(plan, source, self.job)
        self.job.data = counts
        if source.backoff_until:
            type(source).objects.filter(pk=source.pk).update(backoff_until=None)
        dev = counts["devices"]
        self.logger.info(
            f"Devices created {dev['created']}, updated {dev['updated']}, "
            f"unchanged {dev['unchanged']}; ports updated {counts['ports']['updated']}"
        )


def _backoff_key(source):
    return f"netbox_lantronix:backoff:{source.pk}"


def _credential_tag(cfg):
    """Keyed hash of the endpoint credentials; tells whether they changed, reveals nothing."""
    material = "\x00".join(str(cfg.get(k, "")) for k in ("url", "username", "password"))
    return hmac.new(settings.SECRET_KEY.encode(), material.encode(), hashlib.sha256).hexdigest()


def _sync_user():
    name = get_plugin_config("netbox_lantronix", "sync_user")
    user = get_user_model().objects.filter(username=name).first() if name else None
    if user is None:
        raise JobFailed("Configured sync_user does not exist in NetBox")
    return user


def suspend(source):
    """Remove pending scheduled runs; used when a user without the sync action saves a source."""
    return waiting_periodic(source).delete()


def apply_schedule_for(source, user):
    """Schedule if ``user`` may sync this source, else suspend. Returns True if scheduled."""
    if user.has_perm("netbox_lantronix.sync_percepxionsource", source):
        schedule(source, user)
        return True
    suspend(source)
    return False


def schedule(source, user=None):
    """Create, update or remove the periodic job for a source.

    Called from views and the API only after checking the user holds the sync action.
    """
    waiting = waiting_periodic(source)
    if not source.enabled or not source.sync_interval:
        waiting.delete()
        return None
    # NetBox's enqueue_once looks only at the first queued job, which can be a "Sync now" run
    # or a run in progress, so it can delete the wrong job or start a second chain.
    keep = waiting.filter(interval=source.sync_interval).order_by("scheduled").first()
    waiting.exclude(pk=keep.pk if keep else None).delete()
    if keep:
        return keep
    running = running_jobs(source).filter(interval=source.sync_interval).first()
    if running:
        return running  # NetBox schedules its next run when it finishes
    return SyncJob.enqueue(instance=source, interval=source.sync_interval, user=user)
