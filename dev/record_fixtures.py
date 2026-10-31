"""Record scrubbed Percepxion responses into netbox_lantronix/tests/fixtures/.

    PERCEPXION_URL=... PERCEPXION_USERNAME=... PERCEPXION_PASSWORD=... \
        python dev/record_fixtures.py [device_id_with_stored_sysconfig]

Read-only: uses the plugin's own client, so only allowlisted calls are possible. Every
identifier (device and org IDs, names, serials, addresses, users, tokens, job IDs) is replaced
with a stable placeholder everywhere it appears, including inside CLI output. Review the
written files and run gitleaks before committing.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import conftest  # noqa: E402,F401  (stubs NetBox so the client imports outside NetBox)
from netbox_lantronix.percepxion.client import PercepxionClient  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "netbox_lantronix" / "tests" / "fixtures"

# Keys whose string values identify a customer, device, person or session.
ID_KEYS = {"device_id", "parent_device_id", "id", "job_group_id", "tenant_id", "org_id",
           "project_id", "user_id", "created_by", "updated_by", "owner"}
NAME_KEYS = {"device_name", "hostname", "tenant_name", "org_name", "project_name", "username",
             "first_name", "last_name", "display_name", "location", "site", "description"}
PERSON_KEYS = {"email", "phone", "mobile", "title"}
SERIAL_KEYS = {"serial_num", "serial_number", "serial"}
SECRET_KEYS = {"token", "csrf_token", "refresh_token", "password", "api_key", "secret"}

IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
MAC = re.compile(r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Hex lookarounds instead of \b: a UUID can follow an underscore ("MYSTQ_PT_<uuid>").
UUID = re.compile(r"(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
                  r"(?![0-9a-f])", re.I)
# Full eight-group form or a "::" form; never a MAC address.
IPV6 = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\b"
                  r"|\b(?:[0-9A-Fa-f]{1,4}:)+:(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*)?")


class Scrubber:
    def __init__(self):
        self.map: dict[str, str] = {}
        self.kind: dict[str, str] = {}
        self.counts: dict[str, int] = {}

    def _placeholder(self, kind: str, value: str) -> str:
        if value in self.map:
            return self.map[value]
        n = self.counts[kind] = self.counts.get(kind, 0) + 1
        if kind == "id":
            new = f"00204AFIXTURE{n:019d}" if len(value) == 32 else f"fixture-id-{n:04d}"
        elif kind == "name":
            new = f"fixture-name-{n:02d}"
        elif kind == "serial":
            new = f"FIXSER{n:06d}"
        else:
            new = "<redacted>"
        self.map[value] = new
        self.kind[value] = kind
        return new

    def collect(self, obj, key=None):
        if isinstance(obj, dict):
            for k, v in obj.items():
                self.collect(v, k)
        elif isinstance(obj, list):
            for v in obj:
                self.collect(v, key)
        elif isinstance(obj, str) and obj and key:
            k = key.lower()
            # Default port names and placeholders carry no identity.
            if re.fullmatch(r"Port-\d+|<redacted>|", obj):
                return
            if k in SECRET_KEYS:
                self._placeholder("secret", obj)
            elif k in ID_KEYS or k.endswith("_id"):
                self._placeholder("id", obj)
            elif k in SERIAL_KEYS:
                self._placeholder("serial", obj)
            elif k in NAME_KEYS or "name" in k or k in PERSON_KEYS:
                self._placeholder("name", obj)

    def apply(self, text: str) -> str:
        for old in sorted(self.map, key=len, reverse=True):
            if len(old) < 3:
                continue
            escaped = json.dumps(old)[1:-1]
            if self.kind[old] == "name" and not re.search(r"[\d._-]", old):
                # A plain word ("Switch") is replaced only as a whole value, never inside text.
                text = text.replace(f'"{escaped}"', f'"{self.map[old]}"')
            else:
                # Case-insensitive: Percepxion lowercases device IDs in pagination cursors.
                text = re.sub(re.escape(escaped), self.map[old], text, flags=re.I)
        uuids: dict[str, str] = {}
        text = UUID.sub(lambda m: uuids.setdefault(
            m.group().lower(), f"00000000-0000-4000-8000-{len(uuids) + 1:012d}"), text)
        text = EMAIL.sub("user@example.com", text)
        text = MAC.sub("00:00:5e:00:53:01", text)
        text = IPV4.sub(lambda m: "192.0.2." + str(sum(map(int, m.group().split("."))) % 254 + 1),
                        text)
        return IPV6.sub("2001:db8::1", text)


def main():
    client = PercepxionClient(os.environ["PERCEPXION_URL"], os.environ["PERCEPXION_USERNAME"],
                              os.environ["PERCEPXION_PASSWORD"])
    recorded: list[tuple[str, dict, dict]] = []
    real = client._request

    def recording(method, path, body):
        data = real(method, path, body)
        recorded.append((path, body, data))
        return data

    client._request = recording
    client.login()
    list(client.iter_devices())
    list(client.iter_ports())
    jobs = client.latest_sysconfig_jobs()
    want = sys.argv[1] if len(sys.argv) > 1 else next(iter(jobs), None)
    if want in jobs:
        client.cli_output(jobs[want][0], want)

    scrub = Scrubber()
    for _, body, data in recorded:
        scrub.collect(body)
        scrub.collect(data)
    scrub.collect({"username": os.environ["PERCEPXION_USERNAME"]})

    OUT.mkdir(parents=True, exist_ok=True)
    names = {"/v2/user/login": "login", "/v3/device/search": "device_search",
             "/v3/port/search": "port_search", "/v1/job/jobgroup/search": "jobgroup_search",
             "/v1/telemetry/result/search": "clicmd_result"}
    seen: dict[str, int] = {}
    manifest = []
    for path, body, data in recorded:
        base = names[path]
        seen[base] = seen.get(base, 0) + 1
        fname = f"{base}_{seen[base]}.json"
        if path == "/v2/user/login":
            body = {"username": "<redacted>", "password": "<redacted>"}
        text = scrub.apply(json.dumps({"request": body, "response": data}, indent=1,
                                      sort_keys=True))
        (OUT / fname).write_text(text + "\n")
        manifest.append({"file": fname, "path": path})
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    secret = os.environ["PERCEPXION_PASSWORD"]
    leaked = [m["file"] for m in manifest if secret in (OUT / m["file"]).read_text()]
    print(f"wrote {len(manifest)} files; placeholders {scrub.counts}; password found in {leaked}")


if __name__ == "__main__":
    main()
