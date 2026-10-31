"""Telemetry view parsing and merge into port-search records (scrubbed sandbox fixture)."""

import json
from pathlib import Path

from netbox_lantronix.percepxion.records import PxPort
from netbox_lantronix.percepxion.telemetry import PxPortState, merge_state, parse_port_states

FIXTURE = Path(__file__).parent / "fixtures" / "stat_view_1.json"
LEFTOVER = (2, 6, 8, 9, 10, 15)  # an earlier cable left the old Cisco's facts on these ports


def states():
    return parse_port_states(json.loads(FIXTURE.read_text()))


def test_every_port_parsed():
    assert sorted(states()) == list(range(1, 17))


def test_managed_port_carries_device_identity():
    s = states()[14]
    assert s == PxPortState(
        number=14,
        state="Managed",
        attached=True,
        make="Cisco",
        os="IOS",
        os_version="15.2(2)E10",
        model="WS-C3560CG-8TC-S",
        serial=s.serial,
        hostname="switch-01",
        ip="192.0.2.57",
        last_seen=s.last_seen,
    )
    assert s.serial and s.last_seen


def test_leftover_ports_show_nothing_attached():
    s = states()
    for n in LEFTOVER:
        assert s[n].attached is False
        assert s[n].state == "Disconnected"
        assert (s[n].model, s[n].hostname, s[n].serial) == ("", "", "")


def test_identifying_port_has_state_only():
    s = states()[3]
    assert (s.state, s.attached, s.model) == ("Identifying", False, "")


def test_config_text_never_leaves_the_parser():
    assert "FIXTURE-SECRET-MARKER" in FIXTURE.read_text()
    assert "FIXTURE-SECRET-MARKER" not in repr(states())


def test_bad_shapes_return_nothing():
    assert parse_port_states(None) == {}
    assert parse_port_states({"status_record": [None, {"name": "telemetry/cm/x"}]}) == {}
    one = parse_port_states(
        {"status_record": [{"name": "telemetry/port:4/status_record/dp_info", "items": [None]}]}
    )
    assert one == {4: PxPortState(4, "", False)}


def port(number, hostname="old-host", **facts):
    return PxPort("dev-0001", number, f"Port-{number:02d}", hostname, facts or {"model": "OLD"})


def test_merge_attached_replaces_port_search_data():
    p = merge_state(port(14), states()[14])
    assert p.hostname == "switch-01"
    assert p.facts["model"] == "WS-C3560CG-8TC-S"
    assert (p.facts["state"], p.facts["attached"]) == ("Managed", "yes")


def test_merge_not_attached_clears_hostname_and_keeps_only_state():
    p = merge_state(port(6), states()[6])
    assert p.hostname == ""
    assert p.facts == {"state": "Disconnected", "attached": "no"}


def test_merge_without_state_keeps_port_search_record():
    original = port(20)
    assert merge_state(original, None) is original
