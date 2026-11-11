"""Load coverage-gate Targets from the JSON fixture format used by the tests."""

import json
from pathlib import Path

from netbox_lantronix.coverage.core import ConsoleLink, Slc, Target


def targets_from_dicts(items: list[dict]) -> list[Target]:
    slcs: dict[str, Slc] = {}
    out = []
    for t in items:
        links = []
        for ln in t.get("links", []):
            slc = None
            if ln.get("slc"):
                s = ln["slc"]
                slc = slcs.setdefault(str(s["id"]), Slc(**s))
            links.append(
                ConsoleLink(
                    console_port=ln["console_port"],
                    cabled=ln.get("cabled", False),
                    peer_is_console_server_port=ln.get("peer_is_console_server_port", False),
                    slc=slc,
                    slc_port=ln.get("slc_port"),
                    port_state=ln.get("port_state"),
                )
            )
        out.append(
            Target(
                id=str(t["id"]),
                name=t["name"],
                active=t.get("active", True),
                site=t.get("site"),
                role=t.get("role"),
                platform=t.get("platform"),
                tags=t.get("tags", []),
                links=links,
            )
        )
    return out


def load_targets(path: str | Path) -> list[Target]:
    return targets_from_dicts(json.loads(Path(path).read_text()))
