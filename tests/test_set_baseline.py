"""set_baseline: plan_set_baseline is pure analysis of an arbitrary file against
the current baseline -- no live device pull needed. This covers the ZTP path:
authoring a baseline before any device exists to promote from. The actual write
is promote.apply_promotion, already covered in test_promote.py.
"""
from pathlib import Path

from config_audit.set_baseline import plan_set_baseline

FIXTURES = Path(__file__).parent / "fixtures"


def _fx_path(name: str) -> Path:
    return FIXTURES / name


def _baseline_dir(tmp_path: Path) -> Path:
    baseline = tmp_path / "baselines"
    baseline.mkdir()
    return baseline


def test_no_existing_baseline_is_initial(tmp_path):
    """Authoring a baseline where none exists yet is the ZTP case: initial, all drift."""
    baseline = _baseline_dir(tmp_path)
    plan = plan_set_baseline("SW1", _fx_path("ISR1_baseline.cfg"), baseline)
    assert plan.baseline_exists is False
    assert plan.is_initial is True
    assert plan.has_drift is True
    assert plan.source_text == _fx_path("ISR1_baseline.cfg").read_text(encoding="utf-8")


def test_source_matching_existing_baseline_is_no_drift(tmp_path):
    """Re-authoring from a file identical to the existing baseline reports in-sync."""
    baseline = _baseline_dir(tmp_path)
    (baseline / "SW1.cfg").write_text(
        _fx_path("ISR1_baseline.cfg").read_text(encoding="utf-8"), encoding="utf-8"
    )
    plan = plan_set_baseline("SW1", _fx_path("ISR1_baseline.cfg"), baseline)
    assert plan.baseline_exists is True
    assert plan.is_initial is False
    assert plan.has_drift is False


def test_source_differing_from_existing_baseline_shows_drift(tmp_path):
    """A source file that differs from the current baseline reports drift with a diff."""
    baseline = _baseline_dir(tmp_path)
    (baseline / "SW1.cfg").write_text(
        _fx_path("ISR1_baseline.cfg").read_text(encoding="utf-8"), encoding="utf-8"
    )
    plan = plan_set_baseline("SW1", _fx_path("ISR1_current_drift.cfg"), baseline)
    assert plan.has_drift is True
    assert plan.diff_lines
