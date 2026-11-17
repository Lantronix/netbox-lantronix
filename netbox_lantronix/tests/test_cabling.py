# Shared code: this file is the same in netbox-lantronix and nautobot-app-ssot-lantronix.
# scripts/check_shared.py fails CI when the copies differ, so change both.
"""Cable decisions from SLC port reports (no database)."""

from netbox_lantronix.percepxion.cabling import (
    OTHER,
    ConsolePortRef,
    Create,
    Delete,
    ObservedPort,
    Target,
    plan_cables,
)


def port(n, state="Disconnected", attached=False, serial="", hostname=""):
    return ObservedPort("slc", f"p{n}", state, attached, serial, hostname, f"SLC Port-{n:02d}")


CISCO = Target("cisco", "edge-switch-01", "FOC1234X0AB", (ConsolePortRef("cisco-con", "Console"),))
EMG = Target("emg", "emg-01", "0080A3EMG", (ConsolePortRef("emg-con", "Console", "p5"),))


def test_recabled_booth_moves_the_cisco_and_drops_disconnected_cables():
    """The 2026-11-17 re-cabling: Cisco moved from port 14 to 3, EMG stays on 5 unidentified,
    the old seed cables on ports 1, 3 and 4 point at devices no longer there."""
    cisco = Target(
        "cisco", "edge-switch-01", "FOC1234X0AB", (ConsolePortRef("cisco-con", "Console", "p14"),)
    )
    observed = [
        port(1),
        port(3, "Managed", True, "FOC1234X0AB", "edge-switch-01"),
        port(4),
        port(5, "Unidentified"),
        port(14),
    ]
    cables = {"p1": "dvt1-con", "p3": "firewall-con", "p4": "switch-con", "p5": "emg-con",
              "p14": "cisco-con"}
    plan = plan_cables(observed, cables, [cisco, EMG])
    assert {(d.port, d.reason) for d in plan.deletes} == {
        ("p1", "disconnected"),
        ("p3", "replaced"),
        ("p4", "disconnected"),
        ("p14", "moved"),
    }
    assert plan.creates == [
        Create("p3", "cisco", "cisco-con", "SLC Port-03", "edge-switch-01", "serial")
    ]
    assert "p5" not in {d.port for d in plan.deletes}  # unidentified, not disconnected


def test_correct_cable_is_left_alone():
    cisco = Target(
        "cisco", "edge-switch-01", "FOC1234X0AB", (ConsolePortRef("cisco-con", "Console", "p3"),)
    )
    plan = plan_cables(
        [port(3, "Managed", True, "FOC1234X0AB", "edge-switch-01")], {"p3": "cisco-con"}, [cisco]
    )
    assert plan.is_empty() and plan.unchanged == 1 and not plan.findings


def test_only_disconnected_deletes():
    for state in ("Identifying", "Unidentified", "Authenticating", "Unknown", ""):
        plan = plan_cables([port(5, state)], {"p5": "emg-con"}, [EMG])
        assert plan.deletes == [], state


def test_attached_but_not_yet_named_changes_nothing():
    plan = plan_cables([port(3, "Authenticating", True)], {"p3": "firewall-con"}, [CISCO])
    assert plan.is_empty() and not plan.findings


def test_hostname_match_when_serial_unknown():
    plan = plan_cables([port(3, "Managed", True, "", "EDGE-SWITCH-01")], {}, [CISCO])
    assert plan.creates[0].matched_on == "hostname"


def test_serial_wins_over_hostname():
    renamed = Target("cisco2", "renamed", "FOC1234X0AB", (ConsolePortRef("c2", "Console"),))
    same_name = Target("other", "edge-switch-01", "XYZ", (ConsolePortRef("other-con", "Console"),))
    plan = plan_cables(
        [port(3, "Managed", True, "FOC1234X0AB", "edge-switch-01")], {}, [renamed, same_name]
    )
    assert plan.creates[0].target == "cisco2" and plan.creates[0].matched_on == "serial"


def test_unmatched_and_ambiguous_are_findings_only():
    plan = plan_cables([port(3, "Managed", True, "NOPE", "nobody")], {"p3": "x"}, [CISCO])
    assert plan.is_empty() and [f.kind for f in plan.findings] == ["attached_unmatched"]
    twins = [CISCO, Target("c2", "b", "FOC1234X0AB", (ConsolePortRef("c2c", "Console"),))]
    plan = plan_cables([port(3, "Managed", True, "FOC1234X0AB")], {}, twins)
    assert plan.is_empty() and [f.kind for f in plan.findings] == ["attached_ambiguous"]


def test_console_port_choice():
    two = Target("d", "d", "S1", (ConsolePortRef("a", "con1"), ConsolePortRef("b", "con2")))
    plan = plan_cables([port(3, "Managed", True, "S1")], {}, [two])
    assert plan.is_empty() and plan.findings[0].kind == "console_port_unclear"
    named = Target("d", "d", "S1", (ConsolePortRef("a", "aux"), ConsolePortRef("b", "Console")))
    assert plan_cables([port(3, "Managed", True, "S1")], {}, [named]).creates[0].console_port == "b"
    none = Target("d", "d", "S1", ())
    assert plan_cables([port(3, "Managed", True, "S1")], {}, [none]).findings[0].kind == (
        "console_port_unclear"
    )


def test_console_port_cabled_outside_the_sync_is_never_touched():
    elsewhere = Target("d", "d", "S1", (ConsolePortRef("dc", "Console", "other-slc-port"),))
    plan = plan_cables([port(3, "Managed", True, "S1")], {}, [elsewhere])
    assert plan.is_empty() and plan.findings[0].kind == "console_port_cabled_elsewhere"
    panel = Target("d", "d", "S1", (ConsolePortRef("dc", "Console", OTHER),))
    assert plan_cables([port(3, "Managed", True, "S1")], {}, [panel]).is_empty()


def test_patch_panel_cable_on_slc_port_is_never_deleted():
    plan = plan_cables([port(7)], {"p7": OTHER}, [])
    assert plan.is_empty() and plan.findings[0].kind == "port_cabled_elsewhere"


def test_same_device_on_two_ports_creates_one_cable():
    plan = plan_cables(
        [port(3, "Managed", True, "FOC1234X0AB"), port(9, "Managed", True, "FOC1234X0AB")],
        {},
        [CISCO],
    )
    assert len(plan.creates) == 1
    assert [f.kind for f in plan.findings] == ["duplicate_attachment"]


def test_moved_and_disconnected_delete_once():
    cisco = Target("cisco", "e", "S1", (ConsolePortRef("cc", "Console", "p14"),))
    plan = plan_cables(
        [port(14), port(3, "Managed", True, "S1")], {"p14": "cc"}, [cisco]
    )
    assert plan.deletes == [Delete("p14", "moved", "SLC Port-03")]


def test_hostname_only_match_never_removes_a_cable():
    """A device reports its own hostname, so a hostname match alone may only fill free ports."""
    moved = Target("cisco", "edge-switch-01", "", (ConsolePortRef("cc", "Console", "p14"),))
    observed = [port(14), port(3, "Managed", True, "", "edge-switch-01")]
    plan = plan_cables(observed, {"p14": "cc", "p3": "firewall-con"}, [moved])
    assert plan.creates == []
    assert {d.port for d in plan.deletes} == {"p14"}  # only because port 14 is Disconnected
    assert "hostname_only_match" in [f.kind for f in plan.findings]


def test_a_device_claim_never_pulls_a_cable_off_a_port_still_in_use():
    """The serial is the attached device's own claim: a move needs the old port Disconnected."""
    cisco = Target("cisco", "e", "S1", (ConsolePortRef("cc", "Console", "p14"),))
    observed = [port(14, "Authenticating", True), port(3, "Managed", True, "S1")]
    plan = plan_cables(observed, {"p14": "cc"}, [cisco])
    assert plan.is_empty()
    assert [f.kind for f in plan.findings] == ["still_reported_elsewhere"]
