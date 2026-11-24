"""The signed events endpoint and the sync action with device_ids."""

import json
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from users.models import User

from ..models import PercepxionSource
from ..percepxion.events import sign
from .netbox_fixtures import ORG, grant, make_world

SECRET = "w" * 40
ENDPOINTS = {
    "percepxion-sandbox": {
        "url": "https://px.example.com/api",
        "username": "svc",
        "password": "pw",
        "webhook_secrets": [SECRET],
    }
}


def cfg(plugin, key):
    return {"endpoints": ENDPOINTS, "sync_user": "percepxion-sync"}.get(key)


def event(
    event_id="evt-1", event_type="percepxion.port.attached_changed", org=ORG, pxid="dev-0001"
):
    return json.dumps(
        {
            "spec_version": "1",
            "id": event_id,
            "type": event_type,
            "source": "test",
            "occurred_at": "2026-11-24T00:00:00Z",
            "tenant_id": org,
            "device": {"id": pxid, "name": "SLC-A", "serial": "1", "model": "SLC9032"},
            "data": {"port": 3, "change": "detached"},
        }
    ).encode()


@override_settings(EXEMPT_VIEW_PERMISSIONS=[], LOGIN_REQUIRED=True)
@mock.patch("netbox_lantronix.models.get_plugin_config", cfg)
class EventsEndpointTest(TestCase):
    def setUp(self):
        cache.clear()
        self.w = make_world()
        self.source = self.w["source"]
        self.url = reverse(
            "plugins-api:netbox_lantronix-api:percepxionsource-events",
            kwargs={"pk": self.source.pk},
        )

    def post(self, body, secret=SECRET, headers=None, url=None):
        h = sign(secret, body) if headers is None else headers
        extra = {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in h.items()}
        return self.client.post(url or self.url, body, content_type="application/json", **extra)

    def test_signed_event_requests_a_focused_sync_without_a_login(self):
        with mock.patch("netbox_lantronix.api.views.request_sync") as req:
            r = self.post(event())
        self.assertEqual(r.status_code, 202)
        req.assert_called_once_with(self.source, ["dev-0001"])

    def test_duplicate_event_ids_are_dropped(self):
        with mock.patch("netbox_lantronix.api.views.request_sync") as req:
            self.assertEqual(self.post(event("e-9")).status_code, 202)
            self.assertEqual(self.post(event("e-9")).status_code, 204)
        req.assert_called_once()

    def test_every_sync_event_type_is_accepted(self):
        types = (
            "percepxion.port.attached_changed",
            "percepxion.port.state_changed",
            "percepxion.device.status_changed",
        )
        with mock.patch("netbox_lantronix.api.views.request_sync") as req:
            for i, t in enumerate(types):
                self.assertEqual(self.post(event(f"e{i}", t)).status_code, 202, t)
        self.assertEqual(req.call_count, 3)

    def test_other_event_types_are_ignored(self):
        with mock.patch("netbox_lantronix.api.views.request_sync") as req:
            r = self.post(event(event_type="percepxion.rule.triggered"))
        self.assertEqual(r.status_code, 204)
        req.assert_not_called()

    def test_refusals(self):
        body = event()
        with mock.patch("netbox_lantronix.api.views.request_sync") as req:
            self.assertEqual(self.post(body, secret="x" * 40).status_code, 401)
            self.assertEqual(self.post(body, headers={}).status_code, 401)
            stale = sign(SECRET, body, timestamp=1)
            self.assertEqual(self.post(body, headers=stale).status_code, 401)
            self.assertEqual(self.post(event(org="org-other")).status_code, 400)
            self.assertEqual(self.post(b"{}").status_code, 400)
            self.assertEqual(self.post(b"").status_code, 400)
            self.assertEqual(self.post(event(pxid="bad id!")).status_code, 400)
        req.assert_not_called()

    def test_unknown_or_disabled_source_or_no_secrets_is_not_found(self):
        missing = reverse(
            "plugins-api:netbox_lantronix-api:percepxionsource-events", kwargs={"pk": 999}
        )
        self.assertEqual(self.post(event(), url=missing).status_code, 404)
        PercepxionSource.objects.filter(pk=self.source.pk).update(enabled=False)
        self.assertEqual(self.post(event()).status_code, 404)
        PercepxionSource.objects.filter(pk=self.source.pk).update(enabled=True)
        no_secrets = {
            "percepxion-sandbox": {**ENDPOINTS["percepxion-sandbox"], "webhook_secrets": []}
        }
        with mock.patch(
            "netbox_lantronix.models.get_plugin_config",
            lambda p, k: no_secrets if k == "endpoints" else None,
        ):
            self.assertEqual(self.post(event()).status_code, 404)

    def test_only_post(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_refusal_reason_never_echoes_the_body(self):
        body = event(event_id="secret-looking-id")
        r = self.post(body, secret="x" * 40)
        self.assertNotIn("secret-looking-id", r.content.decode())


@override_settings(EXEMPT_VIEW_PERMISSIONS=[], LOGIN_REQUIRED=True)
@mock.patch("netbox_lantronix.models.get_plugin_config", cfg)
class SyncActionDeviceIdsTest(TestCase):
    def setUp(self):
        cache.clear()
        self.w = make_world()
        self.source = self.w["source"]
        self.url = reverse(
            "plugins-api:netbox_lantronix-api:percepxionsource-sync", kwargs={"pk": self.source.pk}
        )
        self.syncer = User.objects.create(username="syncer")
        grant(self.syncer, [PercepxionSource], ["view", "sync"])
        self.client.force_login(self.syncer)

    def post(self, data):
        return self.client.post(self.url, data, content_type="application/json")

    def test_focused_request_is_accepted_even_while_a_sync_runs(self):
        with (
            mock.patch("netbox_lantronix.api.views.sync_is_active", return_value=True),
            mock.patch("netbox_lantronix.api.views.request_sync", return_value=True) as req,
        ):
            r = self.post({"device_ids": ["dev-0001", "dev-0002"]})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json(), {"focused": True, "devices": 2, "new_job": True})
        req.assert_called_once_with(self.source, ["dev-0001", "dev-0002"], user=self.syncer)

    def test_bad_device_ids(self):
        with mock.patch("netbox_lantronix.api.views.request_sync") as req:
            for bad in ([], "dev-0001", ["bad id!"], [1], ["d"] * 501):
                self.assertEqual(self.post({"device_ids": bad}).status_code, 400, bad)
        req.assert_not_called()

    def test_without_device_ids_behaves_as_before(self):
        with (
            mock.patch("netbox_lantronix.api.views.sync_is_active", return_value=True),
            mock.patch("netbox_lantronix.api.views.request_sync") as req,
        ):
            self.assertEqual(self.post({}).status_code, 409)
        req.assert_not_called()

    def test_needs_the_sync_permission(self):
        viewer = User.objects.create(username="viewer2")
        grant(viewer, [PercepxionSource], ["view"])
        self.client.force_login(viewer)
        with mock.patch("netbox_lantronix.api.views.request_sync") as req:
            self.assertEqual(self.post({"device_ids": ["dev-0001"]}).status_code, 403)
        req.assert_not_called()
