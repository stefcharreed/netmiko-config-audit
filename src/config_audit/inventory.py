"""Load tool configuration and merge credentials from the environment.

Device addressing and behaviour live in config.yaml (a sanitized example is
version-controlled). Credentials live in secrets.env (gitignored) and are merged
in at runtime, so they never touch the repo.

This module is implemented as a working reference for the structured-data
approach the rest of the tool follows. Read it, then build collector/drift/report
in the same style.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass
class Device:
    name: str
    host: str
    device_type: str
    username: str
    password: str
    secret: str = ""


@dataclass
class Settings:
    backup_dir: Path
    golden_dir: Path
    report_path: Path


@dataclass
class Config:
    settings: Settings
    devices: list[Device] = field(default_factory=list)


def load_config(config_path: str | Path, secrets_path: str | Path = "secrets.env") -> Config:
    """Read config.yaml + secrets.env into a typed Config object."""
    load_dotenv(secrets_path)

    default_user = os.environ.get("NET_USERNAME", "")
    default_pass = os.environ.get("NET_PASSWORD", "")
    default_secret = os.environ.get("NET_SECRET", "")

    raw = yaml.safe_load(Path(config_path).read_text())
    s = raw.get("settings", {})
    settings = Settings(
        backup_dir=Path(s.get("backup_dir", "../config-backups")),
        golden_dir=Path(s.get("golden_dir", "../config-backups/golden")),
        report_path=Path(s.get("report_path", "../config-backups/reports")),
    )

    devices: list[Device] = []
    for d in raw.get("devices", []):
        devices.append(
            Device(
                name=d["name"],
                host=d["host"],
                device_type=d["device_type"],
                username=d.get("username", default_user),
                password=d.get("password", default_pass),
                secret=d.get("secret", default_secret),
            )
        )
    return Config(settings=settings, devices=devices)
