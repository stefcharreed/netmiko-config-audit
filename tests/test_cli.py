"""cli wires the pieces together. These tests drive main() the way a user would —
through argv — covering argument dispatch, the file-based diff exit codes, and the
promote human-gate. The promote write/commit path is covered in test_promote and
test_gitstore, so here we only confirm the gate refuses on 'n'.
"""
import subprocess
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


def _force_interactive(monkeypatch, value: bool = True) -> None:
    """Wizards check _interactive() (sys.stdin.isatty()) before prompting -- pytest's
    captured stdin is never a tty, so tests that exercise the actual prompt flow must
    force this True. Tests that check the non-interactive/cron-safe path pass False.
    """
    monkeypatch.setattr("config_audit.cli._interactive", lambda: value)


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


def test_ensure_secrets_file_leaves_file_untouched_when_declining_reentry(tmp_path, monkeypatch):
    """Answering 'n' (or the [y/N] default) to the re-entry prompt makes no changes."""
    from config_audit.cli import _ensure_secrets_file

    _force_interactive(monkeypatch)
    secrets_path = tmp_path / "secrets.env"
    secrets_path.write_text("NET_USERNAME=existing\n", encoding="utf-8")

    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "n")

    def _boom(*_a, **_k):
        raise AssertionError("should not prompt for credentials when declining re-entry")

    monkeypatch.setattr("getpass.getpass", _boom)

    _ensure_secrets_file(secrets_path)
    assert secrets_path.read_text(encoding="utf-8") == "NET_USERNAME=existing\n"


def test_ensure_secrets_file_reenters_when_confirmed(tmp_path, monkeypatch):
    """Answering 'y' to the re-entry prompt overwrites the existing secrets.env."""
    from config_audit.cli import _ensure_secrets_file

    _force_interactive(monkeypatch)
    secrets_path = tmp_path / "secrets.env"
    secrets_path.write_text("NET_USERNAME=oldvalue\n", encoding="utf-8")

    inputs = iter(["y", "newuser"])  # confirm re-entry, then the new username
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(inputs))
    passwords = iter(["newpass", "newpass", ""])  # password, confirm, skip enable secret
    monkeypatch.setattr("getpass.getpass", lambda *_a, **_k: next(passwords))

    _ensure_secrets_file(secrets_path)

    content = secrets_path.read_text(encoding="utf-8")
    assert "NET_USERNAME=newuser" in content
    assert "NET_PASSWORD=newpass" in content
    assert "oldvalue" not in content


def test_ensure_secrets_file_prompts_and_writes_when_missing(tmp_path, monkeypatch, capsys):
    """First-run wizard writes username/password; enable secret is optional and skippable."""
    from config_audit.cli import _ensure_secrets_file

    _force_interactive(monkeypatch)
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

    _force_interactive(monkeypatch)
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


def test_required_password_blank_is_rejected_then_retries(monkeypatch, capsys):
    """A blank required password re-prompts instead of writing an empty credential."""
    from config_audit.cli import _prompt_confirmed_password

    responses = iter(["", "realpass", "realpass"])
    monkeypatch.setattr("getpass.getpass", lambda *_a, **_k: next(responses))

    result = _prompt_confirmed_password("Default password")

    assert result == "realpass"
    assert "Can't be blank" in capsys.readouterr().out


@pytest.mark.parametrize(
    "bad_value",
    ["p@ss #word", "trailing   ", "embedded\nnewline"],
    ids=["space-hash", "trailing-whitespace", "newline"],
)
def test_password_shapes_dotenv_would_corrupt_are_rejected(monkeypatch, capsys, bad_value):
    """Values python-dotenv silently mangles are rejected before they reach secrets.env."""
    from config_audit.cli import _prompt_confirmed_password

    responses = iter([bad_value, bad_value, "safepass", "safepass"])
    monkeypatch.setattr("getpass.getpass", lambda *_a, **_k: next(responses))

    result = _prompt_confirmed_password("Default password")

    assert result == "safepass"
    assert capsys.readouterr().out  # some rejection message was printed


def test_invalid_secret_reason_accepts_ordinary_passwords():
    """Sanity check: normal-looking passwords aren't flagged as unsafe."""
    from config_audit.cli import _invalid_secret_reason

    assert _invalid_secret_reason("Tr0ub4dor&3") is None
    assert _invalid_secret_reason("p@ssword#123") is None  # '#' with no preceding space is fine


def test_ensure_secrets_file_noninteractive_missing_fails_clearly(tmp_path, monkeypatch, capsys):
    """No stdin + missing secrets.env -> a clear error and SystemExit, never EOFError."""
    from config_audit.cli import _ensure_secrets_file

    _force_interactive(monkeypatch, False)

    def _boom(*_a, **_k):
        raise AssertionError("must not prompt when non-interactive")

    monkeypatch.setattr("builtins.input", _boom)
    monkeypatch.setattr("getpass.getpass", _boom)

    with pytest.raises(SystemExit) as exit_info:
        _ensure_secrets_file(tmp_path / "secrets.env")
    assert exit_info.value.code == 1
    assert "not found" in " ".join(capsys.readouterr().out.split())


def test_ensure_secrets_file_noninteractive_existing_proceeds_silently(tmp_path, monkeypatch):
    """No stdin + secrets.env already exists -> proceed with no prompt at all.

    This is the cron-safety regression test: a scheduled backup run must never hang
    or crash just because the re-entry prompt exists for interactive users.
    """
    from config_audit.cli import _ensure_secrets_file

    _force_interactive(monkeypatch, False)
    secrets_path = tmp_path / "secrets.env"
    secrets_path.write_text("NET_USERNAME=existing\n", encoding="utf-8")

    def _boom(*_a, **_k):
        raise AssertionError("must not prompt when non-interactive")

    monkeypatch.setattr("builtins.input", _boom)
    monkeypatch.setattr("getpass.getpass", _boom)

    _ensure_secrets_file(secrets_path)  # must not raise
    assert secrets_path.read_text(encoding="utf-8") == "NET_USERNAME=existing\n"


def test_ensure_config_file_skips_when_present(tmp_path, monkeypatch):
    """An existing config.yaml is never auto-reprompted, interactive or not."""
    from config_audit.cli import _ensure_config_file

    config_path = tmp_path / "config.yaml"
    config_path.write_text("settings: {}\ndevices: []\n", encoding="utf-8")

    def _boom(*_a, **_k):
        raise AssertionError("should not prompt when config.yaml already exists")

    monkeypatch.setattr("builtins.input", _boom)

    _ensure_config_file(config_path)
    assert config_path.read_text(encoding="utf-8") == "settings: {}\ndevices: []\n"


def test_ensure_config_file_noninteractive_missing_fails_clearly(tmp_path, monkeypatch, capsys):
    """No stdin + missing config.yaml -> a clear error and SystemExit, never a traceback."""
    from config_audit.cli import _ensure_config_file

    _force_interactive(monkeypatch, False)

    def _boom(*_a, **_k):
        raise AssertionError("must not prompt when non-interactive")

    monkeypatch.setattr("builtins.input", _boom)

    with pytest.raises(SystemExit) as exit_info:
        _ensure_config_file(tmp_path / "config.yaml")
    assert exit_info.value.code == 1
    assert "not found" in " ".join(capsys.readouterr().out.split())


def test_prompt_directory_rejects_blank_then_succeeds(tmp_path, monkeypatch):
    from config_audit.cli import _prompt_directory

    target = tmp_path / "backups"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)

    responses = iter(["", str(target)])
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(responses))

    result = _prompt_directory("Backup directory", require_git=True, code_repo_root=None)
    assert result == target.resolve()


def test_prompt_directory_rejects_path_inside_code_repo(tmp_path, monkeypatch, capsys):
    """A path resolving inside the code repo is rejected -- must be a SEPARATE repo."""
    from config_audit.cli import _prompt_directory

    code_repo_root = tmp_path / "netmiko-config-audit"
    code_repo_root.mkdir()
    bad_path = code_repo_root / "backups"
    good_path = tmp_path / "netmiko-config-private" / "snapshots"
    good_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(good_path)], check=True)

    responses = iter([str(bad_path), str(good_path)])
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(responses))

    result = _prompt_directory(
        "Backup directory", require_git=True, code_repo_root=code_repo_root.resolve()
    )
    assert result == good_path.resolve()
    assert "same code repo" in capsys.readouterr().out


def test_prompt_directory_rejects_non_git_directory(tmp_path, monkeypatch, capsys):
    """An existing plain (non-git) directory is rejected when require_git=True."""
    from config_audit.cli import _prompt_directory

    not_a_repo = tmp_path / "plain-dir"
    not_a_repo.mkdir()
    real_repo = tmp_path / "real-repo"
    real_repo.mkdir()
    subprocess.run(["git", "init", "-q", str(real_repo)], check=True)

    responses = iter([str(not_a_repo), str(real_repo)])
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(responses))

    result = _prompt_directory("Baseline directory", require_git=True, code_repo_root=None)
    assert result == real_repo.resolve()
    assert "isn't a git repository" in " ".join(capsys.readouterr().out.split())


def test_prompt_directory_offers_to_create_missing_dir(tmp_path, monkeypatch):
    """A path that doesn't exist yet is created only if the user confirms."""
    from config_audit.cli import _prompt_directory

    new_dir = tmp_path / "does-not-exist-yet"
    inputs = iter([str(new_dir), "y"])  # the path, then confirm creation
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(inputs))

    result = _prompt_directory("Report directory", require_git=False, code_repo_root=None)
    assert result == new_dir.resolve()
    assert new_dir.exists()


def test_prompt_devices_collects_until_blank_name(monkeypatch):
    from config_audit.cli import _prompt_devices

    inputs = iter(["ISR1", "192.0.2.1", "cisco_ios", "CAT1", "192.0.2.2", "", ""])
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(inputs))

    devices = _prompt_devices()
    assert devices == [
        {"name": "ISR1", "host": "192.0.2.1", "device_type": "cisco_ios"},
        {"name": "CAT1", "host": "192.0.2.2", "device_type": "cisco_ios"},
    ]


def test_run_config_wizard_writes_valid_yaml(tmp_path, monkeypatch):
    """End-to-end: the wizard writes a config.yaml that inventory.load_config can read."""
    from config_audit.cli import _run_config_wizard

    backup_dir = tmp_path / "private" / "snapshots"
    baseline_dir = tmp_path / "private" / "baselines"
    report_dir = tmp_path / "private" / "reports"
    backup_dir.mkdir(parents=True)
    baseline_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(tmp_path / "private")], check=True)

    inputs = iter([
        str(backup_dir), str(baseline_dir), str(report_dir), "y",  # confirm creating report_dir
        "ISR1", "192.0.2.1", "cisco_ios", "",
    ])
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(inputs))

    config_path = tmp_path / "config.yaml"
    _run_config_wizard(config_path)

    from config_audit.inventory import load_config
    cfg = load_config(config_path, secrets_path=tmp_path / "nonexistent.env")
    assert cfg.settings.backup_dir == backup_dir.resolve()
    assert cfg.settings.baseline_dir == baseline_dir.resolve()
    assert len(cfg.devices) == 1
    assert cfg.devices[0].name == "ISR1"


def test_configure_command_declines_overwrite_leaves_file_untouched(tmp_path, monkeypatch):
    """`config-audit configure` on an existing config.yaml, answering 'n', changes nothing."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("settings: {}\ndevices: []\n", encoding="utf-8")

    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "n")

    code = main(["-c", str(config_path), "configure"])
    assert code == 1
    assert config_path.read_text(encoding="utf-8") == "settings: {}\ndevices: []\n"
