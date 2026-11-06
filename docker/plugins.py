# Copy to configuration/plugins.py in a netbox-docker checkout.
# Credentials come from env/percepxion.env.
import os

PLUGINS = ["netbox_lantronix"]

PLUGINS_CONFIG = {
    "netbox_lantronix": {
        "sync_user": "percepxion-sync",
        "endpoints": {
            "percepxion": {
                "url": os.environ.get("PERCEPXION_URL", ""),
                "username": os.environ.get("PERCEPXION_USERNAME", ""),
                "password": os.environ.get("PERCEPXION_PASSWORD", ""),
            },
        },
    },
}
