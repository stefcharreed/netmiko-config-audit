"""The MCP tools only reshape Project 1's output for an LLM; drift detection
itself is tested in Project 1. So these tests prove the wrapping is correct:
credentials are excluded, drift becomes added/removed, unknown devices are
handled, and the mutating tool refuses without confirm.
"""
from pathlib import Path
import subprocess

from config_audit_mcp import tools

BASELINE = (
    "hostname ISR1\n"
    "interface GigabitEthernet0/0\n"
    " description LAN-A\n"
    " ip address 192.0.2.1 255.255.255.0\n"
)
DRIFTED = (
    "hostname ISR1\n"
    "interface GigabitEthernet0/0\n"
    " description LAN-A-PRINTERS\n"
    " ip address 192.0.2.1 255.255.255.0\n"
)


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)


def _project(tmp_path: Path, *, backup: str, baseline: str | None = BASELINE) -> str:
    """Build a temp project (config.yaml + populated dirs). Returns the config path."""
    backup_dir, baseline_dir = tmp_path / "backups", tmp_path / "baselines"
    backup_dir.mkdir(); baseline_dir.mkdir()
    # Both dirs are their own git repo — promote_baseline commits into baseline_dir,
    # backup_now commits into backup_dir.
    _git_init(backup_dir)
    _git_init(baseline_dir)
    (backup_dir / "ISR1.cfg").write_text(backup, encoding="utf-8")
    if baseline is not None:
        (baseline_dir / "ISR1.cfg").write_text(baseline, encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        "settings:\n"
        f"  backup_dir: {backup_dir}\n"
        f"  baseline_dir: {baseline_dir}\n"
        f"  report_path: {tmp_path / 'reports'}\n"
        "devices:\n"
        "  - name: ISR1\n"
        "    host: 192.0.2.1\n"
        "    device_type: cisco_ios\n"
        "    username: secretuser\n"
        "    password: secretpass\n",
        encoding="utf-8",
    )
    return str(config)


def test_list_devices_excludes_credentials(tmp_path):
    """list_devices exposes name/host/type only — never the username or password."""
    config = _project(tmp_path, backup=BASELINE)
    devices = tools.list_devices(config)
    assert devices == [{"device": "ISR1", "host": "192.0.2.1", "device_type": "cisco_ios"}]
    assert "secretpass" not in str(devices)


def test_get_drift_reports_in_sync(tmp_path):
    """A backup equal to baseline reports in_sync with no added/removed lines."""
    config = _project(tmp_path, backup=BASELINE)
    result = tools.get_drift("ISR1", config)
    assert result["known"] is True
    assert result["in_sync"] is True
    assert result["added"] == [] and result["removed"] == []


def test_get_drift_splits_change_into_added_and_removed(tmp_path):
    """A changed description surfaces as one removed line and one added line."""
    config = _project(tmp_path, backup=DRIFTED)
    result = tools.get_drift("ISR1", config)
    assert result["in_sync"] is False
    assert any("description LAN-A-PRINTERS" in l for l in result["added"])
    assert any("description LAN-A" in l for l in result["removed"])


def test_get_drift_unknown_device_is_flagged(tmp_path):
    """A device not in the inventory comes back known=False rather than erroring."""
    config = _project(tmp_path, backup=BASELINE)
    result = tools.get_drift("NOPE", config)
    assert result["known"] is False
    assert result["in_sync"] is None


def test_get_drift_all_returns_one_entry_per_device(tmp_path):
    """get_drift_all maps over the whole inventory."""
    config = _project(tmp_path, backup=DRIFTED)
    results = tools.get_drift_all(config)
    assert [r["device"] for r in results] == ["ISR1"]


def test_plan_promotion_reports_would_change(tmp_path):
    """plan_promotion reports drift and the added/removed lines without writing."""
    config = _project(tmp_path, backup=DRIFTED)
    plan = tools.plan_promotion("ISR1", config)
    assert plan["has_drift"] is True
    assert plan["added"] and plan["removed"]


def test_promote_baseline_refuses_without_confirm(tmp_path):
    """With confirm=False the mutating tool is a no-op that only reports the change."""
    config = _project(tmp_path, backup=DRIFTED)
    result = tools.promote_baseline("ISR1", confirm=False, config_path=config)
    assert result["promoted"] is False
    assert result["reason"] == "confirm=False"


def test_promote_baseline_writes_with_confirm(tmp_path):
    """With confirm=True the baseline is overwritten with the backup text and committed."""
    config = _project(tmp_path, backup=DRIFTED)
    result = tools.promote_baseline("ISR1", confirm=True, config_path=config)
    assert result["promoted"] is True
    assert result["committed"] is True
    assert (tmp_path / "baselines" / "ISR1.cfg").read_text(encoding="utf-8") == DRIFTED
    # a re-plan is now in sync
    assert tools.get_drift("ISR1", config)["in_sync"] is True


def test_get_config_returns_baseline_and_current(tmp_path):
    """get_config can fetch either the current backup or the approved baseline."""
    config = _project(tmp_path, backup=DRIFTED)
    assert tools.get_stored_config("ISR1", "current", config)["config"] == DRIFTED
    assert tools.get_stored_config("ISR1", "baseline", config)["config"] == BASELINE


def test_backup_now_writes_and_commits_via_source_texts(tmp_path):
    """The offline seam (source_texts) proves the reshaping without live SSH --
    collector's live path itself is already hardware-validated elsewhere."""
    config = _project(tmp_path, backup=BASELINE)
    result = tools.backup_now(config, source_texts={"ISR1": DRIFTED})

    assert result["committed"] is True
    assert result["results"] == [{"device": "ISR1", "ok": True, "error": None}]
    assert (tmp_path / "backups" / "ISR1.cfg").read_text(encoding="utf-8") == DRIFTED


def test_backup_now_reports_per_device_failure_without_aborting(tmp_path, monkeypatch):
    """A device that fails to collect is reported in the result, not raised --
    proves backup_now's reshaping handles collector's failure shape correctly.
    Monkeypatches collect_all (rather than leaving source_texts empty) so this
    stays fast and deterministic instead of attempting a real, slow connection
    to the RFC 5737 test address -- collector's own real-failure behavior is
    collector.py's concern, not this reshaping layer's."""
    from config_audit import collector

    config = _project(tmp_path, backup=BASELINE)
    monkeypatch.setattr(
        collector, "collect_all",
        lambda devices, source_texts=None: [
            collector.CollectionResult(
                device="ISR1", ok=False, error="TCP connection to device failed."
            )
        ],
    )

    result = tools.backup_now(config)

    assert result["results"] == [
        {"device": "ISR1", "ok": False, "error": "TCP connection to device failed."}
    ]
    # the failed device's backup file is left untouched, not overwritten with nothing
    assert (tmp_path / "backups" / "ISR1.cfg").read_text(encoding="utf-8") == BASELINE


def test_backup_now_second_run_with_no_changes_reports_uncommitted(tmp_path):
    """Re-running with identical content reports committed=False, matching the
    CLI's 'no changes since last run' behavior."""
    config = _project(tmp_path, backup=BASELINE)
    first = tools.backup_now(config, source_texts={"ISR1": BASELINE})
    assert first["committed"] is True  # first write is new content to the repo

    second = tools.backup_now(config, source_texts={"ISR1": BASELINE})
    assert second["committed"] is False
