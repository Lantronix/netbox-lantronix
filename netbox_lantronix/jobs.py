"""Per-source background sync."""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections import Counter
from datetime import timedelta

from core.choices import JobStatusChoices
from core.exceptions import JobFailed
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from netbox.jobs import JobRunner
from netbox.plugins import get_plugin_config

from .endpoint_checks import organization_allowed
from .percepxion.client import AuthError, PartialData, PercepxionClient, PercepxionUnavailable
from .percepxion.resolver import parse_sysconfig, sku_from_sysconfig
from .percepxion.sync_requests import SyncRequests
from .percepxion.telemetry import merge_state
from .percepxion.watch import changed, fingerprint
from .sanitize import clean_text

BACKOFF_MIN = 30
FOCUS_DELAY = 5  # seconds: requests arriving together join one focused sync
REQUEUE_DELAY = 15  # seconds: a focused sync that found another sync running tries again
WATCH_CACHE_TTL = 24 * 60 * 60

# The JobRunner logger writes only to the job's own log. This one has no handler of its own, so
# with NetBox's default logging its warnings reach the worker's stderr (`docker logs`); a
# LOGGING setting can route it like any other logger.
worker_log = logging.getLogger("netbox_lantronix.sync")

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
        is named in the job log. Devices outside the source's organization are never asked
        about: the planner drops them, and their names stay out of the log.
        """
        org = source.organization_id
        wanted = {p.device_id for p in px_ports}
        states = {}
        for dev in px_devices:
            if dev.device_id not in wanted or org not in dev.org_ids:
                continue
            try:
                states[dev.device_id] = client.port_states(dev.device_id, org)
            except PercepxionUnavailable as exc:
                name = clean_text(dev.name, 64)
                self.logger.warning(f"Port state unavailable for {name}: {exc}")
        return [merge_state(p, states.get(p.device_id, {}).get(p.number)) for p in px_ports]

    def run(self, *args, manual=False, focus=False, **kwargs):
        if focus:
            self._run_focused()
            return
        if self.job.interval and self._schedule_superseded():
            # Clearing the interval stops NetBox from scheduling the next run of this chain.
            self.job.interval = None
            self.logger.info("Schedule changed or duplicated; this periodic run stops its chain")
            return
        self._guarded(manual)

    def _run_focused(self):
        """Sync only the console servers requested since the last focused run (events, the
        sync API with device_ids, the change watch)."""
        source = self.job.object
        if source is None:
            return
        devices = sync_requests(source).take()
        if not devices:
            self.logger.info("No console servers are waiting for a focused sync")
            return
        if not source.enabled:
            self.logger.info("Source is disabled; focused sync skipped")
            return
        try:
            self._guarded(focus=devices)
        except _Busy:
            # Requests are never dropped for a running sync: they wait for the next run.
            request_sync(source, devices, user=self.job.user, delay=REQUEUE_DELAY)
            self.logger.info(
                "Another sync for this source is running; this focused sync runs again after it"
            )

    def _guarded(self, manual=False, focus=None):
        try:
            self._sync(manual, focus)
        except _Busy:
            raise
        except JobFailed as exc:
            # NetBox doesn't store a JobFailed message; log it so the status page can show why.
            self.logger.error(str(exc))
            self.job.error = str(exc)[:500]
            worker_log.warning("%s failed: %s", self._label(), exc)
            raise
        except Exception:
            # NetBox stores the traceback on the job but prints nothing to the worker log.
            worker_log.exception("%s errored", self._label())
            raise

    def _label(self) -> str:
        source = self.job.object
        name = f"'{source}'" if source is not None else f"#{self.job.object_id}"
        return f"Percepxion sync for source {name} (job {self.job.pk})"

    def _schedule_superseded(self) -> bool:
        source = self.job.object
        return (
            source is None
            or not source.enabled
            or self.job.interval != source.sync_interval
            or waiting_periodic(source, exclude_pk=self.job.pk).exists()
        )

    def _sync(self, manual=False, focus=None):
        from .sync.apply import SyncPermissionDenied, apply_plan, record_findings

        source = self.job.object
        cfg = self._check_can_run(source, manual, busy_ok=focus is not None)
        user = _sync_user()
        if focus is not None:
            self.logger.info(f"Focused sync of {len(focus)} console server(s)")
        plan = self._read_and_plan(source, cfg, focus)
        try:
            counts = apply_plan(plan, source, user)
        except SyncPermissionDenied as exc:
            raise JobFailed(f"Sync user lacks NetBox permission: {exc}") from None
        if focus is None:
            counts["findings"] = record_findings(plan, source, self.job)
        else:
            # Findings stay with full runs, so frequent focused runs can't push them out of the
            # kept history; the next full run reports them.
            counts["findings"] = dict(Counter(f.kind for f in plan.findings))
            counts["focus"] = len(focus)
        self.job.data = counts
        _clear_backoff(source)
        dev = counts["devices"]
        self.logger.info(
            f"Devices created {dev['created']}, updated {dev['updated']}, "
            f"unchanged {dev['unchanged']}; ports updated {counts['ports']['updated']}"
        )
        if "cables" in counts:
            c = counts["cables"]
            self.logger.info(f"Console cables created {c['created']}, deleted {c['deleted']}")

    def _check_can_run(self, source, manual, busy_ok=False):
        """Fail the job before any Percepxion call if this run shouldn't happen. Returns the
        endpoint settings."""
        if source is None or not source.enabled:
            raise JobFailed("Source is disabled or deleted")
        cfg = source.endpoint_config
        if not cfg:
            raise JobFailed(f"Endpoint '{source.endpoint}' is not in plugin configuration")
        # Checked again here: the allowlist can change after a source was saved.
        if not organization_allowed(cfg, source.organization_id):
            raise JobFailed(
                f"Endpoint '{source.endpoint}' isn't configured to sync this source's organization"
            )
        if _backoff_blocks(source, cfg, manual):
            raise JobFailed(
                f"Login backoff until {source.backoff_until:%Y-%m-%d %H:%M} UTC; "
                "fix the endpoint credentials and restart NetBox, then Sync now"
            )
        if running_jobs(source, exclude_pk=self.job.pk).exists():
            if busy_ok:
                raise _Busy
            raise JobFailed("Another sync for this source is running")
        return cfg

    def _read_and_plan(self, source, cfg, focus=None):
        """Log in, read Percepxion and NetBox, and return the plan. Writes nothing to NetBox
        except the login backoff after a rejected login.

        With ``focus`` (Percepxion device IDs), every device is still read and planned, so
        names, adoption and "missing" stay right, but port state and SSH settings are read
        only for those console servers, and only their ports, facts and cables are planned.
        """
        from .sync.apply import build_context, build_snapshot
        from .sync.planner import plan as make_plan

        client = _client(cfg)
        try:
            client.login()
            px_devices = list(client.iter_devices())
            px_ports = list(client.iter_ports())
            if focus is not None:
                px_ports = [p for p in px_ports if p.device_id in focus]
            px_ports = self._with_port_states(client, source, px_devices, px_ports)
            nb_devices, nb_ports = build_snapshot(source, px_devices)
            plan = make_plan(
                build_context(source),
                px_devices,
                px_ports,
                nb_devices,
                nb_ports,
                self._sysconfig_reader(client, source.organization_id),
            )
            synced = plan.seen if focus is None else [d for d in plan.seen if d in focus]
            plan.ssh_ports = self._ssh_ports(client, source, cfg, synced)
            if source.manage_cables:
                # Only telemetry says whether a port is plugged in; port search alone is stale.
                plan.observed = [p for p in px_ports if p.number > 0 and "attached" in p.facts]
            return plan
        except AuthError:
            _start_backoff(source, cfg)
            raise JobFailed("Percepxion rejected the login for this endpoint") from None
        except (PercepxionUnavailable, PartialData) as exc:
            raise JobFailed(f"Percepxion read failed: {exc}") from None
        finally:
            client.logout()  # everything Percepxion is asked for has been read by now

    def _ssh_ports(self, client, source, cfg, device_ids):
        """Each synced unit's SSH TCP port per device port, from its configuration in
        Percepxion: one call per unit. None when the endpoint turns the read off.

        A unit whose configuration can't be read is left out and keeps what an earlier run
        stored; the run goes on. Percepxion has no configuration for a unit that never
        uploaded one, so that is reported as a count, not as a warning per unit.
        """
        if not cfg.get("read_port_config", True):
            return None
        ssh_ports, unread = {}, 0
        for device_id in sorted(device_ids):
            try:
                ssh_ports[device_id] = client.port_ssh(device_id, source.organization_id)
            except PercepxionUnavailable:
                unread += 1
        if unread:
            self.logger.info(
                f"Port SSH settings unavailable for {unread} of {len(device_ids)} devices; "
                "their Open console links keep the last known ports, or the source's base port"
            )
        return ssh_ports

    def _sysconfig_reader(self, client, org):
        """Return a function giving the device-type slug from a device's stored
        `show sysconfig` output, or None. The job list is fetched on first use."""
        jobs = None

        def sysconfig(pxid):
            nonlocal jobs
            try:
                if jobs is None:
                    # A project_admin session gets HTTP 400 on job and telemetry searches
                    # without tenant_id; tenant_user sessions accept it too.
                    jobs = client.latest_sysconfig_jobs(org)
                job = jobs.get(pxid)
                if not job:
                    return None
                text = client.cli_output(job[0], pxid, org)
            except (PercepxionUnavailable, PartialData) as exc:
                # One unreadable result must not stop the sync: the planner falls back to
                # the model rules. A failed job search isn't retried for every device.
                if jobs is None:
                    jobs = {}
                self.logger.warning(f"Stored show sysconfig output unavailable: {exc}")
                return None
            return sku_from_sysconfig(parse_sysconfig(text))

        return sysconfig


class _Busy(Exception):
    """A focused sync found another sync for the source running."""


def _client(cfg):
    return PercepxionClient(
        cfg["url"],
        cfg["username"],
        cfg["password"],
        verify=cfg.get("verify", True),
        ca_bundle=cfg.get("ca_bundle"),
        proxy=cfg.get("proxy"),
    )


# -- focused sync requests -------------------------------------------------------------------
#
# Events, the sync API with device_ids and the change watch ask for some console servers to be
# synced. Requests made while a focused sync waits to start join it (percepxion/sync_requests).


def sync_requests(source) -> SyncRequests:
    return SyncRequests(cache, f"netbox_lantronix:source:{source.pk}")


def request_sync(source, device_ids, user=None, delay=FOCUS_DELAY) -> bool:
    """Ask for a focused sync of these console servers (Percepxion device IDs). Returns True if
    this call enqueued the job, False if the IDs joined one already waiting."""
    requests = sync_requests(source)
    if not requests.add(device_ids):
        return False
    try:
        SyncJob.enqueue(
            instance=source,
            user=user or _sync_user_or_none(),
            schedule_at=timezone.now() + timedelta(seconds=delay),
            focus=True,
        )
    except Exception:
        requests.release()
        raise
    return True


# -- change watch ----------------------------------------------------------------------------


class WatchJob(JobRunner):
    """Read each linked console server's port state and request a focused sync for the ones
    whose ports changed since the last check. Bridges the time until Percepxion sends events;
    with events, a long interval or none."""

    class Meta:
        name = "Percepxion change watch"

    def run(self, *args, **kwargs):
        source = self.job.object
        if self.job.interval and _watch_superseded(source, self.job):
            self.job.interval = None
            self.logger.info("Watch schedule changed or duplicated; this run stops its chain")
            return
        if source is None or not source.enabled:
            return
        cfg = source.endpoint_config
        if not cfg or not organization_allowed(cfg, source.organization_id):
            raise JobFailed(
                f"Endpoint '{source.endpoint}' is missing or isn't configured for this organization"
            )
        if _backoff_blocks(source, cfg, False):
            self.logger.info("Percepxion login backoff in effect; this check is skipped")
            return
        from .models import DeviceLink, LinkStatusChoices

        linked = set(
            DeviceLink.objects.filter(source=source, status=LinkStatusChoices.ACTIVE).values_list(
                "percepxion_device_id", flat=True
            )
        )
        if not linked:
            self.logger.info("No linked console servers to watch")
            return
        org = source.organization_id
        current, unread = {}, 0
        client = _client(cfg)
        try:
            client.login()
            for dev in client.iter_devices():
                if dev.device_id not in linked or org not in dev.org_ids:
                    continue
                try:
                    current[dev.device_id] = fingerprint(client.port_states(dev.device_id, org))
                except PercepxionUnavailable:
                    unread += 1  # keeps its last fingerprint, so it's no change
        except AuthError:
            _start_backoff(source, cfg)
            raise JobFailed("Percepxion rejected the login for this endpoint") from None
        except (PercepxionUnavailable, PartialData) as exc:
            raise JobFailed(f"Percepxion read failed: {exc}") from None
        finally:
            client.logout()
        key = f"netbox_lantronix:watch:{source.pk}"
        previous = cache.get(key) or {}
        moved = changed(previous, current)
        kept = {d: fp for d, fp in previous.items() if d in linked and d not in current}
        cache.set(key, {**kept, **current}, WATCH_CACHE_TTL)
        if moved:
            request_sync(source, moved, user=self.job.user)
            self.logger.info(
                f"Ports changed on {len(moved)} console server(s); focused sync requested"
            )
        self.job.data = {"watched": len(current), "changed": len(moved), "unread": unread}


def waiting_watch(source, exclude_pk=None):
    jobs = WatchJob.get_jobs(source).filter(status__in=WAITING_STATES, interval__isnull=False)
    return jobs.exclude(pk=exclude_pk) if exclude_pk else jobs


def _watch_superseded(source, job) -> bool:
    return (
        source is None
        or not source.enabled
        or job.interval != source.watch_interval
        or waiting_watch(source, exclude_pk=job.pk).exists()
    )


def schedule_watch(source, user=None):
    """Create, update or remove the periodic change watch for a source. Same rules as
    ``schedule``: called only after checking the user holds the sync action."""
    waiting = waiting_watch(source)
    if not source.enabled or not source.watch_interval:
        waiting.delete()
        return None
    keep = waiting.filter(interval=source.watch_interval).order_by("scheduled").first()
    waiting.exclude(pk=keep.pk if keep else None).delete()
    if keep:
        return keep
    running = (
        WatchJob.get_jobs(source)
        .filter(status=JobStatusChoices.STATUS_RUNNING, interval=source.watch_interval)
        .first()
    )
    if running:
        return running
    return WatchJob.enqueue(instance=source, interval=source.watch_interval, user=user)


# -- login backoff ---------------------------------------------------------------------------
#
# After Percepxion rejects a login, scheduled runs stop until the backoff ends. Only "Sync now"
# with changed credentials may try sooner, so repeated clicks can't hammer the Percepxion
# login with the rejected password.


def _backoff_blocks(source, cfg, manual) -> bool:
    if not source.backoff_until or source.backoff_until <= timezone.now():
        return False
    credentials_changed = cache.get(_backoff_key(source)) != _credential_tag(cfg)
    return not (manual and credentials_changed)


def _start_backoff(source, cfg) -> None:
    minutes = max(BACKOFF_MIN, source.sync_interval)
    source.backoff_until = timezone.now() + timedelta(minutes=minutes)
    cache.set(_backoff_key(source), _credential_tag(cfg), timeout=minutes * 60)
    type(source).objects.filter(pk=source.pk).update(backoff_until=source.backoff_until)


def _clear_backoff(source) -> None:
    if source.backoff_until:
        type(source).objects.filter(pk=source.pk).update(backoff_until=None)


def _backoff_key(source):
    return f"netbox_lantronix:backoff:{source.pk}"


def _credential_tag(cfg):
    """Keyed hash of the endpoint credentials; tells whether they changed, reveals nothing."""
    material = "\x00".join(str(cfg.get(k, "")) for k in ("url", "username", "password"))
    return hmac.new(settings.SECRET_KEY.encode(), material.encode(), hashlib.sha256).hexdigest()


def _sync_user_or_none():
    name = get_plugin_config("netbox_lantronix", "sync_user")
    return get_user_model().objects.filter(username=name).first() if name else None


def _sync_user():
    user = _sync_user_or_none()
    if user is None:
        raise JobFailed("Configured sync_user does not exist in NetBox")
    return user


def suspend(source):
    """Remove pending scheduled runs and watch checks; used when a user without the sync action
    saves a source."""
    waiting_watch(source).delete()
    return waiting_periodic(source).delete()


def apply_schedule_for(source, user):
    """Schedule if ``user`` may sync this source, else suspend. Returns True if scheduled."""
    if user.has_perm("netbox_lantronix.sync_percepxionsource", source):
        schedule(source, user)
        schedule_watch(source, user)
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
