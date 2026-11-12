#!/usr/bin/env python3
"""Fail when the code shared with the sibling integration has drifted.

netbox-lantronix and nautobot-app-ssot-lantronix each carry a copy of the Percepxion client,
record parsing, device-type resolver, sanitizers and console coverage gate, with their tests.
A fix made in one copy only is a fix the other product never gets.

Usage: python scripts/check_shared.py PATH_TO_THE_OTHER_REPOSITORY

Package names, distribution names and the pinned client version are expected to differ and
are ignored. Standard library only.
"""

from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

# Relative to the Python package directory.
SHARED = (
    "percepxion/__init__.py",
    "percepxion/client.py",
    "percepxion/records.py",
    "percepxion/resolver.py",
    "percepxion/telemetry.py",
    "sanitize.py",
    "coverage/__init__.py",
    "coverage/core.py",
    "tests/coverage_fixtures.py",
    "tests/test_client.py",
    "tests/test_coverage_core.py",
    "tests/test_resolver.py",
    "tests/test_sanitize.py",
    "tests/test_telemetry.py",
)
# Relative to the repository root.
SHARED_AT_ROOT = ("scripts/check_shared.py",)

# The longer name first: it contains the shorter one.
_NAMES = re.compile(
    r"nautobot_ssot_lantronix|netbox_lantronix|nautobot-app-ssot-lantronix|netbox-lantronix"
)
_VERSION = re.compile(r'^(CLIENT_VERSION = )"[^"]*"$', re.MULTILINE)


def normalize(text: str) -> str:
    """Blank out what the two copies are allowed to disagree on."""
    return _VERSION.sub(r'\1"<version>"', _NAMES.sub("<name>", text))


def package_dir(root: Path) -> Path:
    found = sorted(p.parent.parent for p in root.glob("*/percepxion/client.py"))
    if len(found) != 1:
        raise SystemExit(f"{root}: expected one percepxion/client.py, found {len(found)}")
    return found[0]


def shared_files(root: Path) -> dict[str, Path]:
    package = package_dir(root)
    files = {name: package / name for name in SHARED}
    files.update({name: root / name for name in SHARED_AT_ROOT})
    return files


def compare(here: Path, other: Path) -> list[str]:
    """Return one report per shared file that differs or is missing; empty when in step."""
    reports = []
    theirs = shared_files(other)
    for name, mine in shared_files(here).items():
        missing = [str(p) for p in (mine, theirs[name]) if not p.is_file()]
        if missing:
            reports.append(f"{name}: missing {', '.join(missing)}\n")
            continue
        a = normalize(mine.read_text(encoding="utf-8")).splitlines(keepends=True)
        b = normalize(theirs[name].read_text(encoding="utf-8")).splitlines(keepends=True)
        if a != b:
            reports.append("".join(difflib.unified_diff(a, b, str(mine), str(theirs[name]))))
    return reports


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    here = Path(__file__).resolve().parent.parent
    reports = compare(here, Path(argv[1]).resolve())
    if not reports:
        print(f"Shared code matches ({len(SHARED) + len(SHARED_AT_ROOT)} files).")
        return 0
    for report in reports:
        print(report)
    print(
        f"{len(reports)} shared file(s) differ. Make the same change in both repositories, "
        "on branches with the same name.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
