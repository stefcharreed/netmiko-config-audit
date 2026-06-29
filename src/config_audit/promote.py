"""Promote a device's current backup into its baseline (v1.1).

Human-gated, deterministic, no AI, no device write. The lifecycle:

    backup   pull running-config -> backup_dir/<device>.cfg     (actual state)
    diff     review drift vs baseline_dir/<device>.cfg          (intended state)
    promote  bless the reviewed backup AS the new baseline       (this module)

Promote operates on the ON-DISK backup -- what `backup` last wrote -- NOT a fresh
live pull. You promote exactly what you reviewed in `diff`; re-run `backup` first
if you want to capture newer state. Keeping it file-based means promote has no
live-device dependency and no time-of-check/time-of-use gap, and it stays unit-
testable with no gear (same discipline as the `diff` command).

Separation of concerns mirrors the rest of the package (cf. report.py, sanitize_
check.py): the analysis is a pure function that returns *what would change* and
writes nothing; the write is a second function; the human y/N gate, the diff
rendering, and the git commit all live in the CLI caller. Functions report,
callers decide and render.

Note: this is intentionally read-then-write-one-file with a mandatory interactive
gate. There is deliberately NO --yes / auto-approve flag in v1.1 -- the human gate
is the whole point of this slice (D6). A gated, non-interactive promote belongs to
the MCP/automation layer later, where the gate moves into policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .drift import compare_to_baseline


@dataclass
class PromotionPlan:
    """What a promotion *would* do. Produced by plan_promotion(); writes nothing."""
    device: str
    backup_exists: bool          # is there a backup_dir/<device>.cfg to promote at all?
    baseline_exists: bool        # did a baseline already exist?
    is_initial: bool             # True == establishing the very first baseline
    has_drift: bool              # does the backup differ from the baseline (after normalize)?
    diff_lines: list[str]        # unified-diff lines to show the human (empty if in sync)
    current_text: str            # the RAW backup text that would become the new baseline


def _read(path: Path) -> str | None:
    """Return file text, or None if the file does not exist."""
    return path.read_text(encoding="utf-8") if path.exists() else None


def plan_promotion(
    device_name: str, backup_dir: Path, baseline_dir: Path
) -> PromotionPlan:
    """Analyze a promotion without performing it.

    Reads the on-disk backup and the current baseline, computes drift between them
    (normalizing both sides via drift.compare_to_baseline), and reports what would
    happen. No writes, no prompts, no git.
    """
    current = _read(Path(backup_dir) / f"{device_name}.cfg")
    baseline = _read(Path(baseline_dir) / f"{device_name}.cfg")

    # Nothing to promote: no backup has been pulled for this device yet.
    if current is None:
        return PromotionPlan(
            device=device_name, backup_exists=False,
            baseline_exists=baseline is not None, is_initial=False,
            has_drift=False, diff_lines=[], current_text="",
        )

    # First baseline: diff against an empty baseline so the whole (normalized)
    # config shows as the delta being established.
    if baseline is None:
        result = compare_to_baseline(device_name, current, "")
        return PromotionPlan(
            device=device_name, backup_exists=True, baseline_exists=False,
            is_initial=True, has_drift=result.has_drift,
            diff_lines=result.diff_lines, current_text=current,
        )

    result = compare_to_baseline(device_name, current, baseline)
    return PromotionPlan(
        device=device_name, backup_exists=True, baseline_exists=True,
        is_initial=False, has_drift=result.has_drift,
        diff_lines=result.diff_lines, current_text=current,
    )


def apply_promotion(device_name: str, current_text: str, baseline_dir: Path) -> Path:
    """The one write: overwrite baseline_dir/<device>.cfg with current_text.

    Stores the RAW config text (not the normalized line list) -- baselines are real
    .cfg files; normalize() is applied at *compare* time, to both sides, by drift.py.
    Returns the path written. The caller is responsible for the git commit.
    """
    baseline_dir = Path(baseline_dir)
    baseline_dir.mkdir(parents=True, exist_ok=True)
    path = baseline_dir / f"{device_name}.cfg"
    path.write_text(current_text, encoding="utf-8")
    return path
