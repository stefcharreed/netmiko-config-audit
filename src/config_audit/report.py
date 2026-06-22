"""Structured reporting: emit a JSON summary of a run.

JSON output (not just console text) is a deliberate seam: a later AI correlation
layer will read these reports — alongside syslog events — to produce plain-English
summaries of what changed and why. Keep the schema stable once devices depend on it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class RunReport:
    timestamp: str
    devices_total: int = 0
    devices_ok: int = 0
    devices_failed: int = 0
    drifted: list[str] = field(default_factory=list)   # device names with drift
    failures: dict = field(default_factory=dict)        # device name -> error string


def build_report(results, drift_results) -> RunReport:
    """Assemble a RunReport from collection + drift results.

    Args:
        results:       list[CollectionResult] from collector.collect_all()
        drift_results: list[DriftResult] from drift.compare_to_golden()

    TODO:
        - Count totals / ok / failed from `results`.
        - Populate `drifted` from drift_results where has_drift is True.
        - Populate `failures` from any CollectionResult where ok is False.
    """
    raise NotImplementedError


def write_report(report: RunReport, report_dir: Path) -> Path:
    """Write the report to report_dir/run-<timestamp>.json and return the path."""
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = report_dir / f"run-{stamp}.json"
    path.write_text(json.dumps(asdict(report), indent=2))
    return path
