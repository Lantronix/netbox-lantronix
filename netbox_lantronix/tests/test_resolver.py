import pytest

from netbox_lantronix.percepxion.resolver import (
    SLC9000_SLUGS,
    SLC9000_TYPES,
    emg_sku,
    is_default_port_name,
    parse_sysconfig,
    resolve_sku,
    sku_from_sysconfig,
)

# Scrubbed from a real SLC9016-16U-16E: layout kept, MACs and serials zeroed.
SYSCONFIG_LINES = [
    "Model:             SLC9016-16U-16E      Power Supply: AC, 2 power supplies",
    "Serial Number:          0000000000      MAC Address:   00:00:00:00:00:00",
    "Number of USB Ports:             2      Cellular Modem:      Not Installed",
    "Firmware Version:       9.7.0.0R21      Ethernet Switch: Installed/16 port",
    "I/O Module Type(s):                                RJ45-16, USB-16, ETH-16",
]
SYSCONFIG_LITERAL = "\\n".join(SYSCONFIG_LINES) + "\\n"
SYSCONFIG_REAL = "\n".join(SYSCONFIG_LINES) + "\n"


def test_table_has_twenty_types():
    assert len(SLC9000_TYPES) == 20
    assert len(SLC9000_SLUGS) == 20
    assert "lantronix-slc9016-16u-16e-dc" in SLC9000_SLUGS


def test_reported_model_totals_match_every_type():
    # Each type's RJ45 + USB total maps back to a valid SLC90NN model.
    for sku, (rj45, usb) in SLC9000_TYPES.items():
        model = f"SLC90{rj45 + usb:02d}"
        assert f"lantronix-{sku.lower()}" in resolve_sku(model, rj45, usb)


def test_resolve_slc9032_rj45_and_usb():
    assert resolve_sku("SLC9032", "16", "16") == [
        "lantronix-slc9016-16u",
        "lantronix-slc9016-16u-16e",
        "lantronix-slc9016-16u-16e-dc",
        "lantronix-slc9016-16u-dc",
    ]


def test_resolve_slc9016_rj45_only():
    assert resolve_sku("SLC9016", 16, 0) == [
        "lantronix-slc9016",
        "lantronix-slc9016-16e",
        "lantronix-slc9016-16e-dc",
        "lantronix-slc9016-32e",
        "lantronix-slc9016-32e-dc",
        "lantronix-slc9016-dc",
    ]


def test_resolve_slc9048_two_families():
    assert resolve_sku("SLC9048", "48", "0") == ["lantronix-slc9048", "lantronix-slc9048-dc"]
    assert resolve_sku("SLC9048", "16", "32") == [
        "lantronix-slc9016-32u",
        "lantronix-slc9016-32u-dc",
    ]
    assert resolve_sku("SLC9048", "32", "16") == [
        "lantronix-slc9032-16u",
        "lantronix-slc9032-16u-dc",
    ]


def test_resolve_missing_usb_treated_as_zero():
    assert resolve_sku("SLC9032", "32", None) == [
        "lantronix-slc9032",
        "lantronix-slc9032-16e",
        "lantronix-slc9032-16e-dc",
        "lantronix-slc9032-dc",
    ]


@pytest.mark.parametrize(
    ("model", "rj45", "usb"),
    [
        ("SLC9032", "16", "0"),  # total mismatch
        ("SLC9099", "24", "0"),  # no such type
        ("SLC80081201", "8", "0"),  # SLC 8000 not resolved
        ("EMG851101", "8", "0"),
        (None, "16", "0"),
        ("SLC9016", "abc", "0"),
        ("SLC9016", "-16", "0"),
        ("SLC9016", True, 0),
    ],
)
def test_resolve_no_match(model, rj45, usb):
    assert resolve_sku(model, rj45, usb) == []


@pytest.mark.parametrize(
    ("model", "sku"),
    [
        ("EMG851101", "EMG851110S"),
        ("EMG851110", "EMG851110S"),
        ("EMG852002", "EMG852020S"),
        ("EMG851000", "EMG851000S"),
        ("EMG751001", "EMG751001S"),
        ("EMG752011", "EMG752011S"),
        ("emg851101", "EMG851110S"),
    ],
)
def test_emg_sku(model, sku):
    assert emg_sku(model) == sku


@pytest.mark.parametrize("model", ["SLC9016", "EMG8511", "EMG85110S", "", None, 851101])
def test_emg_sku_not_emg(model):
    assert emg_sku(model) is None


@pytest.mark.parametrize(
    ("name", "number", "expected"),
    [
        ("Port-07", 7, True),
        ("Port-7", 7, True),
        ("Port-12", 12, True),
        ("Port-12", "12", True),
        ("Port-07", 8, False),
        ("Port-007", 7, False),
        ("router1", 7, False),
        ("Port-07-old", 7, False),
        (None, 7, False),
        ("Port-07", None, False),
    ],
)
def test_is_default_port_name(name, number, expected):
    assert is_default_port_name(name, number) is expected


@pytest.mark.parametrize("text", [SYSCONFIG_LITERAL, SYSCONFIG_REAL])
def test_parse_sysconfig(text):
    assert parse_sysconfig(text) == {
        "model": "SLC9016-16U-16E",
        "power": "AC",
        "switch": "Installed/16 port",
        "io_modules": ["RJ45-16", "USB-16", "ETH-16"],
    }


def test_parse_sysconfig_dc_and_no_switch():
    text = (
        SYSCONFIG_LITERAL.replace("SLC9016-16U-16E", "SLC9016-16U     ")
        .replace("AC, 2 power supplies", "DC, 2 power supplies")
        .replace("Installed/16 port", "Not Installed")
        .replace("RJ45-16, USB-16, ETH-16", "        RJ45-16, USB-16")
    )
    info = parse_sysconfig(text)
    assert info == {
        "model": "SLC9016-16U",
        "power": "DC",
        "switch": None,
        "io_modules": ["RJ45-16", "USB-16"],
    }
    assert sku_from_sysconfig(info) == "lantronix-slc9016-16u-dc"


def test_parse_sysconfig_dc_model_string():
    # 9.8 firmware reports the -DC suffix in Model: (read from the manufacturing VPD flag).
    text = SYSCONFIG_LITERAL.replace("SLC9016-16U-16E   ", "SLC9016-16U-16E-DC").replace(
        "AC, 2 power supplies", "DC, 2 power supplies"
    )
    info = parse_sysconfig(text)
    assert info["model"] == "SLC9016-16U-16E-DC"
    assert info["power"] == "DC"
    assert sku_from_sysconfig(info) == "lantronix-slc9016-16u-16e-dc"


def test_sku_from_sysconfig_ac():
    assert sku_from_sysconfig(parse_sysconfig(SYSCONFIG_LITERAL)) == "lantronix-slc9016-16u-16e"


def test_sku_from_sysconfig_unknown_model():
    info = parse_sysconfig(SYSCONFIG_LITERAL.replace("SLC9016-16U-16E", "SLC9099-99X    "))
    assert info is not None and info["model"] == "SLC9099-99X"
    assert sku_from_sysconfig(info) is None


def test_sku_from_sysconfig_power_unknown_does_not_guess():
    info = {"model": "SLC9048", "power": None, "switch": None, "io_modules": []}
    assert sku_from_sysconfig(info) is None


@pytest.mark.parametrize(
    "info",
    [None, {}, {"model": None}, {"model": "SLC9016<script>"}, "SLC9016", {"model": 9016}],
)
def test_sku_from_sysconfig_bad_input(info):
    assert sku_from_sysconfig(info) is None


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        42,
        b"Model: SLC9016",
        "garbage \\n\\n\x00\x1b[31m no fields here",
        "Power Supply: AC, 2 power supplies",
        "Model:\\nPower Supply: AC",
        "Model:    <img src=x>      Power Supply: AC",
    ],
)
def test_parse_sysconfig_garbage(text):
    assert parse_sysconfig(text) is None


def test_parse_sysconfig_minimal_fields():
    info = parse_sysconfig("Model:  SLC9032")
    assert info == {"model": "SLC9032", "power": None, "switch": None, "io_modules": []}


def test_sysconfig_model_regex_is_linear_on_long_whitespace():
    import time

    start = time.monotonic()
    parse_sysconfig("x" + " " * 60000 + "y")
    assert time.monotonic() - start < 1.0
