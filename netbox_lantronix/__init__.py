from netbox.plugins import PluginConfig

__version__ = "0.5.4"


class NetBoxLantronixConfig(PluginConfig):
    name = "netbox_lantronix"
    verbose_name = "Lantronix"
    author = "Lantronix"
    description = "Sync Lantronix SLC and EMG devices from Percepxion into NetBox"
    version = __version__
    base_url = "lantronix"
    min_version = "4.6.0"
    max_version = "4.7.99"
    default_settings = {
        "sync_user": None,
        "endpoints": {},
    }

    def ready(self):
        super().ready()
        from netbox.plugins import get_plugin_config

        from .endpoint_checks import validate_endpoints

        validate_endpoints(get_plugin_config(self.name, "endpoints"))


config = NetBoxLantronixConfig
