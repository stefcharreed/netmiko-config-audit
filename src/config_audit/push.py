"""Push a device's baseline to the device itself over SSH (Netmiko).

Lifecycle:
    plan   compare the device's live running-config against baseline_dir/<device>.cfg,
           produce the lines that would be sent -- no SSH, no writes
    (human reviews diff_lines, confirms)
    apply  send_config_set() the reviewed lines -- NOT saved yet, so a bad push is
           still just one reload away from gone
    (human reviews the post-push drift check, confirms again)
    save   separate, second confirm -- `write memory` so the push survives a reload

Same split as promote.py: plan_push() is pure and side-effect free; apply_push()/
save_running_config() are the only functions that open a session and write. Diff
rendering, both y/N gates, and any post-push backup/git commit live in the CLI
caller, not here.

The save step is deliberately its own function/gate rather than a `save=True` on
apply_push that the CLI just always passes -- a rejected save must still leave the
already-sent config live on the device for inspection, and merging the two would
make an "abort" answer ambiguous between "don't push" and "don't persist."
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .drift import compare_to_baseline
from .inventory import Device


@dataclass
class PushPlan:
    device: str
    baseline_exists: bool
    config_lines: list[str]   # exact lines that would be sent, in order
    diff_lines: list[str]     # unified diff: live -> baseline, for human review
    no_changes: bool          # live already matches baseline (or no baseline exists)


def plan_push(device_name: str, baseline_dir: Path, live_config: str) -> PushPlan:
    """Compare a device's live running-config against its baseline and report
    what would be sent to reconcile it. Pure -- no SSH, no writes.
    """
    baseline_path = Path(baseline_dir) / f"{device_name}.cfg"
    if not baseline_path.exists():
        return PushPlan(
            device=device_name, baseline_exists=False,
            config_lines=[], diff_lines=[], no_changes=True,
        )

    baseline_text = baseline_path.read_text(encoding="utf-8")
    result = compare_to_baseline(device_name, live_config, baseline_text)
    if not result.has_drift:
        return PushPlan(
            device=device_name, baseline_exists=True,
            config_lines=[], diff_lines=[], no_changes=True,
        )

    # v1: push the WHOLE reviewed baseline, not a subset sliced out of the diff.
    # A unified diff's +/- lines drop parent/child context (e.g. an ACL's
    # `ip access-list` parent line vs. its `permit`/`deny` children) -- sending
    # just the delta lines risks orphaned children or malformed blocks.
    config_lines = [line for line in baseline_text.splitlines() if line.strip()]
    return PushPlan(
        device=device_name, baseline_exists=True,
        config_lines=config_lines, diff_lines=result.diff_lines, no_changes=False,
    )


def _connect_params(device: Device) -> dict:
    return {
        "device_type": device.device_type,
        "host": device.host,
        "username": device.username,
        "password": device.password,
        "secret": device.secret,
    }


def apply_push(device: Device, config_lines: list[str]) -> str:
    """The one write that changes running config: send config_lines via Netmiko.

    Does NOT save -- an un-saved push is reversible with a reload. Returns the
    POST-push running-config so the caller can drift-check it before deciding
    whether to save (see save_running_config).
    """
    from netmiko import ConnectHandler  # lazy import, same reason as collector.py

    with ConnectHandler(**_connect_params(device)) as conn:
        if device.secret:
            conn.enable()
        conn.send_config_set(config_lines)
        return conn.send_command("show running-config").replace("\r\n", "\n")


def save_running_config(device: Device) -> None:
    """Persist the current running-config so it survives a reload.

    Deliberately separate from apply_push and its own confirm gate in the CLI --
    a human should see the post-push drift check before committing to `wr mem`.
    """
    from netmiko import ConnectHandler

    with ConnectHandler(**_connect_params(device)) as conn:
        if device.secret:
            conn.enable()
        conn.save_config()
