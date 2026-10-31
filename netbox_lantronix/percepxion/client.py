"""Read-only Percepxion API client. No NetBox or Django imports (reused by the Nautobot app).

Every request goes through ``PercepxionClient._request``, which refuses any (method, path)
outside ``ALLOWED`` before touching the network. Credentials and session tokens never
appear in exception messages, logs or ``repr``. HTTPS is required and TLS verification
is on by default.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import requests

from .records import PxDevice, PxPort
from .telemetry import PxPortState, parse_port_states

# netbox_lantronix/__init__.py imports NetBox, so the version is pinned here rather than imported.
CLIENT_VERSION = "0.1.0"
USER_AGENT = f"netbox-lantronix/{CLIENT_VERSION}"

ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/v2/user/login"),
        ("POST", "/v3/device/search"),
        ("POST", "/v3/port/search"),
        ("POST", "/v1/job/jobgroup/search"),
        ("POST", "/v1/telemetry/result/search"),
        ("POST", "/v1/telemetry/stat/view"),
    }
)

TIMEOUT = (30, 60)  # connect, read (seconds)
PAGE_SIZE = 50
MAX_PAGES = 10_000  # guard against a server that never returns a short page
ITM_DEV_TYPE = "ltrx_itm"
SYSCONFIG_OPERATION = "show sysconfig"
COMPLETED = "Completed"

log = logging.getLogger(__name__)


class PercepxionError(Exception):
    """Base class. Messages carry method, path and status only, never bodies or headers."""


class ForbiddenRequest(PercepxionError):
    """A request outside ALLOWED was attempted. Raised before any network I/O."""


class AuthError(PercepxionError):
    """Login rejected (HTTP 400/401/403) or the response held no token."""


class PercepxionUnavailable(PercepxionError):
    """Network failure, timeout, unexpected status or malformed response."""


class PartialData(PercepxionError):
    """A page after the first failed; results so far are incomplete."""

    def __init__(self, message: str, pages_read: int):
        super().__init__(message)
        self.pages_read = pages_read


class PercepxionClient:
    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        verify: bool = True,
        ca_bundle: str | None = None,
        proxy: str | None = None,
    ):
        parts = urlsplit(url or "")
        if parts.scheme.lower() != "https" or not parts.netloc:
            raise ValueError("Percepxion url must be an https:// URL")
        self.url = url.rstrip("/")
        self._username = username
        self._password = password
        self.verify = verify
        self._session = requests.Session()
        self._session.verify = ca_bundle if ca_bundle else verify
        if proxy:
            self._session.proxies = {"https": proxy}
        self._session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            }
        )
        self._logged_in = False

    def __repr__(self) -> str:
        return f"PercepxionClient(url={self.url!r}, verify={self._session.verify!r})"

    __str__ = __repr__

    # -- transport -----------------------------------------------------------------------------

    def _request(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        method = method.upper()
        if (method, path) not in ALLOWED:
            raise ForbiddenRequest(f"{method} {path} is not on the Percepxion allowlist")
        try:
            resp = self._session.request(method, self.url + path, json=body, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise PercepxionUnavailable(f"{method} {path} failed: {type(exc).__name__}") from None
        status = resp.status_code
        if path == "/v2/user/login" and status in (400, 401, 403):
            raise AuthError(f"Percepxion login rejected (HTTP {status})")
        if status in (401, 403):
            raise AuthError(f"{method} {path} not authorized (HTTP {status})")
        if not 200 <= status < 300:
            raise PercepxionUnavailable(f"{method} {path} returned HTTP {status}")
        try:
            data = resp.json()
        except ValueError:
            raise PercepxionUnavailable(f"{method} {path} returned non-JSON") from None
        if not isinstance(data, dict):
            raise PercepxionUnavailable(f"{method} {path} returned unexpected JSON")
        return data

    def login(self) -> None:
        data = self._request(
            "POST", "/v2/user/login", {"username": self._username, "password": self._password}
        )
        token, csrf = data.get("token"), data.get("csrf_token")
        if not token or not csrf:
            raise AuthError("Percepxion login response held no session token")
        self._session.headers["x-mystq-token"] = str(token)
        self._session.headers["x-csrf-token"] = str(csrf)
        self._logged_in = True

    def _ensure_login(self) -> None:
        if not self._logged_in:
            self.login()

    def _page(self, path: str, body: dict[str, Any], page_index: int) -> dict[str, Any]:
        """First-page failures propagate as-is; later failures become PartialData."""
        try:
            return self._request("POST", path, body)
        except PercepxionError as exc:
            if page_index == 0:
                raise
            raise PartialData(
                f"POST {path} page {page_index + 1} failed: {exc}", pages_read=page_index
            ) from None

    def _search_after_pages(self, path: str, items_key: str, sort: str) -> Iterator[dict]:
        self._ensure_login()
        search_after: Any = None
        for page_index in range(MAX_PAGES):
            body: dict[str, Any] = {
                "search_string": "*",
                "limit": PAGE_SIZE,
                "sort": sort,
                "order": "asc",
            }
            if search_after is not None:
                body["search_after"] = search_after
                body["pagination"] = "next"
            data = self._page(path, body, page_index)
            items = data.get(items_key) or []
            if not isinstance(items, list):
                raise PercepxionUnavailable(f"POST {path} returned unexpected JSON")
            yield from items
            next_after = data.get("sort_last")
            if len(items) < PAGE_SIZE or next_after in (None, "") or next_after == search_after:
                return
            search_after = next_after

    # -- reads ---------------------------------------------------------------------------------

    def iter_devices(self) -> Iterator[PxDevice]:
        for raw in self._search_after_pages("/v3/device/search", "search_results", "device_name"):
            if isinstance(raw, dict) and raw.get("dev_type") == ITM_DEV_TYPE:
                yield PxDevice.from_api(raw)

    def iter_ports(self) -> Iterator[PxPort]:
        for raw in self._search_after_pages("/v3/port/search", "result", "name"):
            if isinstance(raw, dict):
                yield PxPort.from_api(raw)

    def port_states(self, device_id: str, org_id: str | None = None) -> dict[int, PxPortState]:
        """Attached-device state per port for one device (telemetry view, one call per device)."""
        self._ensure_login()
        body: dict[str, Any] = {"device_id": device_id, "selected": True}
        if org_id:
            body["tenant_id"] = org_id
        return parse_port_states(self._request("POST", "/v1/telemetry/stat/view", body))

    def latest_sysconfig_jobs(self, tenant_id: str | None = None) -> dict[str, tuple[str, str]]:
        """device_id -> (job_group_id, created_at) for the newest Completed `show sysconfig` job."""
        self._ensure_login()
        path = "/v1/job/jobgroup/search"
        latest: dict[str, tuple[str, str]] = {}
        offset = 0
        for page_index in range(MAX_PAGES):
            body: dict[str, Any] = {
                # search_string matches job names, not operations; list all and filter below.
                "search_string": "",
                "type": "command",
                "subtype": "cli",
                "offset": offset,
                "limit": PAGE_SIZE,
            }
            if tenant_id:
                body["tenant_id"] = tenant_id
            data = self._page(path, body, page_index)
            jobs = data.get("jobgroup") or []
            if not isinstance(jobs, list):
                raise PercepxionUnavailable(f"POST {path} returned unexpected JSON")
            for job in jobs:
                if not isinstance(job, dict):
                    continue
                if job.get("operation") != SYSCONFIG_OPERATION or job.get("status") != COMPLETED:
                    continue
                job_id = job.get("id")
                created = str(job.get("created_at") or "")
                # device_id is a list: one CLI job can run on many devices.
                device_ids = job.get("device_id") or []
                if isinstance(device_ids, str):
                    device_ids = [device_ids]
                if not job_id or not isinstance(device_ids, list):
                    continue
                for device_id in device_ids:
                    if not device_id:
                        continue
                    current = latest.get(str(device_id))
                    if current is None or _ts(created) > _ts(current[1]):
                        latest[str(device_id)] = (str(job_id), created)
            offset += len(jobs)
            total = data.get("total_result")
            if len(jobs) < PAGE_SIZE or (isinstance(total, int) and offset >= total):
                break
        return latest

    def cli_output(self, job_group_id: str, device_id: str) -> str | None:
        """Raw `Results` text of one CLI job on one device (literal \\n kept), or None."""
        self._ensure_login()
        data = self._request(
            "POST",
            "/v1/telemetry/result/search",
            {
                "type": "clicmd",
                "job_group_id": [job_group_id],
                "device_id": [device_id],
                "offset": 0,
                "limit": 1,
            },
        )
        measurements = data.get("measurements") or []
        if not measurements or not isinstance(measurements[0], dict):
            return None
        columns = measurements[0].get("columns") or []
        rows = measurements[0].get("rows") or []
        if "Results" not in columns or not rows or not isinstance(rows[0], list):
            return None
        idx = columns.index("Results")
        if idx >= len(rows[0]) or rows[0][idx] is None:
            return None
        return str(rows[0][idx])


def _ts(value: str) -> tuple[int, Any]:
    """Sort key for created_at: parsed ISO-8601 when possible, raw string otherwise."""
    try:
        return (1, datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return (0, value)
