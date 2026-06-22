"""Git backend: write configs to the backup repo and commit them.

Uses plain `git` via subprocess (no extra dependency). The backup repo is a
SEPARATE git repository from this code repo — point settings.backup_dir at it.

Implemented as a working reference. The one TODO is optional hardening.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path


def write_config(backup_dir: Path, device_name: str, config_text: str) -> Path:
    """Write a device's running-config to backup_dir/<device>.cfg and return the path."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"{device_name}.cfg"
    path.write_text(config_text)
    return path


def commit_changes(repo_dir: Path, message: str | None = None) -> bool:
    """Stage all changes and commit.

    Returns True if a commit was made, False if there was nothing to commit
    (i.e. no drift since last run).

    TODO (optional hardening):
        - Verify repo_dir is a git repo first (git rev-parse --is-inside-work-tree).
        - Surface stderr on a real failure instead of treating every non-zero
          exit as "no changes".
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    message = message or f"Config backup — {stamp}"

    subprocess.run(["git", "-C", str(repo_dir), "add", "-A"], check=True)
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "commit", "-m", message],
        capture_output=True, text=True,
    )
    return result.returncode == 0
