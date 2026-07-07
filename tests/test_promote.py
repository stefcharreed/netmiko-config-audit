"""promote (v1.1): plan_promotion is pure analysis, apply_promotion is the single
write. These tests cover the four promotion states and the write roundtrip — no
gear and no stdin, because the human gate and rendering live in the CLI.
"""
from pathlib import Path

from config_audit.promote import apply_promotion, plan_promotion

FIXTURES = Path(__file__).parent / "fixtures"


def _fx(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _dirs(tmp_path: Path):
    """Create backup + baseline dirs inside the test's temp dir."""
    backup, baseline = tmp_path / "backups", tmp_path / "baselines"
    backup.mkdir()
    baseline.mkdir()
    return backup, baseline


def test_no_backup_means_nothing_to_promote(tmp_path):
    """With no backup file for the device, the plan reports nothing to promote."""
    backup, baseline = _dirs(tmp_path)
    plan = plan_promotion("ISR1", backup, baseline)
    assert plan.backup_exists is False
    assert plan.has_drift is False


def test_initial_baseline_is_all_drift(tmp_path):
    """A backup with no existing baseline is the initial-baseline case: all drift."""
    backup, baseline = _dirs(tmp_path)
    (backup / "ISR1.cfg").write_text(_fx("ISR1_baseline.cfg"), encoding="utf-8")
    plan = plan_promotion("ISR1", backup, baseline)
    assert plan.backup_exists is True
    assert plan.baseline_exists is False
    assert plan.is_initial is True
    assert plan.has_drift is True
    assert plan.current_text == _fx("ISR1_baseline.cfg")  # raw text, unmodified


def test_in_sync_reports_no_drift(tmp_path):
    """When backup and baseline match, the plan reports in-sync."""
    backup, baseline = _dirs(tmp_path)
    (backup / "ISR1.cfg").write_text(_fx("ISR1_baseline.cfg"), encoding="utf-8")
    (baseline / "ISR1.cfg").write_text(_fx("ISR1_baseline.cfg"), encoding="utf-8")
    plan = plan_promotion("ISR1", backup, baseline)
    assert plan.is_initial is False
    assert plan.has_drift is False


def test_noise_only_backup_is_not_drift(tmp_path):
    """A backup differing only in volatile lines reads as in-sync, so promote won't churn."""
    backup, baseline = _dirs(tmp_path)
    (backup / "ISR1.cfg").write_text(_fx("ISR1_current_clean.cfg"), encoding="utf-8")
    (baseline / "ISR1.cfg").write_text(_fx("ISR1_baseline.cfg"), encoding="utf-8")
    assert plan_promotion("ISR1", backup, baseline).has_drift is False


def test_real_drift_is_detected(tmp_path):
    """A genuinely changed backup is reported as drift with a non-empty diff."""
    backup, baseline = _dirs(tmp_path)
    (backup / "ISR1.cfg").write_text(_fx("ISR1_current_drift.cfg"), encoding="utf-8")
    (baseline / "ISR1.cfg").write_text(_fx("ISR1_baseline.cfg"), encoding="utf-8")
    plan = plan_promotion("ISR1", backup, baseline)
    assert plan.has_drift is True
    assert plan.diff_lines


def test_apply_writes_baseline_and_roundtrips_to_in_sync(tmp_path):
    """apply_promotion writes the raw backup as the new baseline; a re-plan is then in-sync."""
    backup, baseline = _dirs(tmp_path)
    (backup / "ISR1.cfg").write_text(_fx("ISR1_current_drift.cfg"), encoding="utf-8")
    (baseline / "ISR1.cfg").write_text(_fx("ISR1_baseline.cfg"), encoding="utf-8")

    plan = plan_promotion("ISR1", backup, baseline)
    path = apply_promotion("ISR1", plan.current_text, baseline)

    assert path == baseline / "ISR1.cfg"
    assert path.read_text(encoding="utf-8") == _fx("ISR1_current_drift.cfg")
    assert plan_promotion("ISR1", backup, baseline).has_drift is False
