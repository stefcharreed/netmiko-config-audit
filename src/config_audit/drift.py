"""Drift detection: compare a device's current config against its baseline.

Baseline = intended state (per-device approved config). Current = actual state.
Drift = the meaningful diff after normalizing BOTH sides identically.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

from .normalize import normalize


@dataclass
class DriftResult:
    device: str
    has_drift: bool
    diff_lines: list[str]


def load_baseline(baseline_dir: Path, device_name: str) -> str:
    """Read the per-device baseline, or '' if none exists yet."""
    path = baseline_dir / f"{device_name}.cfg"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def compare_to_baseline(device_name: str, current: str, baseline: str) -> DriftResult:
    """Unified diff of baseline vs current, after identical normalization of both."""
    base_lines = normalize(baseline)
    curr_lines = normalize(current)

    if base_lines == curr_lines:                      # short-circuit: in sync
        return DriftResult(device=device_name, has_drift=False, diff_lines=[])

    diff = list(
        difflib.unified_diff(
            base_lines, curr_lines,
            fromfile=f"{device_name} baseline", tofile=f"{device_name} current",
            lineterm="",
        )
    )
    # A real change is a +/- line that isn't the +++/--- file headers.
    meaningful = [
        d for d in diff
        if (d.startswith("+") or d.startswith("-")) and not d.startswith(("+++", "---"))
    ]
    return DriftResult(device=device_name, has_drift=bool(meaningful), diff_lines=diff)
