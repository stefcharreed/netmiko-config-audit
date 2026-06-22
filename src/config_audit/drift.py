"""Drift detection: compare a device's current config against its golden baseline.

Core logic worth writing yourself. Golden baseline = intended state; current
backup = actual state; drift = the meaningful diff between them.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class DriftResult:
    device: str
    has_drift: bool
    diff_lines: list[str]


def load_golden(golden_dir: Path, device_name: str) -> str:
    """Read the golden baseline for a device, or '' if none exists yet."""
    path = golden_dir / f"{device_name}.cfg"
    return path.read_text() if path.exists() else ""


def compare_to_golden(device_name: str, current: str, golden: str) -> DriftResult:
    """Return a unified diff between the golden baseline and the current config.

    TODO:
        - Use difflib.unified_diff(golden.splitlines(), current.splitlines(), ...).
        - Filter cosmetic noise so it doesn't read as real drift, e.g.:
            * `! Last configuration change ...`
            * `! NVRAM config last updated ...`
            * uptime/clock lines
        - has_drift = any meaningful diff lines remain after filtering.
    """
    raise NotImplementedError
