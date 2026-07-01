"""The MCP tool surface, as data — one entry per capability an agent can call.

Why a registry instead of decorating functions inline in server.py: it makes the
question "what can this server actually do?" answerable as a plain list, which means
the surface is testable WITHOUT standing up the MCP SDK, and adding a tool can't
silently forget to register it (server.py and the tests both read this one list).

The handlers here are the LLM-facing layer: clean signatures with NO `config_path`
parameter (the running server resolves the config from the CONFIG_AUDIT_CONFIG env
var), each delegating to the already-tested logic in tools.py. Keeping config_path
out of these signatures keeps it out of the JSON schema the LLM sees.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import tools


@dataclass(frozen=True)
class ToolSpec:
    """One MCP tool: its public name, the callable behind it, and its properties."""
    name: str
    handler: Callable
    description: str
    mutating: bool = False    # writes state — must be explicitly confirmed
    needs_gear: bool = False  # opens a live SSH session — needs real hardware


# --- LLM-facing handlers (no config_path; resolved from env by tools.py) ----

def _list_devices() -> list[dict]:
    return tools.list_devices()


def _get_drift(device: str) -> dict:
    return tools.get_drift(device)


def _get_drift_all() -> list[dict]:
    return tools.get_drift_all()


def _plan_promotion(device: str) -> dict:
    return tools.plan_promotion(device)


def _get_config(device: str, which: str = "current") -> dict:
    return tools.get_stored_config(device, which)


def _promote_baseline(device: str, confirm: bool = False) -> dict:
    return tools.promote_baseline(device, confirm=confirm)


def _backup_now() -> dict:
    return tools.backup_now()


# --- the surface ------------------------------------------------------------

REGISTRY: list[ToolSpec] = [
    ToolSpec(
        "list_devices", _list_devices,
        "List the network devices under management (name, host, device_type).",
    ),
    ToolSpec(
        "get_drift", _get_drift,
        "Report configuration drift for one device: how its latest backup differs "
        "from its approved baseline, as added/removed config lines. Use this to "
        "answer 'did anything change on <device>?'.",
    ),
    ToolSpec(
        "get_drift_all", _get_drift_all,
        "Report configuration drift across every managed device at once — the "
        "network-wide 'what has changed anywhere?' snapshot.",
    ),
    ToolSpec(
        "plan_promotion", _plan_promotion,
        "Show what promoting a device's current backup into its baseline would "
        "change, without changing anything. Read-only.",
    ),
    ToolSpec(
        "get_config", _get_config,
        "Return a stored config for a device: which='current' (latest backup) or "
        "which='baseline' (approved reference).",
    ),
    ToolSpec(
        "promote_baseline", _promote_baseline,
        "Promote a device's current backup to be its new approved baseline. This "
        "MUTATES state and commits: with confirm=False it only reports what would "
        "change; set confirm=True only when a human has approved the exact diff.",
        mutating=True,
    ),
    ToolSpec(
        "backup_now", _backup_now,
        "Pull every managed device's live running-config over SSH right now and "
        "commit the results to the backup repo. This is the one tool that "
        "contacts real network gear — everything else here is file-based.",
        mutating=True,
        needs_gear=True,
    ),
]
