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


def test_ensure_secrets_file_skips_prompt_when_file_exists(tmp_path, monkeypatch):
    """No prompt at all if secrets.env is already there -- input() would hang otherwise."""
    from config_audit.cli import _ensure_secrets_file

    secrets_path = tmp_path / "secrets.env"
    secrets_path.write_text("NET_USERNAME=existing\n", encoding="utf-8")

    def _boom(*_a, **_k):
        raise AssertionError("should not prompt when secrets.env already exists")

    monkeypatch.setattr("builtins.input", _boom)
    monkeypatch.setattr("getpass.getpass", _boom)

    _ensure_secrets_file(secrets_path)
    assert secrets_path.read_text(encoding="utf-8") == "NET_USERNAME=existing\n"


def test_ensure_secrets_file_prompts_and_writes_when_missing(tmp_path, monkeypatch, capsys):
    """First-run wizard writes username/password; enable secret is optional and skippable."""
    from config_audit.cli import _ensure_secrets_file

    secrets_path = tmp_path / "secrets.env"
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "admin")
    # password, confirm password, then skip enable secret (empty = no confirm prompt)
    passwords = iter(["hunter2", "hunter2", ""])
    monkeypatch.setattr("getpass.getpass", lambda *_a, **_k: next(passwords))

    _ensure_secrets_file(secrets_path)

    content = secrets_path.read_text(encoding="utf-8")
    assert "NET_USERNAME=admin" in content
    assert "NET_PASSWORD=hunter2" in content
    assert "NET_SECRET" not in content
    assert "Wrote" in capsys.readouterr().out


def test_ensure_secrets_file_includes_secret_when_provided(tmp_path, monkeypatch):
    """The enable/secret line is written when the user provides one, confirmed."""
    from config_audit.cli import _ensure_secrets_file

    secrets_path = tmp_path / "secrets.env"
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "admin")
    passwords = iter(["hunter2", "hunter2", "enable123", "enable123"])
    monkeypatch.setattr("getpass.getpass", lambda *_a, **_k: next(passwords))

    _ensure_secrets_file(secrets_path)
    assert "NET_SECRET=enable123" in secrets_path.read_text(encoding="utf-8")


def test_password_mismatch_retries_then_succeeds(monkeypatch, capsys):
    """A mismatched confirmation re-prompts instead of silently accepting either value."""
    from config_audit.cli import _prompt_confirmed_password

    # first pair mismatches, second pair matches
    responses = iter(["typo1", "typo2", "correct", "correct"])
    monkeypatch.setattr("getpass.getpass", lambda *_a, **_k: next(responses))

    result = _prompt_confirmed_password("Default password")

    assert result == "correct"
    assert "Didn't match" in capsys.readouterr().out


def test_password_mismatch_exhausts_attempts_and_exits(monkeypatch):
    """Repeated mismatches abort setup instead of looping forever."""
    from config_audit.cli import _prompt_confirmed_password

    responses = iter(["a", "b", "c", "d", "e", "f"])  # 3 mismatched pairs
    monkeypatch.setattr("getpass.getpass", lambda *_a, **_k: next(responses))

    with pytest.raises(SystemExit) as exit_info:
        _prompt_confirmed_password("Default password")
    assert exit_info.value.code == 1


def test_optional_password_confirmation_skipped_when_blank(monkeypatch):
    """An empty optional value (e.g. skipping the enable secret) needs no confirmation."""
    from config_audit.cli import _prompt_confirmed_password

    monkeypatch.setattr("getpass.getpass", lambda *_a, **_k: "")

    result = _prompt_confirmed_password("Enable secret", optional=True)
    assert result == ""
