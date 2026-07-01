"""Tool logic for the config-audit MCP server.

Every function here is PURE in the MCP sense: it takes plain arguments, calls
Project 1's already-tested functions, and returns a JSON-serializable dict or
list. No MCP types leak in, so this module is unit-testable with no server and
no SDK installed — the SDK glue lives in server.py and does nothing but register
these.

This is the payoff of Project 1's seam discipline (DECISIONS: "functions return
JSON-serializable data; rendering lives in the caller"). The MCP server IS just
another caller. Nothing here re-implements drift, normalization, or promotion —
it delegates to config_audit and reshapes the result for an LLM consumer.

Read-only tools (list_devices, get_drift, get_drift_all, plan_promotion,
get_stored_config) are file-based and need no device. The mutating tool
(promote_baseline) and the live tool (backup_now) are described in server.py;
promote carries an explicit confirm gate, and backup_now is the one operation
that needs real gear.
"""
from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path

from config_audit import drift, gitstore, promote
from config_audit.inventory import Config, load_config

# The server is pointed at one config.yaml via this env var (set in the MCP
# client config). Tools resolve it once per call so a config edit is picked up
# without restarting the server.
CONFIG_ENV = "CONFIG_AUDIT_CONFIG"
DEFAULT_CONFIG = "config/config.yaml"


def _load(config_path: str | None) -> Config:
    """Resolve and load the config.yaml the server was pointed at."""
    path = config_path or os.environ.get(CONFIG_ENV, DEFAULT_CONFIG)
    return load_config(path)


def _find_device(cfg: Config, device: str):
    """Return the named Device, or None if it isn't in the inventory."""
    return next((d for d in cfg.devices if d.name == device), None)


def _read_current(cfg: Config, device: str) -> str:
    """Read the device's on-disk backup (actual state), or '' if none yet."""
    path = cfg.settings.backup_dir / f"{device}.cfg"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _split_diff(diff_lines: list[str]) -> dict:
    """Turn a unified diff into added/removed line lists for an LLM consumer.

    Reconciles the as-built DriftResult (device/has_drift/diff_lines) with the
    richer contract the platform docs describe (in_sync/added/removed/diff).
    """
    added = [l[1:].strip() for l in diff_lines
             if l.startswith("+") and not l.startswith("+++")]
    removed = [l[1:].strip() for l in diff_lines
               if l.startswith("-") and not l.startswith("---")]
    return {"added": added, "removed": removed}


# --- read-only tools -------------------------------------------------------

def list_devices(config_path: str | None = None) -> list[dict]:
    """List the devices in the inventory (name, host, type). Credentials excluded."""
    cfg = _load(config_path)
    return [
        {"device": d.name, "host": d.host, "device_type": d.device_type}
        for d in cfg.devices
    ]


def get_drift(device: str, config_path: str | None = None) -> dict:
    """Drift for one device: its on-disk backup vs its baseline, normalized both sides.

    Returns {device, known, in_sync, added, removed, unified_diff}. `known` is
    False when the device isn't in the inventory.
    """
    cfg = _load(config_path)
    if _find_device(cfg, device) is None:
        return {"device": device, "known": False, "in_sync": None,
                "added": [], "removed": [], "unified_diff": []}

    current = _read_current(cfg, device)
    baseline = drift.load_baseline(cfg.settings.baseline_dir, device)
    result = drift.compare_to_baseline(device, current, baseline)
    return {
        "device": device,
        "known": True,
        "in_sync": not result.has_drift,
        **_split_diff(result.diff_lines),
        "unified_diff": result.diff_lines,
    }


def get_drift_all(config_path: str | None = None) -> list[dict]:
    """Drift for every device in the inventory — the single-pane drift snapshot."""
    cfg = _load(config_path)
    return [get_drift(d.name, config_path) for d in cfg.devices]


def plan_promotion(device: str, config_path: str | None = None) -> dict:
    """What promoting this device's backup into its baseline WOULD change. No write.

    Returns {device, backup_exists, baseline_exists, is_initial, has_drift,
    added, removed}. This is the analysis half of Project 1's promote feature.
    """
    cfg = _load(config_path)
    plan = promote.plan_promotion(device, cfg.settings.backup_dir, cfg.settings.baseline_dir)
    return {
        "device": plan.device,
        "backup_exists": plan.backup_exists,
        "baseline_exists": plan.baseline_exists,
        "is_initial": plan.is_initial,
        "has_drift": plan.has_drift,
        **_split_diff(plan.diff_lines),
    }


def get_stored_config(device: str, which: str = "current",
                      config_path: str | None = None) -> dict:
    """Return a stored config for a device. `which` is 'current' (backup) or 'baseline'."""
    cfg = _load(config_path)
    if which == "baseline":
        text = drift.load_baseline(cfg.settings.baseline_dir, device)
    elif which == "current":
        text = _read_current(cfg, device)
    else:
        return {"device": device, "which": which, "error": "which must be 'current' or 'baseline'"}
    return {"device": device, "which": which, "exists": bool(text), "config": text}


# --- live tool (needs real gear) -------------------------------------------

def backup_now(config_path: str | None = None, source_texts: dict | None = None) -> dict:
    """Pull every managed device's live running-config over SSH and commit backups.

    Mirrors the CLI's `backup` command exactly (same collector.collect_all +
    gitstore.write_config/commit_changes calls), reshaped for an LLM consumer.
    This is the one MCP tool that contacts real network gear -- collector's live
    SSH path is already hardware-validated (Project 1's own CLI `backup`); what's
    new here is purely the reshaping into a JSON result, which is what
    `source_texts` lets you test without a device (mirrors collect_all's own
    offline seam). `source_texts` is a testing-only parameter -- the registry
    handler always calls this with none, so an LLM caller never sees it.

    Returns {results: [{device, ok, error}], committed}.
    """
    from config_audit import collector

    cfg = _load(config_path)
    results = collector.collect_all(cfg.devices, source_texts=source_texts)
    for r in results:
        if r.ok:
            gitstore.write_config(cfg.settings.backup_dir, r.device, r.config_text)
    committed = gitstore.commit_changes(cfg.settings.backup_dir)
    return {
        "results": [
            {"device": r.device, "ok": r.ok, "error": r.error or None}
            for r in results
        ],
        "committed": committed,
    }


# --- mutating tool (gated) -------------------------------------------------

def promote_baseline(device: str, confirm: bool = False,
                     config_path: str | None = None) -> dict:
    """Promote a device's backup to its baseline. Mutating + git commit.

    The interactive y/N gate of the CLI becomes an explicit `confirm` parameter
    here (D6: "the gate moves into policy"). With confirm=False this is a no-op
    that just returns the plan, so an agent must be *deliberately* told to write.
    On confirm it writes the baseline AND commits it, exactly like the CLI, so the
    "git history is the timeline" invariant holds for MCP-driven promotes too. The
    baseline dir must therefore be its own git repo (a real git failure raises).
    """
    cfg = _load(config_path)
    plan = promote.plan_promotion(device, cfg.settings.backup_dir, cfg.settings.baseline_dir)

    if not plan.backup_exists:
        return {"device": device, "promoted": False, "reason": "no backup to promote"}
    if not plan.is_initial and not plan.has_drift:
        return {"device": device, "promoted": False, "reason": "already in sync"}
    if not confirm:
        return {"device": device, "promoted": False, "reason": "confirm=False",
                "would_change": _split_diff(plan.diff_lines)}

    path = promote.apply_promotion(device, plan.current_text, cfg.settings.baseline_dir)
    committed = gitstore.commit_changes(
        cfg.settings.baseline_dir, message=f"Promote baseline — {device}"
    )
    return {"device": device, "promoted": True,
            "baseline_path": str(path), "committed": committed}
