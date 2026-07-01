"""Git backend: write configs to the backup repo and commit them.

Uses plain `git` via subprocess (no extra dependency). The backup repo is a
SEPARATE git repository from this code repo — point settings.backup_dir at it.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

# Cap every git subprocess so a hung git can never hang the tool.
_GIT_TIMEOUT = 30  # seconds


def write_config(backup_dir: Path, device_name: str, config_text: str) -> Path:
    """Write a device's running-config to backup_dir/<device>.cfg and return the path.

    One file per device, overwritten each run — git history IS the timeline, so
    `git log <device>.cfg` shows how that device evolved. No timestamped filenames.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"{device_name}.cfg"
    path.write_text(config_text, encoding="utf-8")
    return path


def is_git_repo(path: Path) -> bool:
    """True if `path` is inside a git working tree.

    Used by config setup to validate backup_dir/baseline_dir before anything is
    written -- commit_changes requires this too, but discovering it only when
    `backup` fails deep in a subprocess call is a worse experience than catching
    it here, before a single device is even contacted.
    """
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True, timeout=_GIT_TIMEOUT,
    )
    return result.returncode == 0


def git_repo_root(path: Path) -> Path | None:
    """Return the top-level directory of the git repo containing `path`, or None.

    Used to check whether a proposed backup_dir/baseline_dir resolves inside this
    same code repo -- it must be a SEPARATE, private repo instead.
    """
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, timeout=_GIT_TIMEOUT,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def commit_changes(repo_dir: Path, message: str | None = None) -> bool:
    """Stage all changes and commit. Returns True if a commit was made, False if
    there was nothing to commit (no drift since last run).

    Hardened: verifies repo_dir is a git repo, and a real git failure raises
    instead of being silently read as "no changes."
    """
    subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "--is-inside-work-tree"],
        check=True, capture_output=True, text=True, timeout=_GIT_TIMEOUT,
    )
    subprocess.run(["git", "-C", str(repo_dir), "add", "-A"], check=True, timeout=_GIT_TIMEOUT)

    # Anything staged? `git diff --cached --quiet` exits 0 = no changes, 1 = changes.
    staged = subprocess.run(["git", "-C", str(repo_dir), "diff", "--cached", "--quiet"], timeout=_GIT_TIMEOUT)
    if staged.returncode == 0:
        return False   # nothing to commit

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    message = message or f"Config backup — {stamp}"
    subprocess.run(
        ["git", "-C", str(repo_dir), "commit", "-m", message],
        check=True, capture_output=True, text=True, timeout=_GIT_TIMEOUT,
    )
    return True
