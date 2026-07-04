"""push: plan_push is pure analysis (no SSH), apply_push/save_running_config are
the only functions that write to a device. These tests cover plan_push's states
using the same fixtures as test_drift/test_promote -- no gear and no stdin, since
the human gates and rendering live in the CLI.
"""
from pathlib import Path

from config_audit.push import plan_push

FIXTURES = Path(__file__).parent / "fixtures"


def _fx(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _baseline_dir(tmp_path: Path) -> Path:
    baseline = tmp_path / "baselines"
    baseline.mkdir()
    return baseline


def test_no_baseline_means_nothing_to_push(tmp_path):
    """With no baseline for the device, the plan reports nothing to push."""
    baseline = _baseline_dir(tmp_path)
    plan = plan_push("ISR1", baseline, live_config="hostname ISR1\n")
    assert plan.baseline_exists is False
    assert plan.no_changes is True
    assert plan.config_lines == []


def test_live_matching_baseline_is_no_changes(tmp_path):
    """When the live config already matches the baseline, nothing would be sent."""
    baseline = _baseline_dir(tmp_path)
    (baseline / "ISR1.cfg").write_text(_fx("ISR1_baseline.cfg"), encoding="utf-8")
    plan = plan_push("ISR1", baseline, live_config=_fx("ISR1_current_clean.cfg"))
    assert plan.baseline_exists is True
    assert plan.no_changes is True
    assert plan.config_lines == []


def test_drifted_live_produces_config_lines_and_diff(tmp_path):
    """A live config that has drifted from the baseline produces the full baseline
    as config_lines to send, plus a non-empty diff for human review."""
    baseline = _baseline_dir(tmp_path)
    (baseline / "ISR1.cfg").write_text(_fx("ISR1_baseline.cfg"), encoding="utf-8")
    plan = plan_push("ISR1", baseline, live_config=_fx("ISR1_current_drift.cfg"))
    assert plan.baseline_exists is True
    assert plan.no_changes is False
    assert plan.diff_lines
    assert plan.config_lines  # non-empty: this is what would be sent
    # no blank lines -- send_config_set doesn't need them and they add noise
    assert all(line.strip() for line in plan.config_lines)
