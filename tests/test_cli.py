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


def _project(
    tmp_path: Path, backup_fixture: str, baseline_fixture: str | None = "ISR1_baseline.cfg"
) -> Path:
    """Build a temp project: config.yaml + populated backup/baseline dirs. Returns the config path.

    baseline_fixture=None simulates a device with no baseline established yet --
    the backups dir is populated but baselines/ISR1.cfg is never written.
    """
    backup, baseline, reports = tmp_path / "backups", tmp_path / "baselines", tmp_path / "reports"
    backup.mkdir(); baseline.mkdir()
    (backup / "ISR1.cfg").write_text((FIXTURES / backup_fixture).read_text(), encoding="utf-8")
    if baseline_fixture is not None:
        (baseline / "ISR1.cfg").write_text(
            (FIXTURES / baseline_fixture).read_text(), encoding="utf-8"
        )
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
    out = capsys.readouterr().out
    assert code == 1
    assert "DRIFT" in out


def test_diff_suggests_push_when_drift_found(tmp_path, capsys):
    """Drift found -- diff points at `push` as the next command, but never runs it."""
    config = _project(tmp_path, backup_fixture="ISR1_current_drift.cfg")
    code = main(["-c", str(config), "diff"])
    out = capsys.readouterr().out
    assert code == 1
    assert "config-audit push ISR1" in out


def test_diff_no_drift_does_not_suggest_push(tmp_path, capsys):
    """No drift -- nothing to reconcile, so no push suggestion is printed."""
    config = _project(tmp_path, backup_fixture="ISR1_current_clean.cfg")
    code = main(["-c", str(config), "diff"])
    out = capsys.readouterr().out
    assert code == 0
    assert "config-audit push" not in out


def test_diff_no_baseline_is_distinct_from_drift(tmp_path, capsys):
    """A device with no baseline yet shows NO BASELINE, not DRIFT -- these are not
    the same thing, and showing the whole config as 'drift' is misleading."""
    config = _project(tmp_path, backup_fixture="ISR1_current_clean.cfg", baseline_fixture=None)
    code = main(["-c", str(config), "diff"])
    out = capsys.readouterr().out
    assert code == 1
    assert "NO BASELINE" in out
    assert "DRIFT" not in out
    assert "config-audit promote" in out


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


def test_set_baseline_missing_file_returns_two(tmp_path, capsys):
    """A source file that doesn't exist is rejected before any diff/prompt."""
    config = _project(tmp_path, backup_fixture="ISR1_current_clean.cfg", baseline_fixture=None)
    missing = tmp_path / "does-not-exist.cfg"

    code = main(["-c", str(config), "set-baseline", "ISR1", str(missing)])
    assert code == 2
    assert "no such file" in capsys.readouterr().out.lower()


def test_set_baseline_ztp_establishes_initial_baseline(tmp_path, capsys, monkeypatch):
    """ZTP path: no baseline yet, author one straight from a template file, no device
    ever contacted -- confirming writes the file and commits it."""
    config = _project(tmp_path, backup_fixture="ISR1_current_clean.cfg", baseline_fixture=None)
    template = FIXTURES / "ISR1_baseline.cfg"
    subprocess.run(["git", "init", "-q", str(tmp_path / "baselines")], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path / "baselines"), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path / "baselines"), "config", "user.name", "Test"], check=True
    )

    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")
    code = main(["-c", str(config), "set-baseline", "ISR1", str(template)])

    assert code == 0
    baseline_file = tmp_path / "baselines" / "ISR1.cfg"
    assert baseline_file.read_text(encoding="utf-8") == template.read_text(encoding="utf-8")
    assert "baseline updated" in capsys.readouterr().out.lower()


def test_set_baseline_aborts_on_no_and_leaves_baseline_untouched(tmp_path, capsys, monkeypatch):
    """Answering 'n' exits 1 and does not create/modify the baseline file."""
    config = _project(tmp_path, backup_fixture="ISR1_current_clean.cfg", baseline_fixture=None)
    template = FIXTURES / "ISR1_baseline.cfg"

    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
    code = main(["-c", str(config), "set-baseline", "ISR1", str(template)])

    assert code == 1
    assert "aborted" in capsys.readouterr().out.lower()
    assert not (tmp_path / "baselines" / "ISR1.cfg").exists()


def test_set_baseline_matching_existing_is_nothing_to_do(tmp_path, capsys):
    """Source file identical to the existing baseline -- no prompt, exits 0."""
    config = _project(tmp_path, backup_fixture="ISR1_current_clean.cfg")
    template = FIXTURES / "ISR1_baseline.cfg"  # same content _project wrote as the baseline

    code = main(["-c", str(config), "set-baseline", "ISR1", str(template)])
    assert code == 0
    assert "nothing to do" in capsys.readouterr().out.lower()


def test_push_no_baseline_is_not_pushed(tmp_path, capsys, monkeypatch):
    """No baseline yet for the device -- push refuses rather than sending nothing
    meaningful, and points at `promote` instead."""
    config = _project(tmp_path, backup_fixture="ISR1_current_clean.cfg", baseline_fixture=None)
    monkeypatch.setattr("config_audit.cli._ensure_secrets_file", lambda *_a, **_k: None)

    from config_audit.collector import CollectionResult

    monkeypatch.setattr(
        "config_audit.collector.fetch_running_config",
        lambda device, source_text=None: CollectionResult(
            device=device.name, ok=True, config_text=_fx_text("ISR1_current_clean.cfg")
        ),
    )

    code = main(["-c", str(config), "push", "ISR1"])
    out = capsys.readouterr().out
    assert code == 2
    assert "no baseline yet" in out.lower()
    assert "config-audit promote" in out


def test_push_no_changes_when_live_matches_baseline(tmp_path, capsys, monkeypatch):
    """Live config already matches baseline -- nothing to push, no gate shown."""
    config = _project(tmp_path, backup_fixture="ISR1_current_clean.cfg")
    monkeypatch.setattr("config_audit.cli._ensure_secrets_file", lambda *_a, **_k: None)

    from config_audit.collector import CollectionResult

    monkeypatch.setattr(
        "config_audit.collector.fetch_running_config",
        lambda device, source_text=None: CollectionResult(
            device=device.name, ok=True, config_text=_fx_text("ISR1_current_clean.cfg")
        ),
    )
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("should not prompt when there's nothing to push")
    ))

    code = main(["-c", str(config), "push", "ISR1"])
    assert code == 0
    assert "nothing to push" in capsys.readouterr().out.lower()


def test_push_aborts_on_no_at_first_gate(tmp_path, capsys, monkeypatch):
    """Answering 'n' at the push gate exits 1 and never calls apply_push."""
    config = _project(tmp_path, backup_fixture="ISR1_current_clean.cfg")
    monkeypatch.setattr("config_audit.cli._ensure_secrets_file", lambda *_a, **_k: None)

    from config_audit.collector import CollectionResult

    monkeypatch.setattr(
        "config_audit.collector.fetch_running_config",
        lambda device, source_text=None: CollectionResult(
            device=device.name, ok=True, config_text=_fx_text("ISR1_current_drift.cfg")
        ),
    )

    def _boom(*_a, **_k):
        raise AssertionError("must not push when the first gate is declined")

    monkeypatch.setattr("config_audit.push.apply_push", _boom)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")

    code = main(["-c", str(config), "push", "ISR1"])
    assert code == 1
    assert "aborted" in capsys.readouterr().out.lower()


def test_push_shows_exact_commands_and_flags_only_synthesized_removals(
    tmp_path, capsys, monkeypatch
):
    """Before the confirm prompt, push must show the human the literal commands
    it's about to send -- including any synthesized `no` reconciliation lines,
    flagged as removals. A baseline line that's legitimately `no ...` on its
    own (e.g. `no ip domain lookup`) must NOT be flagged as a removal -- only
    lines push itself generated to undo a stale child on the device."""
    config = _project(tmp_path, backup_fixture="ISR1_current_clean.cfg")
    monkeypatch.setattr("config_audit.cli._ensure_secrets_file", lambda *_a, **_k: None)

    from config_audit.collector import CollectionResult

    monkeypatch.setattr(
        "config_audit.collector.fetch_running_config",
        lambda device, source_text=None: CollectionResult(
            device=device.name, ok=True, config_text=_fx_text("ISR1_current_drift.cfg")
        ),
    )
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")  # decline, just inspect output

    code = main(["-c", str(config), "push", "ISR1"])
    out = capsys.readouterr().out
    assert code == 1
    assert "Exact commands to be sent" in out
    # synthesized removal -- must be flagged
    assert "- no permit tcp host 198.51.100.51 any eq 443" in out
    assert "will be explicitly" in out
    # legitimate baseline line that happens to start with `no` -- must render
    # as an ordinary sent line, not a flagged removal
    assert "+ no ip domain lookup" in out
    assert "- no ip domain lookup" not in out


def test_push_confirmed_but_save_declined_leaves_device_unsaved(tmp_path, capsys, monkeypatch):
    """Confirming the push but declining the save gate calls apply_push but not
    save_running_config -- the two gates are independent."""
    config = _project(tmp_path, backup_fixture="ISR1_current_clean.cfg")
    monkeypatch.setattr("config_audit.cli._ensure_secrets_file", lambda *_a, **_k: None)

    from config_audit.collector import CollectionResult

    monkeypatch.setattr(
        "config_audit.collector.fetch_running_config",
        lambda device, source_text=None: CollectionResult(
            device=device.name, ok=True, config_text=_fx_text("ISR1_current_drift.cfg")
        ),
    )
    # apply_push "succeeds" and the device now matches baseline post-push.
    monkeypatch.setattr(
        "config_audit.push.apply_push",
        lambda device, config_lines: _fx_text("ISR1_baseline.cfg"),
    )

    def _boom_save(*_a, **_k):
        raise AssertionError("must not save when the save gate is declined")

    monkeypatch.setattr("config_audit.push.save_running_config", _boom_save)

    inputs = iter(["y", "n"])  # confirm push, decline save
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))

    code = main(["-c", str(config), "push", "ISR1"])
    out = capsys.readouterr().out
    assert code == 0
    assert "pushed" in out.lower()
    assert "not saved" in out.lower()


def test_push_confirmed_and_saved_calls_save_running_config(tmp_path, capsys, monkeypatch):
    """Confirming both gates calls apply_push then save_running_config."""
    config = _project(tmp_path, backup_fixture="ISR1_current_clean.cfg")
    monkeypatch.setattr("config_audit.cli._ensure_secrets_file", lambda *_a, **_k: None)

    from config_audit.collector import CollectionResult

    monkeypatch.setattr(
        "config_audit.collector.fetch_running_config",
        lambda device, source_text=None: CollectionResult(
            device=device.name, ok=True, config_text=_fx_text("ISR1_current_drift.cfg")
        ),
    )
    monkeypatch.setattr(
        "config_audit.push.apply_push",
        lambda device, config_lines: _fx_text("ISR1_baseline.cfg"),
    )
    saved = []
    monkeypatch.setattr(
        "config_audit.push.save_running_config", lambda device: saved.append(device.name)
    )

    inputs = iter(["y", "y"])  # confirm push, confirm save
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))

    code = main(["-c", str(config), "push", "ISR1"])
    assert code == 0
    assert saved == ["ISR1"]
    assert "saved" in capsys.readouterr().out.lower()


def _fx_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


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
    """End-to-end: repo root once, default subdirectories accepted, then devices --
    writes a config.yaml that inventory.load_config can read."""
    from config_audit.cli import _run_config_wizard

    repo_root = tmp_path / "private"
    repo_root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo_root)], check=True)

    inputs = iter([
        str(repo_root),  # repo root
        "", "", "",      # accept default snapshots/baselines/reports subdirectories
        "ISR1", "192.0.2.1", "cisco_ios", "",
    ])
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(inputs))

    config_path = tmp_path / "config.yaml"
    _run_config_wizard(config_path)

    from config_audit.inventory import load_config
    cfg = load_config(config_path, secrets_path=tmp_path / "nonexistent.env")
    assert cfg.settings.backup_dir == (repo_root / "snapshots").resolve()
    assert cfg.settings.baseline_dir == (repo_root / "baselines").resolve()
    assert cfg.settings.report_path == (repo_root / "reports").resolve()
    assert len(cfg.devices) == 1
    assert cfg.devices[0].name == "ISR1"


def test_prompt_subdirectory_accepts_default(tmp_path, monkeypatch):
    from config_audit.cli import _prompt_subdirectory

    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "")  # accept default (Y)

    result = _prompt_subdirectory(tmp_path, "snapshots", "current running-configs")
    assert result == tmp_path / "snapshots"
    assert result.exists()


def test_prompt_subdirectory_declines_default_and_uses_custom_name(tmp_path, monkeypatch):
    from config_audit.cli import _prompt_subdirectory

    inputs = iter(["n", "current"])  # decline default, use an existing custom name instead
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(inputs))

    result = _prompt_subdirectory(tmp_path, "snapshots", "current running-configs")
    assert result == tmp_path / "current"
    assert result.exists()
    assert not (tmp_path / "snapshots").exists()


def test_prompt_subdirectory_rejects_blank_custom_name(tmp_path, monkeypatch):
    from config_audit.cli import _prompt_subdirectory

    inputs = iter(["n", "", "current"])  # decline default, blank name rejected, then valid
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(inputs))

    result = _prompt_subdirectory(tmp_path, "snapshots", "current running-configs")
    assert result == tmp_path / "current"


def test_configure_command_declines_overwrite_leaves_file_untouched(tmp_path, monkeypatch):
    """`config-audit configure` on an existing config.yaml, answering 'n', changes nothing."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("settings: {}\ndevices: []\n", encoding="utf-8")

    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "n")

    code = main(["-c", str(config_path), "configure"])
    assert code == 1
    assert config_path.read_text(encoding="utf-8") == "settings: {}\ndevices: []\n"
