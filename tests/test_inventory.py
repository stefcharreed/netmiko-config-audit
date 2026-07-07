"""inventory.load_config reads config.yaml and merges credentials from the
environment at runtime, so secrets never live in the YAML. These tests pin the
parsing and the device-vs-environment credential precedence.
"""
from pathlib import Path

from config_audit.inventory import load_config

CONFIG_WITH_DEVICES = """\
settings:
  backup_dir: /tmp/ca/backups
  baseline_dir: /tmp/ca/baselines
  report_path: /tmp/ca/reports
devices:
  - name: ISR1
    host: 192.0.2.1
    device_type: cisco_ios
  - name: CAT1
    host: 192.0.2.2
    device_type: cisco_ios
    username: localuser
    password: localpass
    secret: localsecret
"""


def _write(tmp_path: Path, text: str) -> Path:
    """Write a config.yaml into the temp dir and return its path."""
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_settings_parse_into_path_objects(tmp_path, monkeypatch):
    """The settings block becomes typed Path fields on Settings."""
    cfg = load_config(_write(tmp_path, CONFIG_WITH_DEVICES), secrets_path=tmp_path / "none.env")
    assert cfg.settings.backup_dir == Path("/tmp/ca/backups")
    assert cfg.settings.baseline_dir == Path("/tmp/ca/baselines")
    assert cfg.settings.report_path == Path("/tmp/ca/reports")


def test_devices_inherit_credentials_from_environment(tmp_path, monkeypatch):
    """A device that omits credentials falls back to the NET_* environment vars."""
    monkeypatch.setenv("NET_USERNAME", "envuser")
    monkeypatch.setenv("NET_PASSWORD", "envpass")
    monkeypatch.setenv("NET_SECRET", "envsecret")
    cfg = load_config(_write(tmp_path, CONFIG_WITH_DEVICES), secrets_path=tmp_path / "none.env")

    isr1 = next(d for d in cfg.devices if d.name == "ISR1")
    assert (isr1.username, isr1.password, isr1.secret) == ("envuser", "envpass", "envsecret")


def test_device_level_credentials_override_environment(tmp_path, monkeypatch):
    """Explicit per-device credentials in YAML win over the environment defaults."""
    monkeypatch.setenv("NET_USERNAME", "envuser")
    monkeypatch.setenv("NET_PASSWORD", "envpass")
    cfg = load_config(_write(tmp_path, CONFIG_WITH_DEVICES), secrets_path=tmp_path / "none.env")

    cat1 = next(d for d in cfg.devices if d.name == "CAT1")
    assert (cat1.username, cat1.password, cat1.secret) == ("localuser", "localpass", "localsecret")


def test_missing_settings_block_uses_defaults(tmp_path, monkeypatch):
    """With no settings block, load_config falls back to its default relative paths."""
    cfg = load_config(_write(tmp_path, "devices: []\n"), secrets_path=tmp_path / "none.env")
    assert cfg.devices == []
    assert cfg.settings.backup_dir == Path("../config-backups")


def test_device_missing_required_field_raises_clear_error(tmp_path, monkeypatch):
    """A device missing a required field fails with a legible message, not a bare KeyError."""
    bad = "devices:\n  - name: ISR1\n    device_type: cisco_ios\n"   # no host
    try:
        load_config(_write(tmp_path, bad), secrets_path=tmp_path / "none.env")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "host" in str(exc)
        assert "ISR1" in str(exc)
