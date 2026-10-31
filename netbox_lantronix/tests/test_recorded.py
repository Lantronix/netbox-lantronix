"""Replays scrubbed sandbox responses (tests/fixtures, recorded by dev/record_fixtures.py)
through the client and resolver, so parsing is checked against real Percepxion shapes."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
import requests

from netbox_lantronix.percepxion.client import PercepxionClient
from netbox_lantronix.percepxion.resolver import parse_sysconfig, sku_from_sysconfig

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://px.example.test"


class Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def recorded():
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    queues: dict[str, list[dict]] = {}
    for item in manifest:
        page = json.loads((FIXTURES / item["file"]).read_text())
        queues.setdefault(item["path"], []).append(page)
    sent: list[tuple[str, dict]] = []

    def replay(session, method, url, json=None, **kwargs):
        path = url[len(URL):]
        sent.append((path, json))
        return Response(queues[path].pop(0)["response"])

    with mock.patch.object(requests.Session, "request", autospec=True, side_effect=replay):
        yield queues, sent


def test_recorded_sandbox_responses_parse(recorded):
    queues, sent = recorded
    port_pages = [p["response"] for p in queues["/v3/port/search"]]
    raw_devices = queues["/v3/device/search"][0]["response"]["search_results"]
    c = PercepxionClient(URL, "user", "pw")
    c.login()

    devices = list(c.iter_devices())
    assert len(devices) == sum(d["dev_type"] == "ltrx_itm" for d in raw_devices) > 0
    assert all(d.model and d.rj45_ports and d.org_ids for d in devices)

    ports = list(c.iter_ports())
    assert len(ports) == sum(len(p["result"]) for p in port_pages) == port_pages[0]["total"]
    # Pagination follows sort_last into search_after.
    cursors = [body.get("search_after") for path, body in sent if path == "/v3/port/search"]
    assert cursors[1:] == [p["sort_last"] for p in port_pages[:-1]]
    assert any(p.facts.get("model") for p in ports)

    jobs = c.latest_sysconfig_jobs()
    assert jobs
    device_id, (job_id, _) = next(iter(jobs.items()))
    text = c.cli_output(job_id, device_id)
    assert sku_from_sysconfig(parse_sysconfig(text)) == "lantronix-slc9016-16u-16e"
