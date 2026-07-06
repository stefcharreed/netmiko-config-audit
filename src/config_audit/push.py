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

`config_lines` isn't purely additive: within a parent block that exists in both
the live config and the baseline (e.g. the same `interface`/`ip access-list`),
a child line the device has that the baseline doesn't gets an explicit `no`
line before the baseline's own children -- see `_build_config_lines`. A whole
extra parent block (the device has an `interface`/ACL the baseline never
mentions at all) is never auto-removed -- that stays a human decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .drift import compare_to_baseline
from .inventory import Device
from .normalize import normalize


@dataclass
class PushPlan:
    device: str
    baseline_exists: bool
    config_lines: list[str]   # exact lines that would be sent, in order
    diff_lines: list[str]     # unified diff: live -> baseline, for human review
    no_changes: bool          # live already matches baseline (or no baseline exists)


def _parse_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Group normalized config lines into (parent, children) blocks.

    IOS indents a command's children (e.g. an interface's `description`/`ip
    address`, an ACL's `permit`/`deny` entries) with one leading space; a parent
    line has none. One level of nesting covers every real block this tool has
    seen (interfaces, ACLs, `router ospf`, `line con`/`line vty`) -- deeper
    nesting isn't a thing in IOS config output.
    """
    blocks: list[tuple[str, list[str]]] = []
    for line in lines:
        if line.startswith((" ", "\t")) and blocks:
            blocks[-1][1].append(line)
        else:
            blocks.append((line, []))
    return blocks


def _invert(child_line: str) -> str:
    """Reverse a single child config line into its `no` form.

    `no no shutdown` isn't a thing -- a child that's already a `no ...` line
    reverses by dropping the `no`, not by doubling it.
    """
    stripped = child_line.strip()
    if stripped.startswith("no "):
        return stripped[3:]
    return f"no {stripped}"


def _build_config_lines(baseline_text: str, live_config: str) -> list[str]:
    """Reconcile live -> baseline: adds (baseline's lines, sent as today) plus
    mechanical per-block removes for child lines the device has that the
    baseline doesn't -- e.g. a stale ACL entry or a changed `description`.

    Scope, by design: only child lines under a parent that exists in BOTH
    configs are auto-reversed. A whole parent block the device has that the
    baseline doesn't at all (e.g. an extra `interface`/ACL the template never
    mentions) is never auto-removed here -- deleting a whole block is a
    different risk class (an ACL might still be referenced elsewhere; IOS
    won't even let you `no` a physical interface) and stays a human decision
    via `promote`/manual cleanup, same as before this change.
    """
    base_blocks = _parse_blocks(normalize(baseline_text))
    live_blocks = dict(_parse_blocks(normalize(live_config)))

    config_lines: list[str] = []
    for parent, base_children in base_blocks:
        config_lines.append(parent)
        live_children = live_blocks.get(parent)
        if live_children is not None:
            base_child_set = {c.strip() for c in base_children}
            for live_child in live_children:
                if live_child.strip() not in base_child_set:
                    config_lines.append(_invert(live_child))
        config_lines.extend(base_children)
    return config_lines


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

    config_lines = _build_config_lines(baseline_text, live_config)
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
