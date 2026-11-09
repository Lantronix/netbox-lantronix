"""NetBox can list the plugin. Runs under the NetBox test runner (manage.py test)."""

import importlib

from django.test import TestCase
from users.models import User

from netbox_lantronix import NetBoxLantronixConfig, __version__


class PluginRegistryTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create(username="admin", is_superuser=True))

    def test_package_config_is_the_plugin_config(self):
        # NetBox reads <package>.config; a submodule named config would replace it at startup.
        self.assertIs(importlib.import_module("netbox_lantronix").config, NetBoxLantronixConfig)

    def test_plugins_page_lists_the_plugin(self):
        response = self.client.get("/core/plugins/")
        self.assertEqual(response.status_code, 200)

    def test_status_api_reports_the_plugin_version(self):
        response = self.client.get("/api/status/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["plugins"].get("netbox_lantronix"), __version__)
