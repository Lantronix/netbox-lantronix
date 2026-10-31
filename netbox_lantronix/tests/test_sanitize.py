import pytest

from netbox_lantronix.sanitize import clean_text, safe_name


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("SLC9016-16E-A", "SLC9016-16E-A"),
        ("core_sw1.dc1", "core_sw1.dc1"),
        ("  router1  ", "router1"),
        ("a" * 64, "a" * 64),
        ("a" * 65, None),
        ("", None),
        ("   ", None),
        ("router 1", None),
        ("router1;rm -rf", None),
        ("<b>x</b>", None),
        ("réseau", None),
        ("abc‮dcba", None),
        ("name\n", "name"),
        (None, None),
        (42, None),
    ],
)
def test_safe_name(value, expected):
    assert safe_name(value) == expected


def test_clean_text_none_and_non_string():
    assert clean_text(None, 10) == ""
    assert clean_text(123, 10) == "123"


def test_clean_text_strips_control_chars():
    assert clean_text("a\x00b\x1b[31mc\x7fd", 100) == "a b [31mc d"


def test_clean_text_collapses_whitespace():
    assert clean_text("  host\t\tname\r\n  here  ", 100) == "host name here"


@pytest.mark.parametrize("cp", [*range(0x202A, 0x202F), *range(0x2066, 0x206A)])
def test_clean_text_strips_bidi(cp):
    assert clean_text(f"abc{chr(cp)}def", 100) == "abcdef"


def test_clean_text_keeps_normal_unicode():
    assert clean_text("réseau café", 100) == "réseau café"


def test_clean_text_truncates():
    assert clean_text("abcdefghij", 4) == "abcd"
    assert clean_text("abc def", 4) == "abc"
    assert clean_text("abc", 0) == ""


def test_safe_serial_and_id():
    from netbox_lantronix.sanitize import safe_id, safe_serial

    assert safe_serial(None) == "" and safe_serial("  ") == ""
    assert safe_serial(" 000F2C030B88 ") == "000F2C030B88"
    assert safe_serial("A" * 51) is None and safe_serial("ab cd") is None
    assert safe_serial("x<y") is None
    assert safe_id("00204ARIDH5L84WT0IHZ8TEMXLLAZFKQ") == "00204ARIDH5L84WT0IHZ8TEMXLLAZFKQ"
    assert safe_id("../etc") is None and safe_id("") is None and safe_id(None) is None


def test_safe_ip_rejects_scoped_and_junk():
    from netbox_lantronix.sanitize import safe_ip

    assert safe_ip(" 198.51.100.75 ") == "198.51.100.75"
    assert safe_ip("2001:db8::1") == "2001:db8::1"
    assert safe_ip("fe80::1%eth0") is None
    assert safe_ip("fe80::1%x@evil.example") is None
    assert safe_ip("192.0.2.1/24") is None and safe_ip("host") is None and safe_ip(None) is None
