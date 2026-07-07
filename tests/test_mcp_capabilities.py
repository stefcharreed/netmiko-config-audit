"""What the config-audit MCP server should let an agent accomplish.

Acceptance-style, one capability per test, exercised through the *exposed* tool
handlers (the registry) with the config resolved from CONFIG_AUDIT_CONFIG — exactly
how the running server resolves it. This is the "can it do the job, end to end" layer,
above the unit-level wrapping in test_tools.py: multi-device snapshots, the safety
gate, and the full promote-then-verify loop.
"""
import subprocess
from pathlib import Path

from config_audit_mcp import registry

# Small sanitized configs (RFC 5737 IPs, fake names, no credentials).
ISR1_BASE = (
    "hostname ISR1\n"
    "interface GigabitEthernet0/0\n description LAN-A\n ip address 192.0.2.1 255.255.255.0\n"
)
ISR1_DRIFT = (
    "hostname ISR1\n"
    "interface GigabitEthernet0/0\n description LAN-A-PRINTERS\n"
    " ip address 192.0.2.1 255.255.255.0\n"
)
CAT1_BASE = "hostname CAT1\ninterface GigabitEthernet1/0/2\n switchport access vlan 10\n"
CAT1_DRIFT = "hostname CAT1\ninterface GigabitEthernet1/0/2\n switchport access vlan 20\n"


def _tool(name: str):
    """Fetch the exposed handler for a tool by its registered name."""
    return next(s.handler for s in registry.REGISTRY if s.name == name)


def _platform(tmp_path: Path, monkeypatch, devices: dict):
    """Stand up a temp 'network'. `devices` maps name -> (backup_text, baseline_text|None).

    Builds config.yaml + populated backup/baseline dirs, git-inits the baseline repo
    (promote commits into it), and points CONFIG_AUDIT_CONFIG at the config — so the
    handlers resolve it from the environment just like the real server.
    """
    backup_dir, baseline_dir = tmp_path / "backups", tmp_path / "baselines"
    backup_dir.mkdir()
    baseline_dir.mkdir()
    subprocess.run(["git", "init", "-q", str(baseline_dir)], check=True)
    subprocess.run(
        ["git", "-C", str(baseline_dir), "config", "user.email", "t@example.test"], check=True
    )
    subprocess.run(["git", "-C", str(baseline_dir), "config", "user.name", "Test"], check=True)

    lines = [
        "settings:",
        f"  backup_dir: {backup_dir}",
        f"  baseline_dir: {baseline_dir}",
        f"  report_path: {tmp_path / 'reports'}",
        "devices:",
    ]
    for name, (backup, baseline) in devices.items():
        (backup_dir / f"{name}.cfg").write_text(backup, encoding="utf-8")
        if baseline is not None:
            (baseline_dir / f"{name}.cfg").write_text(baseline, encoding="utf-8")
        lines += [f"  - name: {name}", "    host: 192.0.2.1", "    device_type: cisco_ios"]

    config = tmp_path / "config.yaml"
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("CONFIG_AUDIT_CONFIG", str(config))
    return baseline_dir


# --- inventory & read capabilities -----------------------------------------

def test_can_list_the_managed_inventory(tmp_path, monkeypatch):
    """An operator can ask the server which devices it manages."""
    _platform(
        tmp_path, monkeypatch, {"ISR1": (ISR1_BASE, ISR1_BASE), "CAT1": (CAT1_BASE, CAT1_BASE)}
    )
    devices = _tool("list_devices")()
    assert {d["device"] for d in devices} == {"ISR1", "CAT1"}


def test_can_answer_did_this_device_change_when_it_did(tmp_path, monkeypatch):
    """'Did anything change on ISR1?' returns the drift with added/removed lines."""
    _platform(tmp_path, monkeypatch, {"ISR1": (ISR1_DRIFT, ISR1_BASE)})
    result = _tool("get_drift")("ISR1")
    assert result["in_sync"] is False
    assert any("description LAN-A-PRINTERS" in ln for ln in result["added"])
    assert any("description LAN-A" in ln for ln in result["removed"])


def test_can_answer_did_this_device_change_when_it_did_not(tmp_path, monkeypatch):
    """A device whose backup matches baseline reports in sync."""
    _platform(tmp_path, monkeypatch, {"ISR1": (ISR1_BASE, ISR1_BASE)})
    assert _tool("get_drift")("ISR1")["in_sync"] is True


def test_can_give_a_network_wide_drift_snapshot(tmp_path, monkeypatch):
    """'What changed anywhere?' flags only the device that actually drifted."""
    _platform(tmp_path, monkeypatch, {
        "ISR1": (ISR1_DRIFT, ISR1_BASE),   # drifted
        "CAT1": (CAT1_BASE, CAT1_BASE),    # clean
    })
    snapshot = {d["device"]: d["in_sync"] for d in _tool("get_drift_all")()}
    assert snapshot == {"ISR1": False, "CAT1": True}


def test_can_read_current_and_baseline_config(tmp_path, monkeypatch):
    """An agent can fetch a device's latest backup and its approved baseline."""
    _platform(tmp_path, monkeypatch, {"ISR1": (ISR1_DRIFT, ISR1_BASE)})
    assert _tool("get_config")("ISR1", "current")["config"] == ISR1_DRIFT
    assert _tool("get_config")("ISR1", "baseline")["config"] == ISR1_BASE


# --- robustness / edge capabilities ----------------------------------------

def test_handles_an_unknown_device_without_crashing(tmp_path, monkeypatch):
    """Asking about a device not in inventory returns a clean 'unknown', not an error."""
    _platform(tmp_path, monkeypatch, {"ISR1": (ISR1_BASE, ISR1_BASE)})
    result = _tool("get_drift")("NOPE")
    assert result["known"] is False
    assert result["in_sync"] is None


# --- promotion: preview, gate, and the full write loop ---------------------

def test_can_preview_a_promotion_without_changing_anything(tmp_path, monkeypatch):
    """plan_promotion reports the pending change but leaves the baseline untouched."""
    baseline_dir = _platform(tmp_path, monkeypatch, {"ISR1": (ISR1_DRIFT, ISR1_BASE)})
    before = (baseline_dir / "ISR1.cfg").read_text(encoding="utf-8")
    plan = _tool("plan_promotion")("ISR1")
    assert plan["has_drift"] is True
    assert (baseline_dir / "ISR1.cfg").read_text(encoding="utf-8") == before  # unchanged


def test_will_not_promote_without_explicit_confirmation(tmp_path, monkeypatch):
    """The safety gate: confirm defaults to False, so a write needs to be asked for."""
    baseline_dir = _platform(tmp_path, monkeypatch, {"ISR1": (ISR1_DRIFT, ISR1_BASE)})
    before = (baseline_dir / "ISR1.cfg").read_text(encoding="utf-8")
    result = _tool("promote_baseline")("ISR1")          # confirm omitted -> False
    assert result["promoted"] is False
    assert result["reason"] == "confirm=False"
    assert (baseline_dir / "ISR1.cfg").read_text(encoding="utf-8") == before


def test_approved_promotion_updates_commits_and_reads_clean(tmp_path, monkeypatch):
    """The full loop: approve -> baseline updated AND committed -> device now in sync."""
    baseline_dir = _platform(tmp_path, monkeypatch, {"ISR1": (ISR1_DRIFT, ISR1_BASE)})
    result = _tool("promote_baseline")("ISR1", confirm=True)
    assert result["promoted"] is True and result["committed"] is True

    # baseline now holds the promoted config, and a fresh drift check is clean
    assert (baseline_dir / "ISR1.cfg").read_text(encoding="utf-8") == ISR1_DRIFT
    assert _tool("get_drift")("ISR1")["in_sync"] is True

    # the promotion is in git history
    log = subprocess.run(
        ["git", "-C", str(baseline_dir), "log", "--oneline"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "ISR1" in log


def test_can_establish_an_initial_baseline(tmp_path, monkeypatch):
    """For a device with no baseline yet, the server can bootstrap the first one."""
    baseline_dir = _platform(tmp_path, monkeypatch, {"ISR1": (ISR1_BASE, None)})
    plan = _tool("plan_promotion")("ISR1")
    assert plan["is_initial"] is True

    result = _tool("promote_baseline")("ISR1", confirm=True)
    assert result["promoted"] is True
    assert (baseline_dir / "ISR1.cfg").exists()
