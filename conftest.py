"""Stub netbox.plugins when NetBox is not installed, so pure-function tests import the package.

Lives at the repo root: pytest imports netbox_lantronix/__init__.py (which imports netbox)
before it would load a conftest inside netbox_lantronix/tests.
"""

import sys
import types

try:
    import netbox  # noqa: F401
except ImportError:
    netbox_stub = types.ModuleType("netbox")
    plugins_stub = types.ModuleType("netbox.plugins")

    class PluginConfig:
        pass

    plugins_stub.PluginConfig = PluginConfig
    netbox_stub.plugins = plugins_stub
    sys.modules["netbox"] = netbox_stub
    sys.modules["netbox.plugins"] = plugins_stub
