"""gitstore writes one .cfg per device and commits via plain git. These tests
run against a throwaway repo in a temp dir, so they touch real git but never the
project's own history.
"""
import subprocess

import pytest

from config_audit.gitstore import GitIdentityError, commit_changes, write_config


def _git_init(path):
    """Initialize a quiet git repo with a commit identity set."""
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)


def test_write_config_creates_file_and_returns_path(tmp_path):
    """write_config creates the backup dir if needed and returns the file path."""
    backup = tmp_path / "backups"
    path = write_config(backup, "ISR1", "hostname ISR1\n")
    assert path == backup / "ISR1.cfg"
    assert path.read_text(encoding="utf-8") == "hostname ISR1\n"


def test_write_config_overwrites_in_place(tmp_path):
    """Each run overwrites the same file — one file per device, git is the timeline."""
    backup = tmp_path / "backups"
    write_config(backup, "ISR1", "version 1\n")
    path = write_config(backup, "ISR1", "version 2\n")
    assert path.read_text(encoding="utf-8") == "version 2\n"
    assert list(backup.glob("ISR1*.cfg")) == [path]  # no timestamped duplicates


def test_commit_returns_true_when_there_are_changes(tmp_path):
    """A first write produces staged changes, so commit_changes reports a commit."""
    _git_init(tmp_path)
    write_config(tmp_path, "ISR1", "hostname ISR1\n")
    assert commit_changes(tmp_path, message="initial") is True


def test_commit_returns_false_when_nothing_changed(tmp_path):
    """With nothing new to stage, commit_changes is a no-op and returns False."""
    _git_init(tmp_path)
    write_config(tmp_path, "ISR1", "hostname ISR1\n")
    commit_changes(tmp_path, message="initial")
    assert commit_changes(tmp_path, message="again") is False


def test_commit_raises_outside_a_git_repo(tmp_path):
    """A non-repo directory is a hard error, not a silent 'no changes'."""
    write_config(tmp_path, "ISR1", "hostname ISR1\n")  # tmp_path is NOT a git repo
    with pytest.raises(subprocess.CalledProcessError):
        commit_changes(tmp_path, message="should fail")


def test_commit_scoped_to_subdirectory_ignores_sibling_changes(tmp_path):
    """backup_dir and baseline_dir commonly share one repo as sibling subdirectories.
    A commit_changes(backup_dir) call must not sweep in or report on unrelated
    pending changes sitting in baseline_dir -- confirmed via direct git testing
    that plain (unscoped) add/commit/diff --cached all operate on the WHOLE repo,
    not just the -C'd directory, which would corrupt the audit trail.
    """
    _git_init(tmp_path)
    snapshots = tmp_path / "snapshots"
    baselines = tmp_path / "baselines"
    write_config(snapshots, "ISR1", "v1\n")
    write_config(baselines, "ISR1", "v1\n")
    commit_changes(tmp_path, message="initial")

    # A real change in snapshots/, plus something staged (but not committed) in
    # baselines/ from an unrelated, earlier operation.
    write_config(snapshots, "ISR1", "v2\n")
    write_config(baselines, "ISR1", "STALE UNRELATED EDIT\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "baselines/ISR1.cfg"], check=True)

    committed = commit_changes(snapshots, message="backup run")
    assert committed is True

    log = subprocess.run(
        ["git", "-C", str(tmp_path), "show", "--stat", "-1", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "snapshots/ISR1.cfg" in log
    assert "baselines/ISR1.cfg" not in log  # must NOT have been swept into this commit

    # The stale baseline edit must still be sitting there, staged and untouched.
    status = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--short"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "baselines/ISR1.cfg" in status


def test_commit_raises_clear_error_when_identity_unconfigured(tmp_path, monkeypatch):
    """When git's commit step fails with its 'who are you' error, commit_changes
    raises GitIdentityError with the exact fix, not a bare CalledProcessError
    traceback. Regression for the exit-128 crash hit during hardware validation
    on a fresh backup repo (see CLAUDE.md).

    Real git's identity-guessing is environment-dependent (it can synthesize one
    from the OS account on some machines, masking the failure) -- so this tests
    the translation logic directly by faking just the `commit` subprocess call,
    letting init/add/diff run for real.
    """
    _git_init(tmp_path)
    write_config(tmp_path, "ISR1", "hostname ISR1\n")

    real_run = subprocess.run

    def _fake_run(cmd, *args, **kwargs):
        if "commit" in cmd:
            raise subprocess.CalledProcessError(
                128, cmd, output="",
                stderr="*** Please tell me who you are.\n\nRun\n\n  git config "
                       "user.email \"you@example.com\"\n  git config user.name "
                       "\"Your Name\"\n",
            )
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr("config_audit.gitstore.subprocess.run", _fake_run)

    with pytest.raises(GitIdentityError) as exc_info:
        commit_changes(tmp_path, message="should fail clearly")
    assert "user.email" in str(exc_info.value)
    assert str(tmp_path) in str(exc_info.value)


def test_commit_reraises_other_git_failures_unchanged(tmp_path, monkeypatch):
    """A git commit failure that ISN'T the identity issue still propagates as a
    plain CalledProcessError -- only the specific 'who are you' case is translated."""
    _git_init(tmp_path)
    write_config(tmp_path, "ISR1", "hostname ISR1\n")

    real_run = subprocess.run

    def _fake_run(cmd, *args, **kwargs):
        if "commit" in cmd:
            raise subprocess.CalledProcessError(1, cmd, output="", stderr="some other failure")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr("config_audit.gitstore.subprocess.run", _fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        commit_changes(tmp_path, message="should fail")


def test_commit_scoped_reports_no_changes_when_only_sibling_has_staged_edits(tmp_path):
    """If backup_dir itself has no new changes, commit_changes must return False
    even when an unrelated sibling directory has staged (uncommitted) changes --
    it must not falsely report 'committed' based on the sibling's staged state.
    """
    _git_init(tmp_path)
    snapshots = tmp_path / "snapshots"
    baselines = tmp_path / "baselines"
    write_config(snapshots, "ISR1", "v1\n")
    write_config(baselines, "ISR1", "v1\n")
    commit_changes(tmp_path, message="initial")

    # No new snapshots/ change this run; only an unrelated staged edit in baselines/.
    write_config(baselines, "ISR1", "STALE\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "baselines/ISR1.cfg"], check=True)

    assert commit_changes(snapshots, message="backup run") is False
