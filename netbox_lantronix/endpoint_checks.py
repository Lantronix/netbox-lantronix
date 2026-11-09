"""Plugin configuration checks. No NetBox imports."""

REQUIRED_KEYS = ("url", "username", "password")
OPTIONAL_KEYS = ("verify", "ca_bundle", "web_port_url", "proxy")


class ConfigError(ValueError):
    """Raised for invalid plugin configuration. Messages name keys, never values."""


def validate_endpoints(endpoints):
    """Check every endpoint has an https url and credentials. Returns the endpoint names."""
    if not isinstance(endpoints, dict):
        raise ConfigError("netbox_lantronix: 'endpoints' must be a mapping of name to settings")
    for name, settings in endpoints.items():
        if not isinstance(settings, dict):
            raise ConfigError(f"netbox_lantronix: endpoint '{name}' must be a mapping")
        for key in REQUIRED_KEYS:
            if not settings.get(key):
                raise ConfigError(f"netbox_lantronix: endpoint '{name}' is missing '{key}'")
        if not str(settings["url"]).lower().startswith("https://"):
            raise ConfigError(f"netbox_lantronix: endpoint '{name}' url must use https://")
        unknown = set(settings) - set(REQUIRED_KEYS) - set(OPTIONAL_KEYS)
        if unknown:
            keys = ", ".join(sorted(unknown))
            raise ConfigError(f"netbox_lantronix: endpoint '{name}' has unknown keys: {keys}")
    return sorted(endpoints)
