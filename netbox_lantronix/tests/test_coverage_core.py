# Shared code: this file is the same in netbox-lantronix and nautobot-app-ssot-lantronix.
# scripts/check_shared.py fails CI when the copies differ, so change both.
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from netbox_lantronix.coverage import core
from netbox_lantronix.coverage.core import (
    ConsoleLink,
    Scope,
    Slc,
    Target,
    console_path,
    coverage_report,
)
from netbox_lantronix.tests.coverage_fixtures import load_targets

FIX = Path(__file__).resolve().parent / "fixtures" / "coverage"
NOW = datetime(2026, 10, 30, 18, 0, tzinfo=UTC)


def slc(**kw):
    base = dict(id="1", name="SLC", synced=True, host="192.0.2.10", host_source="primary_ip")
    base.update(kw)
    return Slc(**base)


def target(*links, **kw):
    return Target(id="9", name=kw.pop("name", "dev"), links=list(links), **kw)


def cabled(port="Port-14", state="Managed", s=None):
    return ConsoleLink(
        "Console",
        cabled=True,
        peer_is_console_server_port=True,
        slc=s or slc(),
        slc_port=port,
        port_state=state,
    )


def test_covered_path_has_ssh_port():
    r = console_path(target(cabled()))
    assert r["covered"] and r["reasons"] == []
    assert (r["slc_port_number"], r["ssh_port"], r["slc_host_source"]) == (14, 3014, "primary_ip")


def test_custom_base_port():
    assert console_path(target(cabled(s=slc(ssh_base_port=4000))))["ssh_port"] == 4014


@pytest.mark.parametrize(
    "link,reason",
    [
        (None, core.NO_CONSOLE_PORT),
        (ConsoleLink("Console"), core.NO_CONSOLE_CABLE),
        (ConsoleLink("Console", cabled=True), core.PEER_NOT_CONSOLE_SERVER),
        (cabled(state=None), core.PORT_STATE_UNKNOWN),
        (cabled(state="Unidentified"), core.PORT_STATE_NOT_READY),
        (cabled(port="ttyS1"), core.PORT_NAME_UNPARSED),
        (cabled(s=slc(synced=False)), core.SLC_NOT_SYNCED),
        (cabled(s=slc(host=None)), core.NO_SLC_HOST),
    ],
)
def test_reasons(link, reason):
    r = console_path(target(link) if link else target())
    assert not r["covered"] and reason in r["reasons"]


def test_identified_is_ready_by_default():
    assert console_path(target(cabled(state="Identified")))["covered"]


def test_ready_states_override():
    assert console_path(target(cabled(state="Connected")), ["Connected"])["covered"]


def test_best_console_port_wins():
    r = console_path(
        target(ConsoleLink("Console 0"), cabled(state="Unidentified"), cabled(port="Port-02"))
    )
    assert r["covered"] and r["slc_port"] == "Port-02"


def test_furthest_failure_explains_device():
    r = console_path(target(ConsoleLink("A"), cabled(state="Unidentified")))
    assert r["reasons"] == [core.PORT_STATE_NOT_READY]


def test_scope_filters():
    t = target(cabled(), site="a", role="sw", tags=["x"])
    assert Scope().matches(t)
    assert not Scope(site="b").matches(t)
    assert not Scope(roles=["fw"]).matches(t)
    assert Scope(tags=["x", "y"]).matches(t) and not Scope(tags=["y"]).matches(t)
    assert not Scope(devices=["other"]).matches(t)
    assert not Scope(exclude_roles=["sw"]).matches(t)
    assert not Scope().matches(target(cabled(), active=False))


def sample_report(**scope):
    scope.setdefault("exclude_roles", ["oob-console-server"])
    return coverage_report("netbox", load_targets(FIX / "targets.json"), Scope(**scope), now=NOW)


def test_lab_report_shape():
    rep = sample_report()
    assert rep["schema"] == "aoob.console-coverage/v1" and rep["result"] == "fail"
    assert rep["summary"] == {"in_scope": 5, "covered": 1, "uncovered": 4}
    names = [d["device"] for d in rep["devices"]]
    assert names[-1] == "edge-switch-01"  # uncovered first
    by = {d["device"]: d for d in rep["devices"]}
    assert by["nas-01"]["reasons"] == [core.NO_CONSOLE_PORT]
    assert by["access-sw-01"]["reasons"] == [core.PORT_STATE_UNKNOWN]


def test_preflight_scope_passes_for_covered_device():
    rep = sample_report(devices=["edge-switch-01"])
    assert rep["result"] == "pass" and rep["summary"]["in_scope"] == 1


def test_empty_scope_passes():
    rep = sample_report(site="no-such-site")
    assert rep["result"] == "pass" and rep["summary"]["in_scope"] == 0


def test_unknown_named_device_fails():
    rep = sample_report(devices=["nope"])
    assert rep["result"] == "fail" and rep["not_found"] == ["nope"]


def test_golden_fixture_matches():
    """fixtures/coverage/coverage_report.json is the report contract consumers build against."""
    golden = json.loads((FIX / "coverage_report.json").read_text())
    assert sample_report() == golden


def test_record_fields_match_contract():
    keys = set(sample_report()["devices"][0])
    assert keys == {
        "device",
        "device_id",
        "site",
        "role",
        "platform",
        "console_port",
        "slc",
        "slc_id",
        "slc_port",
        "slc_port_number",
        "slc_host",
        "slc_host_source",
        "ssh_base_port",
        "ssh_port",
        "slc_synced",
        "port_state",
        "covered",
        "reasons",
    }


def test_summary_lines():
    lines = core.summary_lines(sample_report())
    assert lines[0].startswith("FAIL: 1/5")
    assert any(ln.startswith("ok   edge-switch-01") and "3014" in ln for ln in lines)


def test_split_list():
    assert core.split_list("a, b\nc,,") == ["a", "b", "c"]
    assert core.split_list(None) == []


def test_named_device_not_found_fails_the_gate():
    targets = [core.Target(id="1", name="edge-switch-01", site="lab", role="switch", active=True)]
    rep = core.coverage_report("netbox", targets, core.Scope(devices=["edge-switch-01", "typo"]))
    assert rep["not_found"] == ["typo"]
    assert rep["result"] == "fail"
    assert "FAIL typo: not_found" in core.summary_lines(rep)


def test_no_named_devices_means_empty_not_found():
    rep = core.coverage_report("netbox", [], core.Scope())
    assert rep["not_found"] == [] and rep["result"] == "pass"
