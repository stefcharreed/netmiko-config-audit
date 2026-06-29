"""collector pulls running-configs over SSH, but its offline seam (source_text)
lets the whole pipeline be tested with no device. These tests exercise only that
seam — the live Netmiko path needs real gear and is validated by hand.
"""
from config_audit.collector import fetch_running_config, collect_all
from config_audit.inventory import Device


def _device(name: str) -> Device:
    """A throwaway Device; host/creds are unused on the offline path."""
    return Device(name=name, host="192.0.2.1", device_type="cisco_ios",
                  username="x", password="y", secret="")


def test_source_text_returns_ok_without_connecting():
    """Given source_text, fetch returns a successful result and never opens SSH."""
    result = fetch_running_config(_device("ISR1"), source_text="hostname ISR1\n")
    assert result.ok is True
    assert result.device == "ISR1"
    assert result.config_text == "hostname ISR1\n"


def test_source_text_normalizes_crlf_to_lf():
    """Windows line endings in the source are normalized to \\n on the way in."""
    result = fetch_running_config(_device("ISR1"), source_text="line1\r\nline2\r\n")
    assert "\r" not in result.config_text
    assert result.config_text == "line1\nline2\n"


def test_collect_all_maps_source_texts_by_device_name():
    """collect_all routes each device to its own source_text via a {name: text} map."""
    devices = [_device("ISR1"), _device("CAT1")]
    texts = {"ISR1": "hostname ISR1", "CAT1": "hostname CAT1"}
    results = collect_all(devices, source_texts=texts)
    by_name = {r.device: r.config_text for r in results}
    assert by_name == {"ISR1": "hostname ISR1", "CAT1": "hostname CAT1"}


def test_collect_all_preserves_device_order():
    """Results come back in the same order as the input device list."""
    devices = [_device("A"), _device("B"), _device("C")]
    texts = {"A": "a", "B": "b", "C": "c"}
    assert [r.device for r in collect_all(devices, source_texts=texts)] == ["A", "B", "C"]
