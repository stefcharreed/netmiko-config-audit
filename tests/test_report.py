"""report tallies collection + drift results into the JSON schema a later AI layer
will read. These tests pin the tally and confirm the written file is valid JSON,
since anything downstream depends on that schema staying stable.
"""
import json
from pathlib import Path

from config_audit.collector import CollectionResult
from config_audit.drift import DriftResult
from config_audit.report import build_report, write_report


def test_counts_ok_failed_and_drift():
    """build_report tallies totals, ok/failed counts, drifted names, and failures."""
    results = [
        CollectionResult(device="ISR1", ok=True, config_text="hostname ISR1"),
        CollectionResult(device="CAT1", ok=True, config_text="hostname CAT1"),
        CollectionResult(device="SG1", ok=False, error="timed out"),
    ]
    drift_results = [
        DriftResult(device="ISR1", has_drift=True, diff_lines=["+x"]),
        DriftResult(device="CAT1", has_drift=False, diff_lines=[]),
    ]
    rep = build_report(results, drift_results)
    assert rep.devices_total == 3
    assert rep.devices_ok == 2
    assert rep.devices_failed == 1
    assert rep.drifted == ["ISR1"]
    assert rep.failures == {"SG1": "timed out"}


def test_empty_run_reports_all_zero():
    """An empty run produces zero counts and empty collections, not errors."""
    rep = build_report([], [])
    assert rep.devices_total == 0
    assert rep.devices_ok == 0
    assert rep.devices_failed == 0
    assert rep.drifted == []
    assert rep.failures == {}


def test_write_report_emits_valid_json(tmp_path):
    """write_report serializes the report to a run-*.json file that parses back cleanly."""
    results = [CollectionResult(device="ISR1", ok=True, config_text="x")]
    drift_results = [DriftResult(device="ISR1", has_drift=True, diff_lines=["+x"])]
    rep = build_report(results, drift_results)

    path = write_report(rep, tmp_path)
    assert path.parent == tmp_path
    assert path.name.startswith("run-") and path.suffix == ".json"

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["devices_total"] == 1
    assert data["drifted"] == ["ISR1"]
