import pytest

from netbox_lantronix.endpoint_checks import (
    ConfigError,
    organization_allowed,
    validate_endpoints,
)

GOOD = {"px": {"url": "https://px.example.com", "username": "svc", "password": "s3cret-value"}}


def test_valid():
    assert validate_endpoints(GOOD) == ["px"]


@pytest.mark.parametrize("key", ["url", "username", "password"])
def test_missing_key_names_key_not_value(key):
    cfg = {"px": dict(GOOD["px"])}
    del cfg["px"][key]
    with pytest.raises(ConfigError) as exc:
        validate_endpoints(cfg)
    assert f"'{key}'" in str(exc.value)
    assert "s3cret-value" not in str(exc.value)


def test_http_rejected():
    cfg = {"px": dict(GOOD["px"], url="http://px.example.com")}
    with pytest.raises(ConfigError, match="https"):
        validate_endpoints(cfg)


def test_unknown_key():
    cfg = {"px": dict(GOOD["px"], passwd="x")}
    with pytest.raises(ConfigError, match="unknown keys: passwd"):
        validate_endpoints(cfg)


def test_not_mapping():
    with pytest.raises(ConfigError):
        validate_endpoints([])


@pytest.mark.parametrize("value", [None, 0, "", "false", 1])
def test_verify_must_be_a_bool(value):
    cfg = {"px": dict(GOOD["px"], verify=value)}
    with pytest.raises(ConfigError, match="'verify' must be True or False"):
        validate_endpoints(cfg)


@pytest.mark.parametrize("value", [True, False])
def test_verify_bool_accepted(value):
    assert validate_endpoints({"px": dict(GOOD["px"], verify=value)}) == ["px"]


@pytest.mark.parametrize("value", ["org-1", [""], [1], None])
def test_organizations_must_be_a_list_of_ids(value):
    cfg = {"px": dict(GOOD["px"], organizations=value)}
    with pytest.raises(ConfigError, match="'organizations'"):
        validate_endpoints(cfg)


def test_organization_allowlist():
    assert organization_allowed(GOOD["px"], "any-org")  # no key: any organization
    limited = dict(GOOD["px"], organizations=["org-1", "org-2"])
    assert validate_endpoints({"px": limited}) == ["px"]
    assert organization_allowed(limited, "org-2")
    assert not organization_allowed(limited, "org-3")
    assert not organization_allowed(dict(GOOD["px"], organizations=[]), "org-1")
