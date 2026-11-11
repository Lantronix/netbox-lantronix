"""Views, panels and permissions."""

import re
from unittest import mock

from dcim.models import ConsoleServerPort, Device, DeviceRole, Interface, Site
from django.test import TestCase, override_settings
from django.urls import reverse
from ipam.models import IPAddress
from users.models import User

from ..models import DeviceLink, LinkStatusChoices, PercepxionSource, PortFacts, SyncFinding
from .netbox_fixtures import TYPE_SLUG, grant, make_world

ENDPOINTS = {
    "percepxion-sandbox": {
        "url": "https://px.example.com/api",
        "username": "svc",
        "password": "pw",
        "web_port_url": "https://px.example.com/#/app/explore/list/details/{device_id}/{device_name}/ports",
    }
}


def cfg(plugin, key):
    return {"endpoints": ENDPOINTS, "sync_user": "percepxion-sync"}.get(key)


@override_settings(EXEMPT_VIEW_PERMISSIONS=[], LOGIN_REQUIRED=True)
@mock.patch("netbox_lantronix.models.get_plugin_config", cfg)
class ViewTest(TestCase):
    def setUp(self):
        self.w = make_world()
        self.source = self.w["source"]
        self.admin = User.objects.create(username="admin", is_superuser=True)
        self.viewer = User.objects.create(username="viewer")
        grant(self.viewer, [Device, ConsoleServerPort, Site, DeviceRole], ["view"])
        self.device = Device.objects.create(
            name="SLC-A", device_type=self.w["dtype"], role=self.w["role"], site=self.w["site"]
        )
        iface = Interface.objects.create(device=self.device, name="eth1", type="1000base-t")
        ip = IPAddress.objects.create(address="192.0.2.75/24", assigned_object=iface)
        Device.objects.filter(pk=self.device.pk).update(primary_ip4=ip)
        self.link = DeviceLink.objects.create(
            source=self.source, device=self.device, percepxion_device_id="dev-0001"
        )
        self.port = ConsoleServerPort.objects.get(device=self.device, name="Port-02")

    def get(self, user, url):
        self.client.force_login(user)
        return self.client.get(url)

    def test_pages_render_for_admin(self):
        SyncFinding.objects.create(source=self.source, kind="ambiguous_model", device_name="X")
        urls = [
            reverse("plugins:netbox_lantronix:percepxionsource_list"),
            self.source.get_absolute_url(),
            reverse("plugins:netbox_lantronix:percepxionsource_add"),
            reverse("plugins:netbox_lantronix:devicelink_list"),
            self.link.get_absolute_url(),
            reverse("plugins:netbox_lantronix:modelrule_list"),
            reverse("plugins:netbox_lantronix:modelrule_add"),
            reverse("plugins:netbox_lantronix:devicetypeoverride_list"),
            reverse("plugins:netbox_lantronix:devicetypeoverride_add"),
            reverse("plugins:netbox_lantronix:syncfinding_list"),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.get(self.admin, url).status_code, 200)

    def test_console_buttons(self):
        body = self.get(self.admin, self.port.get_absolute_url()).content.decode()
        self.assertIn("ssh://192.0.2.75:3002", body)
        self.assertNotIn("Open in Percepxion", body)
        PercepxionSource.objects.filter(pk=self.source.pk).update(percepxion_link=True)
        body = self.get(self.admin, self.port.get_absolute_url()).content.decode()
        self.assertIn("/details/dev-0001/SLC-A/ports", body)

    def test_ssh_falls_back_to_percepxion_ip(self):
        Device.objects.filter(pk=self.device.pk).update(primary_ip4=None)
        DeviceLink.objects.filter(pk=self.link.pk).update(mgmt_ip="198.51.100.7")
        body = self.get(self.admin, self.port.get_absolute_url()).content.decode()
        self.assertIn("ssh://198.51.100.7:3002", body)
        DeviceLink.objects.filter(pk=self.link.pk).update(mgmt_ip="fe80::1%x@evil.example")
        body = self.get(self.admin, self.port.get_absolute_url()).content.decode()
        self.assertNotIn("ssh://", body)
        DeviceLink.objects.filter(pk=self.link.pk).update(mgmt_ip="")
        body = self.get(self.admin, self.port.get_absolute_url()).content.decode()
        self.assertNotIn("ssh://", body)

    def test_port_facts_without_make_or_os_name(self):
        PortFacts.objects.create(
            port=self.port,
            model="C9300-48P",
            os_version="17.9.4a",
            last_reported="2026-10-29T00:00:00Z",
        )
        body = self.get(self.admin, self.port.get_absolute_url()).content.decode()
        rows = dict(re.findall(r'<th scope="row">([^<]+)</th><td>(.*?)</td>', body, re.S))
        self.assertEqual(rows["Make / model"].strip(), "C9300-48P")
        self.assertEqual(rows["OS"].strip(), "17.9.4a")
        self.assertIn("mdash", rows["Serial"])

    def test_buttons_and_panels_need_view_permission(self):
        PortFacts.objects.create(
            port=self.port,
            make="cisco",
            model="WS-C3560CG-8TC-S",
            last_reported="2026-10-29T00:00:00Z",
        )
        port_body = self.get(self.viewer, self.port.get_absolute_url()).content.decode()
        device_body = self.get(self.viewer, self.device.get_absolute_url()).content.decode()
        self.assertNotIn("ssh://", port_body)
        self.assertNotIn("WS-C3560CG", port_body)
        self.assertNotIn("Type chosen from", device_body)
        device_body = self.get(self.admin, self.device.get_absolute_url()).content.decode()
        self.assertIn("Type chosen from", device_body)

    def test_no_buttons_on_unlinked_or_pending_devices(self):
        DeviceLink.objects.filter(pk=self.link.pk).update(status=LinkStatusChoices.PENDING)
        body = self.get(self.admin, self.port.get_absolute_url()).content.decode()
        self.assertNotIn("ssh://", body)

    def test_sync_now_permissions(self):
        url = self.source.get_absolute_url() + "sync/"
        grant(self.viewer, [PercepxionSource], ["view"])
        with mock.patch("netbox_lantronix.views.SyncJob.enqueue") as enqueue:
            self.client.force_login(self.viewer)
            self.client.post(url)
            enqueue.assert_not_called()
            self.client.force_login(self.admin)
            self.client.post(url)
            enqueue.assert_called_once()
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_api_sync_action(self):
        url = reverse(
            "plugins-api:netbox_lantronix-api:percepxionsource-sync", kwargs={"pk": self.source.pk}
        )
        grant(self.viewer, [PercepxionSource], ["view"])
        with (
            mock.patch("netbox_lantronix.api.views.SyncJob.enqueue") as enqueue,
            mock.patch("netbox_lantronix.api.views.sync_is_active", return_value=False),
        ):
            enqueue.return_value = mock.Mock(pk=7, get_absolute_url=lambda: "/core/jobs/7/")
            self.client.force_login(self.viewer)
            self.assertEqual(self.client.post(url).status_code, 403)
            enqueue.assert_not_called()
            self.client.force_login(self.admin)
            r = self.client.post(url)
            self.assertEqual(r.status_code, 202)
            self.assertEqual(r.json()["job"]["id"], 7)
            enqueue.assert_called_once()
        with (
            mock.patch("netbox_lantronix.api.views.sync_is_active", return_value=True),
            mock.patch("netbox_lantronix.api.views.SyncJob.enqueue") as enqueue,
        ):
            self.assertEqual(self.client.post(url).status_code, 409)
            enqueue.assert_not_called()
        PercepxionSource.objects.filter(pk=self.source.pk).update(enabled=False)
        self.assertEqual(self.client.post(url).status_code, 409)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_api_sync_action_with_only_sync_permission(self):
        url = reverse(
            "plugins-api:netbox_lantronix-api:percepxionsource-sync", kwargs={"pk": self.source.pk}
        )
        syncer = User.objects.create(username="syncer")
        grant(syncer, [PercepxionSource], ["view", "sync"])
        with (
            mock.patch("netbox_lantronix.api.views.SyncJob.enqueue") as enqueue,
            mock.patch("netbox_lantronix.api.views.sync_is_active", return_value=False),
        ):
            enqueue.return_value = mock.Mock(pk=8, get_absolute_url=lambda: "/core/jobs/8/")
            self.client.force_login(syncer)
            self.assertEqual(self.client.post(url).status_code, 202)
            enqueue.assert_called_once()
        self.client.logout()
        self.assertIn(self.client.post(url).status_code, (401, 403))

    def test_approve_pending_link(self):
        DeviceLink.objects.filter(pk=self.link.pk).update(status=LinkStatusChoices.PENDING)
        url = self.link.get_absolute_url() + "approve/"
        grant(self.viewer, [DeviceLink], ["view"])
        self.client.force_login(self.viewer)
        self.client.post(url)
        self.link.refresh_from_db()
        self.assertEqual(self.link.status, LinkStatusChoices.PENDING)
        self.client.force_login(self.admin)
        self.client.post(url)
        self.link.refresh_from_db()
        self.assertEqual(self.link.status, LinkStatusChoices.ACTIVE)

    def test_organization_must_be_on_the_endpoint_allowlist(self):
        limited = {
            "percepxion-sandbox": {**ENDPOINTS["percepxion-sandbox"], "organizations": ["org-0001"]}
        }
        api = reverse(
            "plugins-api:netbox_lantronix-api:percepxionsource-detail",
            kwargs={"pk": self.source.pk},
        )
        self.client.force_login(self.admin)
        with (
            mock.patch(
                "netbox_lantronix.models.get_plugin_config",
                lambda p, k: {"endpoints": limited, "sync_user": "percepxion-sync"}.get(k),
            ),
            mock.patch("netbox_lantronix.jobs.schedule"),
        ):
            r = self.client.patch(
                api, {"organization_id": "org-other"}, content_type="application/json"
            )
            self.assertEqual(r.status_code, 400)
            self.assertIn("organization_id", r.json())
            ok = self.client.patch(api, {"name": "renamed"}, content_type="application/json")
            self.assertEqual(ok.status_code, 200)
        self.source.refresh_from_db()
        self.assertEqual((self.source.organization_id, self.source.name), ("org-0001", "renamed"))

    def test_tls_warning_shows_whenever_verify_is_off(self):
        off = {"percepxion-sandbox": {**ENDPOINTS["percepxion-sandbox"], "verify": False}}
        with mock.patch(
            "netbox_lantronix.models.get_plugin_config",
            lambda p, k: {"endpoints": off, "sync_user": "percepxion-sync"}.get(k),
        ):
            r = self.get(self.admin, self.source.get_absolute_url())
        self.assertTrue(r.context["tls_off"])
        self.assertFalse(self.get(self.admin, self.source.get_absolute_url()).context["tls_off"])

    def test_editor_without_sync_action_suspends_schedule(self):
        editor = User.objects.create(username="editor")
        grant(editor, [Site, DeviceRole], ["view"])
        grant(editor, [PercepxionSource], ["view", "change"])
        data = {
            "name": "test",
            "endpoint": "percepxion-sandbox",
            "organization_id": "org-other",
            "enabled": "on",
            "site": self.w["site"].pk,
            "role": self.w["role"].pk,
            "sync_interval": 60,
            "ssh_base_port": 3000,
        }
        with (
            mock.patch("netbox_lantronix.jobs.suspend") as suspend,
            mock.patch("netbox_lantronix.jobs.schedule") as schedule,
        ):
            self.client.force_login(editor)
            r = self.client.post(self.source.get_absolute_url() + "edit/", data)
            self.assertEqual(r.status_code, 302)
            suspend.assert_called_once()
            schedule.assert_not_called()
            self.client.force_login(self.admin)
            self.client.post(self.source.get_absolute_url() + "edit/", data)
            schedule.assert_called_once()
        self.assertEqual(Device.objects.get(pk=self.device.pk).device_type.slug, TYPE_SLUG)


@override_settings(EXEMPT_VIEW_PERMISSIONS=[], LOGIN_REQUIRED=True)
@mock.patch("netbox_lantronix.models.get_plugin_config", cfg)
class CreateRuleShortcutTest(TestCase):
    def setUp(self):
        from dcim.models import DeviceType

        self.w = make_world()
        self.source = self.w["source"]
        mfr = self.w["mfr"]
        for slug, model in (
            ("lantronix-slc9016", "SLC9016"),
            ("lantronix-slc9016-16e", "SLC9016-16E"),
        ):
            DeviceType.objects.create(manufacturer=mfr, model=model, slug=slug)
        self.admin = User.objects.create(username="admin", is_superuser=True)
        self.finding = SyncFinding.objects.create(
            source=self.source,
            kind="ambiguous_model",
            device_name="SLC-X",
            percepxion_device_id="dev-0009",
            model="SLC9016",
            rj45_ports=16,
            usb_ports=0,
        )
        self.client.force_login(self.admin)

    def test_finding_offers_prefilled_rule_and_override(self):
        body = self.client.get(self.source.get_absolute_url()).content.decode()
        self.assertIn("Create rule", body)
        self.assertIn("model=SLC9016", body)
        self.assertIn("percepxion_device_id=dev-0009", body)

    def test_rule_form_limits_types_to_candidates(self):
        from ..forms import ModelRuleForm

        form = ModelRuleForm(initial={"model": "SLC9016", "rj45_ports": "16", "usb_ports": "0"})
        slugs = set(form.fields["device_type"].queryset.values_list("slug", flat=True))
        self.assertEqual(slugs, {"lantronix-slc9016", "lantronix-slc9016-16e"})
        url = (
            reverse("plugins:netbox_lantronix:modelrule_add") + "?" + self.finding.rule_add_query()
        )
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_saving_rule_from_finding(self):
        from dcim.models import DeviceType

        from ..models import ModelRule

        data = {
            "source": self.source.pk,
            "model": "SLC9016",
            "rj45_ports": 16,
            "usb_ports": 0,
            "device_type": DeviceType.objects.get(slug="lantronix-slc9016-16e").pk,
        }
        r = self.client.post(reverse("plugins:netbox_lantronix:modelrule_add"), data)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(ModelRule.objects.filter(source=self.source, model="SLC9016").exists())
        bad = dict(data, device_type=self.w["dtype"].pk)  # SLC9032: not a candidate
        self.client.post(reverse("plugins:netbox_lantronix:modelrule_add"), dict(bad, usb_ports=1))
        self.assertEqual(ModelRule.objects.count(), 1)
