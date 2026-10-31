"""PercepxionClient tests: pure pytest, no NetBox, no network (Session.request is patched)."""

from __future__ import annotations

import json
from unittest import mock

import pytest
import requests

from netbox_lantronix.percepxion import client as client_mod
from netbox_lantronix.percepxion.client import (
    ALLOWED,
    PAGE_SIZE,
    AuthError,
    ForbiddenRequest,
    PartialData,
    PercepxionClient,
    PercepxionUnavailable,
)
from netbox_lantronix.percepxion.records import PxDevice, PxPort

URL = "https://px.example.test"
USER = "sync-user@example.test"
PASSWORD = "not-a-real-password-7731"
TOKEN = "tok-SECRET-0001"
CSRF = "csrf-SECRET-0002"
SECRETS = (USER, PASSWORD, TOKEN, CSRF)

LOGIN = ("POST", "/v2/user/login")
DEVICES = ("POST", "/v3/device/search")
PORTS = ("POST", "/v3/port/search")
JOBS = ("POST", "/v1/job/jobgroup/search")
RESULTS = ("POST", "/v1/telemetry/result/search")


# -- fixtures ----------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int = 200, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeServer:
    """Routes (method, path) to a queue of responses and records every call."""

    def __init__(self):
        self.routes: dict[tuple[str, str], list] = {}
        self.calls: list[dict] = []

    def add(self, method, path, *responses):
        self.routes.setdefault((method, path), []).extend(responses)

    def __call__(self, session, method, url, **kwargs):
        path = url[len(URL) :]
        self.calls.append(
            {
                "method": method,
                "path": path,
                "json": kwargs.get("json"),
                "timeout": kwargs.get("timeout"),
                "headers": dict(session.headers),
                "verify": session.verify,
                "proxies": dict(session.proxies),
            }
        )
        queue = self.routes.get((method, path))
        if not queue:
            raise AssertionError(f"unexpected call {method} {path}")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def bodies(self, path):
        return [c["json"] for c in self.calls if c["path"] == path]


@pytest.fixture
def server():
    srv = FakeServer()
    with mock.patch.object(requests.Session, "request", autospec=True, side_effect=srv):
        yield srv


def ok_login():
    return FakeResponse(200, {"token": TOKEN, "csrf_token": CSRF, "user": {"role": "tenant_user"}})


def make_client(**kwargs):
    return PercepxionClient(URL, USER, PASSWORD, **kwargs)


def raw_device(n: int, dev_type: str = "ltrx_itm") -> dict:
    return {
        "device_id": f"dev-{n:04d}",
        "device_name": f"SLC-{n}",
        "serial_num": f"{n:012d}",
        "dev_type": dev_type,
        "tenant": [{"id": "org-0001", "name": "Org One"}],
        "attributes": {
            "model": "SLC9032",
            "num_rj45_ports": "16",
            "num_usb_ports": "16",
            "num_ports": "32",
            "product_family": "SLC9000",
            "ipaddress": f"192.0.2.{n % 250 + 1}",
        },
    }


def raw_port(n: int) -> dict:
    return {
        "port_number": str(n % 32 + 1),
        "name": f"Port-{n:03d}",
        "parent_device_id": f"dev-{n // 32:04d}",
        "tenant": [{"id": "org-0001", "name": "Org One"}],
        "network": {"hostname": f"host-{n}", "ipv4_address": "192.0.2.10"},
        "hardware": {
            "make": "Example",
            "model": "R-1",
            "os": "ExampleOS",
            "os_version": "1.0",
            "serial_number": f"9{n:011d}",
        },
    }


def port_page(start: int, count: int, total: int = 114) -> FakeResponse:
    items = [raw_port(i) for i in range(start, start + count)]
    return FakeResponse(
        200,
        {
            "total": total,
            "sort_first": [items[0]["name"]] if items else None,
            "sort_last": [items[-1]["name"]] if items else None,
            "result": items,
        },
    )


def assert_no_secrets(text: str):
    for secret in SECRETS:
        assert secret not in text


# -- allowlist ---------------------------------------------------------------------------------


def test_allowed_is_exactly_the_six_contract_pairs():
    assert set(ALLOWED) == {
        ("POST", "/v2/user/login"),
        ("POST", "/v3/device/search"),
        ("POST", "/v3/port/search"),
        ("POST", "/v1/job/jobgroup/search"),
        ("POST", "/v1/telemetry/result/search"),
        ("POST", "/v1/telemetry/stat/view"),
    }
    assert len(ALLOWED) == 6


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/v1/user/1234"),
        ("GET", "/v3/device/search"),
        ("POST", "/v1/telemetry/stat/update"),
        ("POST", "/v1/job/jobgroup"),
        ("DELETE", "/v3/device/dev-0001"),
        ("POST", "/v3/device/search/../reboot"),
    ],
)
def test_non_allowlisted_call_raises_before_io(method, path):
    c = make_client()
    with mock.patch.object(requests.Session, "request") as req:
        with pytest.raises(ForbiddenRequest):
            c._request(method, path, {})
        req.assert_not_called()


def test_every_call_goes_through_allowlisted_pairs(server):
    server.add(*LOGIN, ok_login())
    server.add(*DEVICES, FakeResponse(200, {"total_results": 1, "search_results": [raw_device(1)]}))
    list(make_client().iter_devices())
    assert all((c["method"], c["path"]) in ALLOWED for c in server.calls)


# -- construction / TLS ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url", ["http://px.example.test", "px.example.test", "", "ftp://x", "https://"]
)
def test_non_https_url_rejected(url):
    with pytest.raises(ValueError) as exc:
        PercepxionClient(url, USER, PASSWORD)
    assert_no_secrets(str(exc.value))


def test_tls_verify_default_on_and_ca_bundle_and_proxy(server):
    server.add(*LOGIN, ok_login(), ok_login(), ok_login())
    make_client().login()
    make_client(ca_bundle="/etc/ssl/px-ca.pem").login()
    make_client(verify=False, proxy="http://proxy.example.test:3128").login()
    assert server.calls[0]["verify"] is True
    assert server.calls[1]["verify"] == "/etc/ssl/px-ca.pem"
    assert server.calls[2]["verify"] is False
    assert server.calls[2]["proxies"] == {"https": "http://proxy.example.test:3128"}


# -- login -------------------------------------------------------------------------------------


def test_login_sets_session_headers_and_user_agent(server):
    server.add(*LOGIN, ok_login())
    server.add(*DEVICES, FakeResponse(200, {"total_results": 0, "search_results": []}))
    c = make_client()
    c.login()
    list(c.iter_devices())
    login_call, search_call = server.calls
    assert login_call["json"] == {"username": USER, "password": PASSWORD}
    assert login_call["headers"]["User-Agent"] == f"netbox-lantronix/{client_mod.CLIENT_VERSION}"
    assert login_call["headers"]["Accept"] == "application/json"
    assert login_call["headers"]["Content-Type"] == "application/json"
    assert login_call["timeout"] == (30, 60)
    assert "x-mystq-token" not in login_call["headers"]
    assert search_call["headers"]["x-mystq-token"] == TOKEN
    assert search_call["headers"]["x-csrf-token"] == CSRF
    assert search_call["headers"]["User-Agent"] == f"netbox-lantronix/{client_mod.CLIENT_VERSION}"
    assert search_call["timeout"] == (30, 60)


@pytest.mark.parametrize("status", [400, 401, 403])
def test_login_rejected_raises_auth_error(server, status):
    server.add(*LOGIN, FakeResponse(status, {"error": f"bad password {PASSWORD}"}))
    with pytest.raises(AuthError) as exc:
        make_client().login()
    assert_no_secrets(str(exc.value))
    assert_no_secrets(repr(exc.value))


@pytest.mark.parametrize(
    "payload", [{}, {"token": TOKEN}, {"csrf_token": CSRF}, {"token": "", "csrf_token": CSRF}]
)
def test_login_missing_token_raises_auth_error(server, payload):
    server.add(*LOGIN, FakeResponse(200, payload))
    with pytest.raises(AuthError) as exc:
        make_client().login()
    assert_no_secrets(str(exc.value))


def test_login_network_failure_is_unavailable(server):
    server.add(*LOGIN, requests.ConnectionError(f"boom {URL} {PASSWORD}"))
    with pytest.raises(PercepxionUnavailable) as exc:
        make_client().login()
    assert_no_secrets(str(exc.value))
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__


def test_login_server_error_is_unavailable(server):
    server.add(*LOGIN, FakeResponse(503, None))
    with pytest.raises(PercepxionUnavailable):
        make_client().login()


# -- devices -----------------------------------------------------------------------------------


def test_device_search_paginates_and_filters_dev_type(server):
    page1 = [raw_device(i) for i in range(PAGE_SIZE)]
    page1[3]["dev_type"] = "ltrx_gw"  # not an SLC/EMG
    page2 = [raw_device(i) for i in range(PAGE_SIZE, PAGE_SIZE + 7)]
    page2[0]["dev_type"] = "other"
    server.add(*LOGIN, ok_login())
    server.add(
        *DEVICES,
        FakeResponse(200, {"total_results": 57, "sort_last": ["SLC-49"], "search_results": page1}),
        FakeResponse(200, {"total_results": 57, "sort_last": ["SLC-56"], "search_results": page2}),
    )
    devices = list(make_client().iter_devices())
    assert len(devices) == 55
    assert all(isinstance(d, PxDevice) for d in devices)
    bodies = server.bodies("/v3/device/search")
    assert len(bodies) == 2  # second page was short, so no third call
    assert "search_after" not in bodies[0]
    assert bodies[0]["search_string"] == "*"
    assert bodies[0]["limit"] == PAGE_SIZE
    assert bodies[1]["search_after"] == ["SLC-49"]
    assert bodies[1]["pagination"] == "next"


def test_device_record_fields():
    raw = raw_device(1)
    raw["tenant"].append({"id": "org-0002", "name": "Org Two"})
    d = PxDevice.from_api(raw)
    assert d.device_id == "dev-0001"
    assert d.name == "SLC-1"
    assert d.serial == "000000000001"
    assert d.model == "SLC9032"
    assert d.rj45_ports == 16 and isinstance(d.rj45_ports, int)
    assert d.usb_ports == 16 and isinstance(d.usb_ports, int)
    assert d.org_ids == {"org-0001", "org-0002"}
    assert d.ip == "192.0.2.2"
    assert d.product_family == "SLC9000"
    with pytest.raises(AttributeError):
        d.name = "changed"  # frozen


def test_device_record_tolerates_missing_attributes():
    d = PxDevice.from_api({"device_id": "dev-9", "dev_type": "ltrx_itm"})
    assert (d.rj45_ports, d.usb_ports, d.ip, d.org_ids) == (0, 0, None, frozenset())


def test_device_first_page_failure_is_unavailable(server):
    server.add(*LOGIN, ok_login())
    server.add(*DEVICES, FakeResponse(500, None))
    with pytest.raises(PercepxionUnavailable):
        list(make_client().iter_devices())


def test_device_later_page_failure_is_partial_data(server):
    server.add(*LOGIN, ok_login())
    page1 = [raw_device(i) for i in range(PAGE_SIZE)]
    server.add(
        *DEVICES,
        FakeResponse(200, {"total_results": 80, "sort_last": ["SLC-49"], "search_results": page1}),
        requests.Timeout("read timed out"),
    )
    seen = []
    with pytest.raises(PartialData) as exc:
        for d in make_client().iter_devices():
            seen.append(d)
    assert len(seen) == PAGE_SIZE
    assert exc.value.pages_read == 1


# -- ports -------------------------------------------------------------------------------------


def test_port_search_three_pages_no_duplicates(server):
    server.add(*LOGIN, ok_login())
    server.add(*PORTS, port_page(0, 50), port_page(50, 50), port_page(100, 14))
    ports = list(make_client().iter_ports())
    assert len(ports) == 114
    assert all(isinstance(p, PxPort) for p in ports)
    keys = [(p.device_id, p.name) for p in ports]
    assert len(set(keys)) == 114
    bodies = server.bodies("/v3/port/search")
    assert len(bodies) == 3
    assert "search_after" not in bodies[0]
    assert bodies[0]["sort"] == "name" and bodies[0]["order"] == "asc"
    assert bodies[1]["search_after"] == ["Port-049"] and bodies[1]["pagination"] == "next"
    assert bodies[2]["search_after"] == ["Port-099"] and bodies[2]["pagination"] == "next"


def test_port_record_fields():
    p = PxPort.from_api(raw_port(33))
    assert p.device_id == "dev-0001"
    assert p.number == 2 and isinstance(p.number, int)
    assert p.name == "Port-033"
    assert p.hostname == "host-33"
    assert p.facts == {
        "make": "Example",
        "model": "R-1",
        "serial": "900000000033",
        "os": "ExampleOS",
        "os_version": "1.0",
    }
    assert p.org_ids == {"org-0001"}


def test_port_later_page_failure_is_partial_data(server):
    server.add(*LOGIN, ok_login())
    server.add(*PORTS, port_page(0, 50), port_page(50, 50), FakeResponse(502, None))
    seen = []
    with pytest.raises(PartialData) as exc:
        for p in make_client().iter_ports():
            seen.append(p)
    assert len(seen) == 100
    assert exc.value.pages_read == 2


def test_port_search_stops_when_sort_last_does_not_advance(server):
    server.add(*LOGIN, ok_login())
    full = port_page(0, 50)
    stuck = FakeResponse(200, dict(full._payload))
    server.add(*PORTS, full, stuck)
    ports = list(make_client().iter_ports())
    assert len(ports) == 100
    assert len(server.bodies("/v3/port/search")) == 2


# -- CLI jobs ----------------------------------------------------------------------------------


def job(job_id, device_id, created_at, operation="show sysconfig", status="Completed"):
    return {
        "id": job_id,
        "operation": operation,
        # Live API returns a list: one CLI job can target many devices.
        "device_id": device_id if isinstance(device_id, list) else [device_id],
        "status": status,
        "created_at": created_at,
        "tenant_id": "org-0001",
    }


def test_latest_sysconfig_jobs_newest_completed_per_device_across_pages(server):
    filler = [
        job(f"jg-f{i:03d}", f"dev-f{i:03d}", "2026-01-01T00:00:00Z", operation="show ports")
        for i in range(PAGE_SIZE - 3)
    ]
    page1 = [
        job("jg-0001", "dev-0001", "2026-10-01T10:00:00Z"),
        job("jg-0002", "dev-0002", "2026-10-02T10:00:00Z"),
        job("jg-0003", "dev-0001", "2026-10-05T10:00:00Z", status="Failed"),
        *filler,
    ]
    page2 = [
        job("jg-0004", "dev-0001", "2026-10-03T10:00:00Z"),
        job("jg-0005", "dev-0002", "2026-09-30T10:00:00Z"),
        job("jg-0006", "dev-0003", "2026-10-04T10:00:00Z", operation="show sysconfig all"),
    ]
    server.add(*LOGIN, ok_login())
    server.add(
        *JOBS,
        FakeResponse(200, {"total_result": 53, "jobgroup": page1}),
        FakeResponse(200, {"total_result": 53, "jobgroup": page2}),
    )
    result = make_client().latest_sysconfig_jobs()
    assert result == {
        "dev-0001": ("jg-0004", "2026-10-03T10:00:00Z"),
        "dev-0002": ("jg-0002", "2026-10-02T10:00:00Z"),
    }
    bodies = server.bodies("/v1/job/jobgroup/search")
    assert [b["offset"] for b in bodies] == [0, PAGE_SIZE]
    assert all(b["limit"] == PAGE_SIZE for b in bodies)
    assert all(b["type"] == "command" and b["subtype"] == "cli" for b in bodies)
    # search_string matches job names, not operations, so it must stay empty.
    assert all(b["search_string"] == "" for b in bodies)
    assert "tenant_id" not in bodies[0]


def test_latest_sysconfig_jobs_multi_device_job(server):
    server.add(*LOGIN, ok_login())
    jobs = [job("jg-0001", ["dev-0001", "dev-0002"], "2026-10-01T10:00:00Z")]
    server.add(*JOBS, FakeResponse(200, {"total_result": 1, "jobgroup": jobs}))
    result = make_client().latest_sysconfig_jobs()
    assert result == {
        "dev-0001": ("jg-0001", "2026-10-01T10:00:00Z"),
        "dev-0002": ("jg-0001", "2026-10-01T10:00:00Z"),
    }


def test_latest_sysconfig_jobs_stops_at_total_and_passes_tenant(server):
    page = [job(f"jg-{i:04d}", f"dev-{i:04d}", "2026-10-01T00:00:00Z") for i in range(PAGE_SIZE)]
    server.add(*LOGIN, ok_login())
    server.add(*JOBS, FakeResponse(200, {"total_result": PAGE_SIZE, "jobgroup": page}))
    result = make_client().latest_sysconfig_jobs(tenant_id="org-0001")
    assert len(result) == PAGE_SIZE
    bodies = server.bodies("/v1/job/jobgroup/search")
    assert len(bodies) == 1
    assert bodies[0]["tenant_id"] == "org-0001"


def test_latest_sysconfig_jobs_later_page_failure_is_partial_data(server):
    page = [job(f"jg-{i:04d}", f"dev-{i:04d}", "2026-10-01T00:00:00Z") for i in range(PAGE_SIZE)]
    server.add(*LOGIN, ok_login())
    server.add(*JOBS, FakeResponse(200, {"total_result": 120, "jobgroup": page}), FakeResponse(500))
    with pytest.raises(PartialData):
        make_client().latest_sysconfig_jobs()


# -- CLI output --------------------------------------------------------------------------------

SYSCONFIG_TEXT = "Model: SLC9016-16U-16E\\nPower Supply: AC\\n"


def test_cli_output_returns_results_column(server):
    server.add(*LOGIN, ok_login())
    server.add(
        *RESULTS,
        FakeResponse(
            200,
            {
                "measurements": [
                    {
                        "columns": ["time", "Command", "Results"],
                        "rows": [["2026-10-01T10:00:00Z", "show sysconfig", SYSCONFIG_TEXT]],
                    }
                ]
            },
        ),
    )
    assert make_client().cli_output("jg-0001", "dev-0001") == SYSCONFIG_TEXT
    (body,) = server.bodies("/v1/telemetry/result/search")
    assert body == {
        "type": "clicmd",
        "job_group_id": ["jg-0001"],
        "device_id": ["dev-0001"],
        "offset": 0,
        "limit": 1,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"measurements": []},
        {"measurements": [{"columns": ["time"], "rows": [["x"]]}]},
        {"measurements": [{"columns": ["Results"], "rows": []}]},
        {"measurements": [{"columns": ["Results"], "rows": [[None]]}]},
    ],
)
def test_cli_output_none_when_absent(server, payload):
    server.add(*LOGIN, ok_login())
    server.add(*RESULTS, FakeResponse(200, payload))
    assert make_client().cli_output("jg-0001", "dev-0001") is None


def test_cli_output_bad_request_is_unavailable(server):
    server.add(*LOGIN, ok_login())
    server.add(*RESULTS, FakeResponse(400, {"error": "job_group_id required"}))
    with pytest.raises(PercepxionUnavailable):
        make_client().cli_output("jg-0001", "dev-0001")


# -- secrets -----------------------------------------------------------------------------------


def test_repr_has_no_credentials_or_tokens(server):
    server.add(*LOGIN, ok_login())
    c = make_client()
    c.login()
    assert_no_secrets(repr(c))
    assert_no_secrets(str(c))


def test_no_secrets_in_any_error(server):
    errors = []
    server.add(*LOGIN, ok_login())
    server.add(*DEVICES, FakeResponse(500, {"echo": PASSWORD}))
    server.add(*PORTS, requests.ConnectionError(f"{TOKEN} {CSRF}"))
    server.add(*JOBS, FakeResponse(401, {"echo": TOKEN}))
    server.add(*RESULTS, FakeResponse(200, ValueError(f"bad json {CSRF}")))
    c = make_client()
    for call in (
        lambda: list(c.iter_devices()),
        lambda: list(c.iter_ports()),
        c.latest_sysconfig_jobs,
        lambda: c.cli_output("jg-1", "dev-1"),
        lambda: c._request("PUT", "/v2/user/login", {"password": PASSWORD}),
    ):
        with pytest.raises(client_mod.PercepxionError) as exc:
            call()
        errors.append(exc.value)
    assert len(errors) == 5
    for err in errors:
        assert_no_secrets(str(err))
        assert_no_secrets(repr(err))
        assert_no_secrets(json.dumps(err.args))
