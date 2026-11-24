# Shared code: this file is the same in netbox-lantronix and nautobot-app-ssot-lantronix.
# scripts/check_shared.py fails CI when the copies differ, so change both.
"""Change watch fingerprints and change detection."""

import json
from dataclasses import replace
from pathlib import Path

from netbox_lantronix.percepxion.telemetry import parse_port_states
from netbox_lantronix.percepxion.watch import changed, fingerprint

FIXTURE = Path(__file__).parent / "fixtures" / "stat_view_1.json"


def states():
    return parse_port_states(json.loads(FIXTURE.read_text()))


def test_fingerprint_is_stable_and_json_safe():
    fp = fingerprint(states())
    assert fp == fingerprint(states())
    assert json.loads(json.dumps(fp)) == fp
    assert sorted(fp, key=int) == [str(n) for n in range(1, 17)]
    assert all(len(v) == 16 for v in fp.values())


def test_last_seen_and_ip_changes_are_not_changes():
    s = states()
    moved = {n: replace(p, last_seen="2030-01-01T00:00:00Z", ip="192.0.2.9") for n, p in s.items()}
    assert fingerprint(moved) == fingerprint(s)


def test_unplugging_a_port_is_a_change():
    s = states()
    unplugged = dict(s)
    unplugged[14] = replace(s[14], attached=False, state="Disconnected", hostname="")
    before = {"SLC-A": fingerprint(s), "SLC-B": fingerprint(s)}
    after = {"SLC-A": fingerprint(unplugged), "SLC-B": fingerprint(s)}
    assert changed(before, after) == {"SLC-A"}


def test_state_and_identity_changes_are_changes():
    s = states()
    for field, value in (("state", "Identifying"), ("serial", "OTHER"), ("hostname", "new-sw")):
        edited = dict(s)
        edited[14] = replace(s[14], **{field: value})
        assert changed({"D": fingerprint(s)}, {"D": fingerprint(edited)}) == {"D"}, field


def test_ports_appearing_or_going_are_changes():
    s = states()
    fewer = {n: p for n, p in s.items() if n != 16}
    assert changed({"D": fingerprint(s)}, {"D": fingerprint(fewer)}) == {"D"}
    assert changed({"D": fingerprint(fewer)}, {"D": fingerprint(s)}) == {"D"}


def test_new_devices_are_a_baseline_and_gone_devices_are_ignored():
    fp = fingerprint(states())
    assert changed({}, {"NEW": fp}) == set()
    assert changed({"GONE": fp}, {}) == set()
