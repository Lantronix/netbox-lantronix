"""Map Percepxion device data to Lantronix NetBox device-type slugs.

Pure functions: no NetBox, Django or network access.

SLC 9000 signature table
------------------------
The table below was derived from the 20 SLC 9000 device-type YAML files in the
Lantronix device-type library (manufacturer "Lantronix", slugs
``lantronix-<model lower>``). For each file the console-server ports were
counted by ``type``: ``rj-45`` ports give the RJ45 count and ``usb-a`` ports
give the USB count. The counts agree with the I/O module convention in the SKU
name: the SLC90NN stem is the RJ45 count, ``-NNU`` adds NN USB ports, ``-NNE``
is an Ethernet switch module (no serial ports). Each stem also ships as a
``-DC`` variant with identical ports.

Percepxion reports ``model`` as ``SLC90NN`` where NN is the total serial port
count (RJ45 + USB), so an SLC9016-16U-16E reports SLC9032. Neither the switch
module nor the power supply type is reported, so one signature matches 2 to 6
types (research R7).

SLC 8000 is not resolved here: the reference playbook has no SLC 8000 rule
(its default rule only appends ``S``), so ``resolve_sku`` returns ``[]`` for it.
"""

from __future__ import annotations

import re

SLUG_PREFIX = "lantronix-"

# SKU stem (AC power) -> (RJ45 serial ports, USB serial ports).
SLC9000_STEMS: dict[str, tuple[int, int]] = {
    "SLC9016": (16, 0),
    "SLC9016-16E": (16, 0),
    "SLC9016-32E": (16, 0),
    "SLC9016-16U": (16, 16),
    "SLC9016-16U-16E": (16, 16),
    "SLC9016-32U": (16, 32),
    "SLC9032": (32, 0),
    "SLC9032-16E": (32, 0),
    "SLC9032-16U": (32, 16),
    "SLC9048": (48, 0),
}

# Every SLC 9000 device type: SKU -> (RJ45, USB). 20 entries.
SLC9000_TYPES: dict[str, tuple[int, int]] = {}
for _stem, _counts in SLC9000_STEMS.items():
    SLC9000_TYPES[_stem] = _counts
    SLC9000_TYPES[f"{_stem}-DC"] = _counts

SLC9000_SLUGS: frozenset[str] = frozenset(SLUG_PREFIX + sku.lower() for sku in SLC9000_TYPES)

_SLC9000_MODEL = re.compile(r"^SLC90(\d{2})$")
_EMG85_MODEL = re.compile(r"^EMG85(\d{2})(\d{2})$")
_EMG75_MODEL = re.compile(r"^EMG75\d{4}$")
_PORT_NAME = re.compile(r"^Port-(\d{1,2})$", re.IGNORECASE)
_SAFE_MODEL = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def slug_for(sku: str) -> str:
    """Return the device-type slug for an orderable SKU."""
    return SLUG_PREFIX + sku.lower()


def _count(value: object) -> int | None:
    """Percepxion reports port counts as strings; accept str or int."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def resolve_sku(model: object, rj45: object, usb: object) -> list[str]:
    """Return the sorted candidate SLC 9000 slugs for a Percepxion signature.

    ``model`` is the Percepxion ``model`` attribute (SLC90NN, NN = total serial
    ports); ``rj45`` and ``usb`` are ``num_rj45_ports`` and ``num_usb_ports``.
    Returns ``[]`` when nothing matches, including SLC 8000 and EMG models.
    """
    if not isinstance(model, str):
        return []
    match = _SLC9000_MODEL.match(model.strip().upper())
    rj45_n = _count(rj45)
    usb_n = _count(usb) if usb not in (None, "") else 0
    if match is None or rj45_n is None or usb_n is None:
        return []
    if int(match.group(1)) != rj45_n + usb_n:
        return []
    return sorted(
        slug_for(sku) for sku, counts in SLC9000_TYPES.items() if counts == (rj45_n, usb_n)
    )


def emg_sku(model: object) -> str | None:
    """Return the orderable EMG SKU for a Percepxion hardware model.

    EMG 8500: keep the two I/O digits, sort the two connectivity digits
    descending (the hardware model records which bay holds the radio, the SKU
    always lists it first), append ``S``: EMG851101 -> EMG851110S.
    EMG 7500: the model plus ``S``: EMG751001 -> EMG751001S.
    Anything else returns None.
    """
    if not isinstance(model, str):
        return None
    value = model.strip().upper()
    match = _EMG85_MODEL.match(value)
    if match:
        io_digits, conn_digits = match.groups()
        return "EMG85" + io_digits + "".join(sorted(conn_digits, reverse=True)) + "S"
    if _EMG75_MODEL.match(value):
        return value + "S"
    return None


def is_default_port_name(name: object, number: object) -> bool:
    """True when ``name`` is the default ``Port-N`` or ``Port-0N`` for ``number``."""
    if not isinstance(name, str):
        return False
    port_number = _count(number)
    match = _PORT_NAME.match(name.strip())
    if match is None or port_number is None:
        return False
    digits = match.group(1)
    return digits in (str(port_number), f"{port_number:02d}")


# ``show sysconfig`` parsing. Fields are laid out two columns per line; a value
# ends at a run of two or more spaces (the next column) or the end of the line.
_FIELD_END = r"(?=[ \t]{2,}|[ \t]*$)"
# No overlapping quantifiers before "Model:": long whitespace runs stay linear.
_SYS_MODEL = re.compile(r"(?m)(?:^[ \t]*|[ \t]{2})Model:[ \t]+(\S+)")
_SYS_POWER = re.compile(r"(?m)Power Supply:[ \t]+(.+?)" + _FIELD_END)
_SYS_SWITCH = re.compile(r"(?m)Ethernet Switch:[ \t]+(.+?)" + _FIELD_END)
_SYS_MODULES = re.compile(r"(?m)I/O Module Type\(s\):[ \t]+(.+?)" + _FIELD_END)
_MAX_SYSCONFIG = 65536


def parse_sysconfig(text: object) -> dict | None:
    """Parse stored ``show sysconfig`` output.

    Handles the literal ``\\n`` sequences Percepxion stores in place of
    newlines. Returns ``{model, power, switch, io_modules}`` or None when there
    is no usable ``Model:`` line. ``power`` is "AC", "DC" or None; ``switch`` is
    the reported value (e.g. "Installed/16 port") or None when not installed.
    """
    if not isinstance(text, str) or not text:
        return None
    text = text[:_MAX_SYSCONFIG].replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r", "")

    model_match = _SYS_MODEL.search(text)
    if model_match is None or not _SAFE_MODEL.match(model_match.group(1)):
        return None

    power = None
    power_match = _SYS_POWER.search(text)
    if power_match:
        first = re.split(r"[\s,]", power_match.group(1).strip(), maxsplit=1)[0].upper()
        if first in ("AC", "DC"):
            power = first

    switch = None
    switch_match = _SYS_SWITCH.search(text)
    if switch_match:
        value = switch_match.group(1).strip()
        if value and not value.lower().startswith("not installed"):
            switch = value

    io_modules: list[str] = []
    modules_match = _SYS_MODULES.search(text)
    if modules_match:
        io_modules = [
            part.strip()
            for part in modules_match.group(1).split(",")
            if part.strip() and part.strip().lower() not in ("none", "not installed")
        ]

    return {
        "model": model_match.group(1),
        "power": power,
        "switch": switch,
        "io_modules": io_modules,
    }


def sku_from_sysconfig(info: object) -> str | None:
    """Return the SLC 9000 slug for parsed sysconfig, or None if not a library type."""
    if not isinstance(info, dict):
        return None
    model = info.get("model")
    if not isinstance(model, str) or not _SAFE_MODEL.match(model):
        return None
    power = info.get("power")
    if power not in ("AC", "DC"):
        # Unknown power: don't guess. A wrong type is permanent, so fall back to rules.
        return None
    sku = model.upper()
    if power == "DC" and not sku.endswith("-DC"):
        sku += "-DC"
    slug = slug_for(sku)
    return slug if slug in SLC9000_SLUGS else None
