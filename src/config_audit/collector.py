"""Collector: pull running-configs from devices over SSH (Netmiko).

Collection ONLY — no hashing, diffing, storage, or git. Per-device try/except
so one unreachable box can't kill the whole run.

Offline seam: pass source_text (or a {name: text} map to collect_all) to develop
and unit-test the pipeline against saved configs, with no live device.

Drivers: cisco_ios (ISR + Catalyst), cisco_s300 (SG L3 switch — test carefully).
"""
from __future__ import annotations

from dataclasses import dataclass

from .inventory import Device


@dataclass
class CollectionResult:
    device: str
    ok: bool
    config_text: str = ""
    error: str = ""


def fetch_running_config(device: Device, source_text: str | None = None) -> CollectionResult:
    """Connect to one device and return its running-config wrapped in a result.

    If source_text is given, use it (offline path) instead of connecting.
    """
    if source_text is not None:
        return CollectionResult(
            device=device.name, ok=True, config_text=source_text.replace("\r\n", "\n")
        )

    # --- LIVE PATH ---------------------------------------------------------
    # netmiko is imported lazily, inside the function, on purpose: it keeps this
    # module importable (and the offline path unit-testable) in environments
    # where netmiko isn't installed, e.g. CI that only exercises source_text.
    from netmiko import ConnectHandler

    params = {
        "device_type": device.device_type,
        "host": device.host,
        "username": device.username,
        "password": device.password,
        "secret": device.secret,
    }
    try:
        with ConnectHandler(**params) as conn:
            if device.secret:
                conn.enable()
            config = conn.send_command("show running-config")
        return CollectionResult(
            device=device.name, ok=True, config_text=config.replace("\r\n", "\n")
        )
    except Exception as exc:
        # Deliberately broad: one unreachable or misbehaving device must not abort
        # the whole run, and its failure is captured as data (ok=False, error=...)
        # for the report. Catching netmiko's specific exception types would require
        # importing them at module top, which would defeat the lazy-import above.
        return CollectionResult(device=device.name, ok=False, error=str(exc))


def collect_all(
    devices: list[Device], source_texts: dict | None = None
) -> list[CollectionResult]:
    """Fetch every device serially. source_texts={name: text} forces the offline path."""
    results = []
    for device in devices:
        text = source_texts.get(device.name) if source_texts else None
        results.append(fetch_running_config(device, source_text=text))
    return results
