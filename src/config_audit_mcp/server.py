"""config-audit MCP server.

Thin FastMCP wrapper that exposes Project 1's drift/promote capabilities to an LLM.
The capability surface is defined as data in registry.py; this file does nothing but
register each entry with FastMCP and start the transport. Keeping the SDK boundary
this thin means the logic (tools.py) and the surface (registry.py) stay testable
without the SDK installed, and the server can follow the SDK across versions with
almost no change.

Run locally (stdio) for Claude Desktop / Cursor:
    CONFIG_AUDIT_CONFIG=/path/to/config.yaml python -m config_audit_mcp.server
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .registry import REGISTRY

mcp = FastMCP("config-audit")

# Register every tool in the registry. One source of truth: a tool that exists in
# the registry is exposed; a tool that isn't, isn't.
for _spec in REGISTRY:
    mcp.tool(name=_spec.name, description=_spec.description)(_spec.handler)


def main() -> None:
    """Entry point: serve over stdio (local clients like Claude Desktop)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
