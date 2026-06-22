"""Collector: pull running-configs from devices over SSH (Netmiko).

This is the automation core of Project 1 — and the piece to build yourself.
The signatures below define the contract the rest of the tool expects; fill in
the bodies once we've walked through the Netmiko patterns.

Patterns to apply (mapped to your gear):
    - cisco_ios driver for the ISR router and the Catalyst switch
    - cisco_s300 driver for the SG L3 switch (limited support — test carefully)
    - one connection per device; pull `show running-config`; return the text
    - catch failures per-device so one unreachable box doesn't kill the whole run
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


def fetch_running_config(device: Device) -> CollectionResult:
    """Connect to a single device and return its running-config.

    TODO:
        1. Build the Netmiko connection dict from `device`
           (device_type, host, username, password, secret).
        2. Open the connection inside a try/except (ConnectHandler(**params)).
        3. send_command("show running-config").
        4. Return CollectionResult(ok=True, config_text=...) on success,
           or CollectionResult(ok=False, error=str(e)) on any failure.
    """
    raise NotImplementedError


def collect_all(devices: list[Device]) -> list[CollectionResult]:
    """Fetch running-configs from every device.

    TODO:
        - Loop over devices, calling fetch_running_config for each.
        - Get the serial version working first; add concurrency later.
    """
    raise NotImplementedError
