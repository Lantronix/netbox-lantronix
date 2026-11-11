"""Plugin configuration checks. No NetBox imports."""

REQUIRED_KEYS = ("url", "username", "password")
OPTIONAL_KEYS = ("verify", "ca_bundle", "web_port_url", "proxy", "organizations")


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
        # None, 0 or "" would turn certificate checks off as surely as False, without the warning.
        if not isinstance(settings.get("verify", True), bool):
            raise ConfigError(f"netbox_lantronix: endpoint '{name}' 'verify' must be True or False")
        if "organizations" in settings:
            orgs = settings["organizations"]
            if not isinstance(orgs, (list, tuple, set, frozenset)) or not all(
                isinstance(o, str) and o for o in orgs
            ):
                raise ConfigError(
                    f"netbox_lantronix: endpoint '{name}' 'organizations' must be a list of "
                    "Percepxion organization IDs"
                )
        unknown = set(settings) - set(REQUIRED_KEYS) - set(OPTIONAL_KEYS)
        if unknown:
            keys = ", ".join(sorted(unknown))
            raise ConfigError(f"netbox_lantronix: endpoint '{name}' has unknown keys: {keys}")
    return sorted(endpoints)


def organization_allowed(settings, organization_id) -> bool:
    """True if the endpoint may sync this organization. No 'organizations' key allows any."""
    if not settings or "organizations" not in settings:
        return True
    return organization_id in settings["organizations"]
