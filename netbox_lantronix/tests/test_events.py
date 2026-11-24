# Shared code: this file is the same in netbox-lantronix and nautobot-app-ssot-lantronix.
# scripts/check_shared.py fails CI when the copies differ, so change both.
"""Signed event verification and envelope parsing (webhook-payload v1).

The bodies and signatures are percepxion-integration-kit's contract fixtures, so this copy of
the check agrees with the reference sender byte for byte."""

import json
from pathlib import Path

import pytest

from netbox_lantronix.percepxion.events import (
    MAX_BODY,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    Event,
    EventRejected,
    parse,
    sign,
    usable_secrets,
    verify,
)

FIXTURES = Path(__file__).parent / "fixtures" / "webhooks"
VECTORS = json.loads((FIXTURES / "vectors.json").read_text())
SECRET = VECTORS["secret"]
TS = VECTORS["timestamp"]


def body(name):
    return (FIXTURES / f"{name}.body.json").read_bytes()


def headers(sig, ts=TS):
    return {TIMESTAMP_HEADER: str(ts), SIGNATURE_HEADER: sig}


@pytest.mark.parametrize("vector", VECTORS["vectors"], ids=lambda v: v["fixture"])
def test_reference_signatures_verify(vector):
    assert verify([SECRET], headers(vector["signature"]), body(vector["fixture"]), now=TS) == TS


def test_sign_matches_the_reference_sender():
    for vector in VECTORS["vectors"]:
        got = sign(SECRET, body(vector["fixture"]), timestamp=TS)[SIGNATURE_HEADER]
        assert got == vector["signature"]


def test_rotation_any_secret_any_signature():
    old, new = "o" * 32, "n" * 32
    b = body("port-attached")
    h = sign([new, old], b, timestamp=TS)
    assert verify([old], h, b, now=TS) == TS
    assert verify([new], h, b, now=TS) == TS


def test_header_names_are_case_insensitive():
    b = body("port-attached")
    h = {k.lower(): v for k, v in sign(SECRET, b, timestamp=TS).items()}
    assert verify([SECRET], h, b, now=TS) == TS


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda b, h: (b + b" ", h), "signature mismatch"),
        (lambda b, h: (b, {**h, TIMESTAMP_HEADER: str(TS + 1)}), "signature mismatch"),
        (lambda b, h: (b, {SIGNATURE_HEADER: h[SIGNATURE_HEADER]}), "missing"),
        (lambda b, h: (b, {**h, SIGNATURE_HEADER: "v2=abc"}), "no v1 signature"),
        (lambda b, h: (b, {**h, TIMESTAMP_HEADER: "soon"}), "not an integer"),
    ],
)
def test_tampered_or_malformed_requests_are_rejected(mutate, reason):
    b, h = mutate(body("port-attached"), sign(SECRET, body("port-attached"), timestamp=TS))
    with pytest.raises(EventRejected, match=reason):
        verify([SECRET], h, b, now=TS)


def test_stale_and_future_timestamps_are_rejected():
    b = body("port-attached")
    h = sign(SECRET, b, timestamp=TS)
    for now in (TS + 301, TS - 301):
        with pytest.raises(EventRejected, match="window"):
            verify([SECRET], h, b, now=now)
    assert verify([SECRET], h, b, now=TS + 300) == TS


def test_no_secret_and_oversized_body_are_rejected():
    b = body("port-attached")
    with pytest.raises(EventRejected, match="no webhook secret"):
        verify([], sign(SECRET, b, timestamp=TS), b, now=TS)
    big = b" " * (MAX_BODY + 1)
    with pytest.raises(EventRejected, match="too large"):
        verify([SECRET], sign(SECRET, big, timestamp=TS), big, now=TS)


def test_rejection_messages_never_echo_secrets():
    b = body("port-attached")
    with pytest.raises(EventRejected) as exc:
        verify(["s" * 40], sign("x" * 40, b, timestamp=TS), b, now=TS)
    assert "s" * 8 not in str(exc.value) and "x" * 8 not in str(exc.value)


@pytest.mark.parametrize(
    "name, event_type",
    [
        ("port-attached", "percepxion.port.attached_changed"),
        ("port-detached", "percepxion.port.attached_changed"),
        ("port-state-changed", "percepxion.port.state_changed"),
        ("device-status-changed", "percepxion.device.status_changed"),
    ],
)
def test_sync_events_parse_to_ids_only(name, event_type):
    raw = json.loads(body(name))
    ev = parse(body(name))
    assert ev == Event(raw["id"], event_type, raw["tenant_id"], raw["device"]["id"])


def test_other_event_types_are_ignored():
    assert parse(body("rule-triggered")) is None


@pytest.mark.parametrize(
    "raw, reason",
    [
        (b"not json", "not JSON"),
        (b"[]", "v1 envelope"),
        (b'{"spec_version": "2"}', "v1 envelope"),
        (b'{"spec_version": "1", "type": "percepxion.port.attached_changed"}', "missing"),
    ],
)
def test_malformed_envelopes_are_rejected(raw, reason):
    with pytest.raises(EventRejected, match=reason):
        parse(raw)


def test_device_id_must_have_the_expected_shape():
    raw = json.loads(body("port-attached"))
    for bad in (None, "", "x" * 65, "abc;rm", 42):
        raw["device"] = {"id": bad}
        with pytest.raises(EventRejected, match="device id"):
            parse(json.dumps(raw).encode())
    raw["device"] = None
    with pytest.raises(EventRejected, match="device id"):
        parse(json.dumps(raw).encode())


def test_usable_secrets():
    long = "a" * 32
    assert usable_secrets(None) == []
    assert usable_secrets("") == []
    assert usable_secrets([long, " " + long + " "]) == [long, long]
    assert usable_secrets(f"{long}, {long}b") == [long, long + "b"]
    with pytest.raises(ValueError, match="at least 32"):
        usable_secrets(["short"])
    with pytest.raises(ValueError, match="list of strings"):
        usable_secrets({"a": 1})
