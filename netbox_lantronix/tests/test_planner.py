"""Planner decisions. No database."""

from netbox_lantronix.percepxion.records import PxDevice, PxPort
from netbox_lantronix.sync.planner import NbDevice, NbPort, SourceContext, plan

ORG = "org-0001"
TYPES = frozenset(
    {
        "lantronix-slc9016-16e",
        "lantronix-slc9016-16u-16e",
        "lantronix-slc9032",
        "lantronix-emg851110s",
    }
)


RULE_9032 = {("SLC9032", 32, 0): "lantronix-slc9032"}


def ctx(**kw):
    # Every SLC 9000 signature also matches a -DC type, so creates need a rule or override.
    kw.setdefault("rules", RULE_9032)
    kw.setdefault("site", 5)
    return SourceContext(pk=1, organization_id=ORG, available_types=TYPES, **kw)


def px(
    pxid="dev-0001",
    name="SLC-A",
    serial="000000000001",
    model="SLC9032",
    rj45=32,
    usb=0,
    orgs=(ORG,),
):
    return PxDevice(pxid, name, serial, model, rj45, usb, frozenset(orgs), "192.0.2.10", "SLC")


def nb(
    pk=10,
    name="SLC-A",
    serial="000000000001",
    manufacturer="lantronix",
    platform="lantronix_itmgos",
    link_source=1,
    link_pxid="dev-0001",
    link_status="active",
    cf_pxid="dev-0001",
    site=5,
):
    return NbDevice(
        pk,
        name,
        serial,
        manufacturer,
        platform,
        link_source,
        link_pxid,
        link_status,
        cf_pxid,
        site,
    )


def port(number, name=None, hostname="", pxid="dev-0001", **facts):
    return PxPort(pxid, number, name or f"Port-{number:02d}", hostname, facts, frozenset({ORG}))


def kinds(result):
    return sorted(f.kind for f in result.findings)


# -- devices ---------------------------------------------------------------------------


def test_create_on_empty_netbox():
    r = plan(ctx(), [px()], [], [], [])
    assert [(c.name, c.type_slug, c.type_source) for c in r.creates] == [
        ("SLC-A", "lantronix-slc9032", "rule")
    ]
    assert r.seen == ["dev-0001"]


def test_port_counts_alone_never_resolve_slc9000():
    r = plan(ctx(rules={}), [px()], [], [], [])
    assert kinds(r) == ["ambiguous_model"] and "lantronix-slc9032-dc" in r.findings[0].detail
    assert r.seen == []


def test_idempotent_when_nothing_changed():
    r = plan(ctx(), [px()], [], [nb()], [])
    assert r.is_empty() and r.unchanged == 1 and not r.findings


def test_linked_rename_updates_name_not_duplicate():
    r = plan(ctx(), [px(name="SLC-B")], [], [nb()], [])
    assert not r.creates
    assert r.updates[0].changes == {"name": "SLC-B"}


def test_linked_rename_into_taken_name_is_conflict():
    other = nb(pk=11, name="SLC-B", serial="", link_source=None, link_pxid=None, cf_pxid=None)
    r = plan(ctx(), [px(name="SLC-B")], [], [nb(), other], [])
    assert kinds(r) == ["conflict"] and not r.updates


def test_match_by_serial_adopts_unlinked_device():
    d = nb(name="old-name", link_source=None, link_pxid=None, link_status=None, cf_pxid=None)
    r = plan(ctx(), [px()], [], [d], [])
    u = r.updates[0]
    assert u.link == "adopt" and u.changes == {"name": "SLC-A", "cf_pxid": "dev-0001"}


def test_serial_match_linked_to_other_source_is_conflict():
    d = nb(link_source=2)
    r = plan(ctx(), [px()], [], [d], [])
    assert kinds(r) == ["conflict"] and not r.updates and not r.creates


def test_same_name_cisco_is_conflict_and_untouched():
    cisco = nb(
        serial="",
        manufacturer="cisco",
        platform="ios",
        link_source=None,
        link_pxid=None,
        cf_pxid=None,
    )
    r = plan(ctx(), [px()], [], [cisco], [])
    assert kinds(r) == ["conflict"] and r.is_empty()


def test_same_name_serial_mismatch_is_conflict():
    d = nb(serial="999999999999", link_source=None, link_pxid=None, cf_pxid=None)
    r = plan(ctx(), [px()], [], [d], [])
    assert kinds(r) == ["conflict"] and r.is_empty()


def test_same_name_other_platform_is_conflict():
    d = nb(serial="", platform="cisco_ios", link_source=None, link_pxid=None, cf_pxid=None)
    r = plan(ctx(), [px()], [], [d], [])
    assert kinds(r) == ["conflict"]


def test_same_name_empty_serial_needs_approval():
    d = nb(serial="", platform=None, link_source=None, link_pxid=None, cf_pxid=None)
    r = plan(ctx(), [px()], [], [d], [])
    assert r.pending == [(10, "dev-0001")] and kinds(r) == ["adoption_pending"]
    assert not r.updates and not r.creates


def test_pending_link_is_not_synced():
    r = plan(ctx(), [px()], [], [nb(link_status="pending")], [])
    assert kinds(r) == ["adoption_pending"] and r.is_empty()


def test_never_changes_type_or_site_fields():
    r = plan(ctx(), [px(serial="000000000002")], [], [nb(platform=None)], [])
    assert set(r.updates[0].changes) <= {"name", "serial", "platform", "cf_pxid"}


def test_ambiguous_model_reported_with_candidates():
    r = plan(ctx(), [px(model="SLC9016", rj45=16, usb=0)], [], [], [])
    assert not r.creates and kinds(r) == ["ambiguous_model"]
    assert "lantronix-slc9016-16e" in r.findings[0].detail
    assert "show sysconfig" in r.findings[0].detail


def test_type_precedence_override_sysconfig_rule():
    dev = px(model="SLC9032", rj45=16, usb=16)
    rule = {("SLC9032", 16, 16): "lantronix-slc9016-16u-16e"}
    # Rule only.
    r = plan(ctx(rules=rule), [dev], [], [], [])
    assert (r.creates[0].type_slug, r.creates[0].type_source) == (
        "lantronix-slc9016-16u-16e",
        "rule",
    )
    # Sysconfig beats rule.
    r = plan(
        ctx(rules={("SLC9032", 16, 16): "lantronix-slc9032"}),
        [dev],
        [],
        [],
        [],
        sysconfig=lambda pxid: "lantronix-slc9016-16u-16e",
    )
    assert r.creates[0].type_source == "sysconfig"
    # Override beats sysconfig.
    r = plan(
        ctx(overrides={"dev-0001": "lantronix-slc9032"}),
        [dev],
        [],
        [],
        [],
        sysconfig=lambda pxid: "lantronix-slc9016-16u-16e",
    )
    assert (r.creates[0].type_slug, r.creates[0].type_source) == ("lantronix-slc9032", "override")


def test_sysconfig_not_fetched_when_unneeded():
    calls = []
    plan(
        ctx(overrides={"dev-0001": "lantronix-slc9032"}),
        [px()],
        [],
        [],
        [],
        sysconfig=lambda pxid: calls.append(pxid),
    )
    plan(
        ctx(),
        [px(name="EMG-A", model="EMG851101", rj45=0)],
        [],
        [],
        [],
        sysconfig=lambda pxid: calls.append(pxid),
    )
    plan(ctx(), [px()], [], [nb()], [], sysconfig=lambda pxid: calls.append(pxid))
    assert calls == []


def test_type_not_in_netbox_is_reported():
    r = plan(
        ctx(overrides={"dev-0001": "lantronix-slc9048"}), [px(model="SLC9048", rj45=48)], [], [], []
    )
    assert kinds(r) == ["no_device_type"] and not r.creates


def test_emg_maps_through_sku_rule():
    r = plan(ctx(), [px(name="EMG-A", model="EMG851101", rj45=0)], [], [], [])
    assert r.creates[0].type_slug == "lantronix-emg851110s"


def test_out_of_org_dropped():
    r = plan(ctx(), [px(orgs=("org-9999",))], [], [], [])
    assert kinds(r) == ["out_of_org"] and r.is_empty()


def test_rejected_and_duplicate_names():
    devs = [
        px(name="bad name<x>"),
        px("dev-0002", "SLC-D", "000000000002"),
        px("dev-0003", "SLC-D", "000000000003"),
    ]
    r = plan(ctx(), devs, [], [], [])
    assert kinds(r) == ["duplicate_name", "duplicate_name", "rejected_name"] and not r.creates


def test_missing_linked_device_reported_not_deleted():
    r = plan(ctx(), [], [], [nb()], [])
    assert kinds(r) == ["missing"] and r.is_empty()


# -- ports -----------------------------------------------------------------------------


def nbp(name, label="", description="", device_pk=10):
    return NbPort(device_pk, name, label, description)


def test_port_label_from_device_name_and_hostname_description():
    ports = [port(1, name="SLC-B"), port(2, hostname="Switch"), port(3)]
    current = [nbp("Port-01"), nbp("Port-02"), nbp("Port-03")]
    r = plan(ctx(), [px()], ports, [nb()], current)
    assert r.port_changes[(10, "Port-01")].label == "SLC-B"
    assert r.port_changes[(10, "Port-02")].description == "Switch"
    assert (10, "Port-03") not in r.port_changes and r.ports_unchanged == 1


def test_default_name_clears_hand_edited_label():
    r = plan(ctx(), [px()], [port(4)], [nb()], [nbp("Port-04", label="hand-edit")])
    assert r.port_changes[(10, "Port-04")].label == ""


def test_port_text_is_sanitized():
    r = plan(
        ctx(), [px()], [port(5, name="evil‮Label", hostname="h\x00ost")], [nb()], [nbp("Port-05")]
    )
    change = r.port_changes[(10, "Port-05")]
    assert "‮" not in change.label and "\x00" not in change.description


def test_ports_of_new_device_planned_by_pxid():
    r = plan(ctx(), [px()], [port(1, name="SLC-B"), port(2)], [], [])
    assert list(r.new_device_ports["dev-0001"]) == ["Port-01"]
    assert r.new_device_ports["dev-0001"]["Port-01"].label == "SLC-B"


def test_ports_for_unplanned_devices_ignored():
    r = plan(ctx(), [], [port(1, name="x", pxid="dev-9999")], [], [])
    assert not r.port_changes and not r.new_device_ports


def test_missing_netbox_port_skipped():
    r = plan(ctx(), [px()], [port(40, name="SLC-B")], [nb()], [])
    assert not r.port_changes


# -- facts -----------------------------------------------------------------------------


def test_facts_only_when_reported():
    ports = [
        port(
            2,
            hostname="Switch",
            make="cisco",
            model="WS-C3560CG-8TC-S",
            os="ios",
            os_version="15.2(2)E10",
            serial="",
        ),
        port(3, make="", model="", os="", os_version="", serial=""),
    ]
    r = plan(ctx(), [px()], ports, [nb()], [nbp("Port-02"), nbp("Port-03")])
    assert r.facts == {
        "dev-0001": {
            "Port-02": {
                "make": "cisco",
                "model": "WS-C3560CG-8TC-S",
                "os": "ios",
                "os_version": "15.2(2)E10",
                "serial": "",
            }
        }
    }


def test_telemetry_state_planned_for_attached_and_unplugged_ports():
    ports = [
        port(
            14,
            hostname="switch-01",
            make="Cisco",
            model="WS-C3560CG-8TC-S",
            os="IOS",
            os_version="15.2(2)E10",
            serial="SER00001",
            state="Managed",
            attached="yes",
        ),
        port(6, state="Disconnected", attached="no"),
    ]
    r = plan(ctx(), [px()], ports, [nb()], [nbp("Port-14"), nbp("Port-06")])
    facts = r.facts["dev-0001"]
    assert facts["Port-14"]["attached"] is True and facts["Port-14"]["state"] == "Managed"
    assert facts["Port-06"] == {"state": "Disconnected", "attached": False}
    assert r.port_changes[(10, "Port-14")].description == "switch-01"


def test_long_state_cut_to_field_length():
    r = plan(ctx(), [px()], [port(6, state="X" * 500, attached="no")], [nb()], [nbp("Port-06")])
    assert len(r.facts["dev-0001"]["Port-06"]["state"]) <= 30


def test_unsafe_serial_or_device_id_rejected_not_cleaned():
    devs = [px(serial="0001<script>"), px("dev-0002\x00x", "SLC-E", "000000000005")]
    r = plan(ctx(), devs, [], [], [])
    assert kinds(r) == ["rejected_name", "rejected_name"] and r.is_empty()
    assert all(f.pxid == "" for f in r.findings)


def test_serial_whitespace_trimmed_for_matching():
    d = nb(name="old", link_source=None, link_pxid=None, link_status=None, cf_pxid=None)
    r = plan(ctx(), [px(serial=" 000000000001 ")], [], [d], [])
    assert r.updates[0].link == "adopt"


def test_serial_match_in_other_site_needs_approval():
    d = nb(name="old", link_source=None, link_pxid=None, link_status=None, cf_pxid=None, site=9)
    r = plan(ctx(), [px()], [], [d], [])
    assert r.pending == [(10, "dev-0001")] and kinds(r) == ["adoption_pending"] and not r.updates


def test_linked_device_in_other_site_still_synced():
    r = plan(ctx(), [px(name="SLC-B")], [], [nb(site=9)], [])
    assert r.updates[0].changes == {"name": "SLC-B"}


def test_ambiguous_finding_carries_signature():
    r = plan(ctx(rules={}), [px(model="SLC9016", rj45=16, usb=0)], [], [], [])
    assert r.findings[0].signature == ("SLC9016", 16, 0)


def test_mgmt_ip_recorded_and_validated():
    good = px()
    bad = px("dev-0002", "SLC-C", "000000000003")
    bad = type(bad)(**{**bad.__dict__, "ip": "not-an-ip"})
    r = plan(ctx(), [good, bad], [], [nb()], [])
    assert r.mgmt_ips == {"dev-0001": "192.0.2.10"}


def test_facts_sanitized():
    ports = [port(2, make="cis\x1bco", model="C9300‮-48P", os="ios-xe\n", serial="X" * 500)]
    r = plan(ctx(), [px()], ports, [nb()], [nbp("Port-02")])
    facts = r.facts["dev-0001"]["Port-02"]
    assert facts["make"] == "cis co" and facts["model"] == "C9300-48P"  # control char -> space
    assert facts["os"] == "ios-xe" and len(facts["serial"]) <= 100
