"""Sanitize device-supplied strings.

Device names, port names, hostnames and facts come from devices and are
untrusted. Names are restricted to a safe character set; other text is
stripped of control and bidirectional-override characters and length-limited.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata

NAME_MAX = 64
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

# Bidirectional embeddings/overrides (U+202A-202E) and isolates (U+2066-2069).
_BIDI = {chr(cp) for cp in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))}


def safe_name(value: object) -> str | None:
    """Return ``value`` if it is a usable name, else None.

    A usable name is 1 to 64 characters of letters, digits, dot, underscore and
    hyphen after trimming surrounding whitespace. Names are never rewritten.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > NAME_MAX or not _SAFE_NAME.match(value):
        return None
    return value


def clean_text(value: object, max_len: int) -> str:
    """Return display-safe text: no control or bidi characters, single spaces, truncated.

    Control characters become spaces, bidi overrides and isolates are removed,
    whitespace runs collapse to one space, and the result is cut to ``max_len``.
    None (or any non-string) gives an empty string; never raises.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = str(value)
        except Exception:
            return ""
    chars = []
    for ch in value:
        if ch in _BIDI:
            continue
        if unicodedata.category(ch) == "Cc":
            chars.append(" ")
        else:
            chars.append(ch)
    text = " ".join("".join(chars).split())
    if max_len is None or max_len < 0:
        return text
    return text[:max_len].rstrip()


SERIAL_MAX = 50  # dcim.Device.serial max_length
_SAFE_SERIAL = re.compile(r"^[A-Za-z0-9-]+$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def safe_serial(value: object) -> str | None:
    """Return a usable serial, "" for none, or None if it is present but unsafe.

    Serials are compared for identity, so they are validated, never rewritten.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return ""
    if len(value) > SERIAL_MAX or not _SAFE_SERIAL.match(value):
        return None
    return value


def safe_id(value: object) -> str | None:
    """Return a Percepxion device ID if it has the expected shape, else None."""
    if not isinstance(value, str) or not _SAFE_ID.match(value):
        return None
    return value


def safe_ip(value: object) -> str | None:
    """Return a plain IPv4/IPv6 address string, or None.

    Scoped IPv6 (``fe80::1%eth0``) is rejected: the scope can carry characters that change
    a URL built from it. The result is rebuilt from the integer value, never echoed.
    """
    if not isinstance(value, str):
        return None
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if getattr(ip, "scope_id", None):
        return None
    return str(ipaddress.ip_address(int(ip)) if ip.version == 4 else ipaddress.IPv6Address(int(ip)))
