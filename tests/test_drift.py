"""Drift = the meaningful diff after normalizing both sides. These tests pin the
two behaviours that matter most: volatile noise must NOT register as drift, and
every kind of real change MUST.
"""
from pathlib import Path

from config_audit.drift import compare_to_baseline

FIXTURES = Path(__file__).parent / "fixtures"


def _fx(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _has(diff_lines, sign: str, needle: str) -> bool:
    """True if some diff line starts with sign (+/-) and contains needle."""
    return any(l.startswith(sign) and needle in l for l in diff_lines)


def test_noise_only_is_not_drift_isr():
    """A backup differing from baseline only in volatile lines reports no drift."""
    r = compare_to_baseline("ISR1", _fx("ISR1_current_clean.cfg"), _fx("ISR1_baseline.cfg"))
    assert r.has_drift is False
    assert r.diff_lines == []


def test_noise_only_is_not_drift_cat():
    """Same no-false-drift guarantee for the Catalyst pair."""
    r = compare_to_baseline("CAT1", _fx("CAT1_current_clean.cfg"), _fx("CAT1_baseline.cfg"))
    assert r.has_drift is False


def test_identical_config_no_drift():
    """Comparing a config against itself is trivially in-sync."""
    base = _fx("ISR1_baseline.cfg")
    r = compare_to_baseline("ISR1", base, base)
    assert r.has_drift is False
    assert r.diff_lines == []


def test_modify_and_add_is_drift():
    """An edited description and an added ACL line both surface in the diff."""
    r = compare_to_baseline("ISR1", _fx("ISR1_current_drift.cfg"), _fx("ISR1_baseline.cfg"))
    assert r.has_drift is True
    assert _has(r.diff_lines, "+", "permit tcp host 198.51.100.51 any eq 443")
    assert _has(r.diff_lines, "-", "description LAN-SEGMENT-A")
    assert _has(r.diff_lines, "+", "description LAN-SEGMENT-A-PRINTERS")


def test_removed_line_is_drift():
    """Removing a line (here passive-interface) is detected as drift."""
    r = compare_to_baseline("ISR1", _fx("ISR1_current_removed.cfg"), _fx("ISR1_baseline.cfg"))
    assert r.has_drift is True
    assert _has(r.diff_lines, "-", "passive-interface Loopback0")


def test_vlan_move_is_drift():
    """A switchport access VLAN change shows as a removal plus an addition."""
    r = compare_to_baseline("CAT1", _fx("CAT1_current_drift.cfg"), _fx("CAT1_baseline.cfg"))
    assert r.has_drift is True
    assert _has(r.diff_lines, "-", "switchport access vlan 10")
    assert _has(r.diff_lines, "+", "switchport access vlan 20")


def test_empty_baseline_is_all_drift():
    """The first-baseline case (empty baseline) makes the whole config an addition."""
    r = compare_to_baseline("ISR1", _fx("ISR1_baseline.cfg"), "")
    assert r.has_drift is True
    assert _has(r.diff_lines, "+", "hostname ISR1")
