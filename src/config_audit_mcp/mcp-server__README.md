# config-audit-mcp

An MCP server that exposes [`netmiko-config-audit`](../../) (Project 1) to an LLM,
so an assistant can answer questions like *"did anything change on CORE1?"* or
*"show me drift across the whole network"* by calling real tools instead of guessing.

> **Why this exists as the first thing after Project 1.** Project 1's functions
> were built to a frozen contract: they return plain JSON-serializable data and
> never print (see its DECISIONS log). That seam means an MCP server is almost
> free — it doesn't re-implement drift or promotion, it just registers Project 1's
> already-tested functions as tools and reshapes the output for an LLM. Because the
> contract is frozen and unit-tested, this wrapper does **not** rot if Project 1's
> internals are tuned during hardware validation; only the data changes, not the
> shapes.

## Design

Two files, one boundary:

| File | Role |
| --- | --- |
| `src/config_audit_mcp/tools.py` | **All logic.** Pure functions that call Project 1 and return dicts/lists. No MCP types. Unit-tested with no SDK installed. |
| `src/config_audit_mcp/server.py` | **SDK glue only.** Registers the `tools.py` functions as MCP tools and starts the transport. |

Keeping the SDK boundary that thin is deliberate: the MCP SDK is moving fast
(the official `mcp` package ships a breaking v2 in mid-2026), so the value —
the wrapping logic — stays in a file that doesn't depend on the SDK at all, and
the part that *does* is a dozen one-line registrations.

## Tool surface

| Tool | Kind | Needs gear? |
| --- | --- | --- |
| `list_devices` | read-only | no |
| `get_drift(device)` | read-only | no |
| `get_drift_all()` | read-only | no |
| `plan_promotion(device)` | read-only | no |
| `get_config(device, which)` | read-only | no |
| `promote_baseline(device, confirm)` | **mutating** (needs `confirm=True`) | no |
| `backup_now(device)` | **live SSH** — *not yet registered* | **yes** |

The interactive `y/N` gate from the CLI becomes the explicit `confirm` parameter on
`promote_baseline`: with `confirm=False` it only reports what *would* change, so an
agent has to be deliberately instructed to write. `backup_now` is intentionally left
unregistered until Project 1's live path is validated against physical gear — wiring
an unvalidated SSH path into an agent tool is exactly the wrong order.

## Requirements

- Python 3.10+
- The official MCP SDK, pinned below v2: `mcp>=1.27,<2`
- Project 1 (`config-audit`) importable — install it editable alongside this.

## Install & run

```bash
# from a venv that also has Project 1 installed editable:
pip install -e ../../              # Project 1 (config-audit)
pip install -e ".[dev]"            # this server + pytest

# point the server at your config.yaml and run over stdio (local clients):
CONFIG_AUDIT_CONFIG=/path/to/config/config.yaml python -m config_audit_mcp.server
```

To register with a local MCP client (e.g. Claude Desktop), add a server entry that
runs that command with the `CONFIG_AUDIT_CONFIG` env var set. For remote use, run
with streamable-HTTP transport instead of stdio.

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ -q
```

The suite proves the wrapping is correct — credentials excluded from `list_devices`,
drift reshaped into added/removed, unknown devices flagged, and `promote_baseline`
refusing to write without `confirm=True`. Drift *detection* itself is covered by
Project 1's own suite; this layer only tests the reshaping.

## Status

🚧 Scaffold. Tool logic is implemented and tested against Project 1's real functions.
The `server.py` SDK registration is written against the official `mcp` SDK (1.x) but
is unverified end-to-end until installed in an environment with the SDK. `backup_now`
is deferred until Project 1's physical-gear validation lands.
