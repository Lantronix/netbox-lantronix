# Shared code: this file is the same in netbox-lantronix and nautobot-app-ssot-lantronix.
# scripts/check_shared.py fails CI when the copies differ, so change both.
"""Per-port SSH settings read from the stored device configuration. No NetBox or Nautobot."""

import pytest

from netbox_lantronix.percepxion.portconfig import SSH_OFF, parse_port_ssh

BASE = "telemetry/cm/config_record/"
CANARIES = ("Canary-community-rw", "Canary-host-key", "Canary-description")


def record(name, /, **fields):
    return {
        "name": BASE + name,
        "record_name": name,
        "field": [{"name": f"{BASE}{name}/{name}_{k}", "value": v} for k, v in fields.items()],
    }


def config(*records):
    return {
        "device_id": "dev-0001",
        "last_fetched": "2026-11-15T09:30:45Z",
        "config_record": list(records),
    }


def test_each_port_reports_its_own_ssh_port():
    raw = config(
        record("dpglo", startsshport="3001"),
        record("dpgen1", sshin="Enable", sshport="3001", sshauth="Enable"),
        record("dpgen2", sshin="Enable", sshport="22022"),
        record("dpgen16", sshin="Enable", sshport="3016"),
    )
    assert parse_port_ssh(raw) == {1: 3001, 2: 22022, 16: 3016}


def test_ssh_turned_off_on_a_port_is_reported_as_off():
    raw = config(record("dpgen3", sshin="Disable", sshport="3003"))
    assert parse_port_ssh(raw) == {3: SSH_OFF}


@pytest.mark.parametrize(
    "fields",
    [
        {"sshin": "Enable"},  # no port
        {"sshport": "3004"},  # no enable state
        {"sshin": "Enable", "sshport": "0"},
        {"sshin": "Enable", "sshport": "65536"},
        {"sshin": "Enable", "sshport": "3004; rm -rf /"},
        {"sshin": "Enable", "sshport": "٣٠٠٤"},
        {"sshin": "Enable", "sshport": None},
        {"sshin": "maybe", "sshport": "3004"},
        {"sshin": ["Enable"], "sshport": {"x": 1}},
    ],
)
def test_a_port_with_missing_or_malformed_settings_is_left_out(fields):
    assert parse_port_ssh(config(record("dpgen4", **fields))) == {}


@pytest.mark.parametrize(
    "name", ["dpgen0", "dpgen1000", "dpgen1x", "usba", "dpglo", "xdpgen1", "servssh"]
)
def test_only_device_port_records_are_read(name):
    assert parse_port_ssh(config(record(name, sshin="Enable", sshport="3001"))) == {}


@pytest.mark.parametrize(
    "raw", [None, [], "text", {}, {"config_record": None}, {"config_record": "x"}]
)
def test_an_unexpected_response_gives_no_ports(raw):
    assert parse_port_ssh(raw) == {}


def test_malformed_records_and_fields_are_skipped():
    raw = config(
        "junk",
        {"name": BASE + "dpgen1", "field": "junk"},
        {"name": BASE + "dpgen2", "field": ["junk", {"name": None}]},
    )
    assert parse_port_ssh(raw) == {}


def test_nothing_else_in_the_configuration_is_returned():
    raw = config(
        record("snmp", readwrite=CANARIES[0], readonly="public"),
        record("servssh", opthostkey=CANARIES[1], port="22"),
        record("dpgen1", sshin="Enable", sshport="3001", name=CANARIES[2], banner=CANARIES[2]),
    )
    result = parse_port_ssh(raw)
    assert result == {1: 3001}
    assert not any(c in repr(result) for c in CANARIES)
