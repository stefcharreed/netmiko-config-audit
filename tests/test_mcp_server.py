"""Verifies the real FastMCP wiring: that server.py registers exactly the registry's
tools with the SDK, with correct names/descriptions and — importantly — that the
internal config_path argument never leaks into the schema an LLM sees.

These need the MCP SDK installed, so they importorskip. That's deliberate, and
consistent with the project's philosophy elsewhere: validate the real integration
against the real dependency rather than a mock. Where the SDK isn't present (e.g. a
minimal CI lane), they skip cleanly; the surface and behavior are still fully covered
by test_registry.py and test_capabilities.py without the SDK.
"""
import asyncio

import pytest

pytest.importorskip("mcp")  # FastMCP SDK; required for these wiring checks

from config_audit_mcp import server  # noqa: E402  (after importorskip)
from config_audit_mcp.registry import REGISTRY  # noqa: E402


def _list_tools():
    """The tools FastMCP actually registered, via the SDK's async list_tools()."""
    return asyncio.run(server.mcp.list_tools())


def test_server_registers_every_registry_tool():
    """FastMCP's advertised tools match the registry exactly — nothing dropped or added."""
    registered = {t.name for t in _list_tools()}
    assert registered == {s.name for s in REGISTRY}


def test_registered_tools_carry_descriptions():
    """Every advertised tool has a non-empty description for the LLM to select on."""
    for t in _list_tools():
        assert t.description and t.description.strip()


def test_get_drift_schema_exposes_device_and_hides_config_path():
    """The LLM sees `device` but never the internal `config_path` argument."""
    tool = next(t for t in _list_tools() if t.name == "get_drift")
    props = tool.inputSchema.get("properties", {})
    assert "device" in props
    assert "config_path" not in props


def test_promote_baseline_schema_exposes_confirm():
    """The safety gate parameter is part of the promote tool's schema."""
    tool = next(t for t in _list_tools() if t.name == "promote_baseline")
    props = tool.inputSchema.get("properties", {})
    assert "confirm" in props
