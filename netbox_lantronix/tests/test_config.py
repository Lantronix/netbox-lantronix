import pytest

from netbox_lantronix.config import ConfigError, validate_endpoints

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
