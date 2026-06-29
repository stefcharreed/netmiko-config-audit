"""cli wires the pieces together. These tests drive main() the way a user would —
through argv — covering argument dispatch, the file-based diff exit codes, and the
promote human-gate. The promote write/commit path is covered in test_promote and
test_gitstore, so here we only confirm the gate refuses on 'n'.
"""
from pathlib import Path

import pytest

from config_audit.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def _project(tmp_path: Path, backup_fixture: str, baseline_fixture: str = "ISR1_baseline.cfg") -> Path:
    """Build a temp project: config.yaml + populated backup/baseline dirs. Returns the config path."""
    backup, baseline, reports = tmp_path / "backups", tmp_path / "baselines", tmp_path / "reports"
    backup.mkdir(); baseline.mkdir()
    (backup / "ISR1.cfg").write_text((FIXTURES / backup_fixture).read_text(), encoding="utf-8")
    (baseline / "ISR1.cfg").write_text((FIXTURES / baseline_fixture).read_text(), encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        "settings:\n"
        f"  backup_dir: {backup}\n"
        f"  baseline_dir: {baseline}\n"
        f"  report_path: {reports}\n"
        "devices:\n"
        "  - name: ISR1\n"
        "    host: 192.0.2.1\n"
        "    device_type: cisco_ios\n",
        encoding="utf-8",
    )
    return config


def test_missing_subcommand_exits_with_code_2(capsys):
    """No subcommand is a usage error: argparse exits 2."""
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code == 2


def test_diff_in_sync_returns_zero(tmp_path, capsys):
    """When backup matches baseline, diff reports the device ok and exits 0."""
    config = _project(tmp_path, backup_fixture="ISR1_current_clean.cfg")
    code = main(["-c", str(config), "diff"])
    assert code == 0
    assert "ok" in capsys.readouterr().out


def test_diff_detects_drift_returns_one(tmp_path, capsys):
    """A drifted backup makes diff print DRIFT and exit 1."""
    config = _project(tmp_path, backup_fixture="ISR1_current_drift.cfg")
    code = main(["-c", str(config), "diff"])
    assert code == 1
    assert "DRIFT" in capsys.readouterr().out


def test_promote_aborts_on_no_and_leaves_baseline_untouched(tmp_path, capsys, monkeypatch):
    """Answering 'n' at the gate exits 1 and does not modify the baseline file."""
    config = _project(tmp_path, backup_fixture="ISR1_current_drift.cfg")
    baseline_file = tmp_path / "baselines" / "ISR1.cfg"
    before = baseline_file.read_text(encoding="utf-8")

    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
    code = main(["-c", str(config), "promote", "ISR1"])

    assert code == 1
    assert "aborted" in capsys.readouterr().out.lower()
    assert baseline_file.read_text(encoding="utf-8") == before  # unchanged
