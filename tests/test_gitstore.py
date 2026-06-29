"""gitstore writes one .cfg per device and commits via plain git. These tests
run against a throwaway repo in a temp dir, so they touch real git but never the
project's own history.
"""
import subprocess

import pytest

from config_audit.gitstore import write_config, commit_changes


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
