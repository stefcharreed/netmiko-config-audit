"""config-audit MCP server.

Thin FastMCP wrapper that exposes Project 1's drift/promote functions to an LLM.
All real logic lives in tools.py (pure, tested); this file only registers those
functions as MCP tools and starts the transport. Keeping the SDK boundary this
thin means the server can be re-pointed at a different SDK version with almost
no change, and the logic stays testable without the SDK installed.

Run locally (stdio) for Claude Desktop / Cursor:
    CONFIG_AUDIT_CONFIG=/path/to/config.yaml python -m config_audit_mcp.server

Tool surface:
    read-only   list_devices, get_drift, get_drift_all, plan_promotion, get_config
    mutating    promote_baseline  (requires confirm=True — the human gate as policy)
    live        backup_now        (NEEDS real gear — the one tool that opens SSH)
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import tools

mcp = FastMCP("config-audit")


@mcp.tool()
def list_devices() -> list[dict]:
    """List the network devices under management (name, host, device_type)."""
    return tools.list_devices()


@mcp.tool()
def get_drift(device: str) -> dict:
    """Report configuration drift for one device: how its live backup differs from
    its approved baseline, with added/removed lines. Use this to answer 'did
    anything change on <device>?'."""
    return tools.get_drift(device)


@mcp.tool()
def get_drift_all() -> list[dict]:
    """Report drift across every managed device at once — the network-wide
    'what has changed anywhere?' snapshot."""
    return tools.get_drift_all()


@mcp.tool()
def plan_promotion(device: str) -> dict:
    """Show what promoting a device's current backup into its baseline WOULD
    change, without changing anything. Read-only analysis."""
    return tools.plan_promotion(device)


@mcp.tool()
def get_config(device: str, which: str = "current") -> dict:
    """Return a stored config for a device. `which` is 'current' (latest backup)
    or 'baseline' (approved reference)."""
    return tools.get_stored_config(device, which)


@mcp.tool()
def promote_baseline(device: str, confirm: bool = False) -> dict:
    """Promote a device's current backup to be its new approved baseline. This
    MUTATES the baseline and must be confirmed: with confirm=False it only
    reports what would change. Only set confirm=True when a human has explicitly
    approved promoting this exact diff."""
    return tools.promote_baseline(device, confirm=confirm)


# backup_now (live SSH pull) is intentionally NOT registered in this scaffold.
# It is the only tool that needs real gear, and wiring it before the physical-
# gear validation of Project 1 is done would expose an unvalidated live path.
# Add it here once collector.fetch_running_config is proven against hardware:
#
#   @mcp.tool()
#   def backup_now(device: str) -> dict:
#       """Pull a fresh running-config from the device over SSH and store it."""
#       return tools.backup_now(device)   # implement in tools.py at that point


def main() -> None:
    """Entry point: serve over stdio (local clients like Claude Desktop)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
