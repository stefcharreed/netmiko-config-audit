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

Three files, one boundary:

| File | Role |
| --- | --- |
| `src/config_audit_mcp/tools.py` | **Logic.** Pure functions that call Project 1 and return dicts/lists. No MCP types. |
| `src/config_audit_mcp/registry.py` | **Surface.** One declarative list of the tools the server exposes (name, handler, description, mutating/needs-gear). Single source of truth. |
| `src/config_audit_mcp/server.py` | **SDK glue only.** Loops the registry and registers each entry with FastMCP, then starts the transport. |

The registry exists so "what can this server do?" is answerable as data — which means the
surface is testable without standing up the SDK, and adding a tool can't silently skip
registration (server.py and the tests both read the one list).

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
- Lives in the same package as Project 1 (`config_audit`), so it imports it directly — no cross-repo install.

## Install & run

```bash
# from the repo root — the MCP adapter ships in this same package:
pip install -e ".[mcp,dev]"       # config_audit + config_audit_mcp + SDK + pytest

# point the server at your config.yaml and run over stdio (local clients):
CONFIG_AUDIT_CONFIG=/path/to/config/config.yaml python -m config_audit_mcp.server
# or via the console script:
CONFIG_AUDIT_CONFIG=/path/to/config/config.yaml config-audit-mcp
```

To register with a local MCP client (e.g. Claude Desktop), add a server entry that
runs that command with the `CONFIG_AUDIT_CONFIG` env var set. For remote use, run
with streamable-HTTP transport instead of stdio.

## Tests

Four layers, 29 tests:

```bash
pip install -e ".[mcp,dev]"
pytest tests/ -q
```

| File | Layer | What it proves | Needs SDK? |
| --- | --- | --- | --- |
| `test_tools.py` | unit | each wrapper reshapes Project 1's output correctly | no |
| `test_registry.py` | surface | the exposed tool set + safety classification stay correct | no |
| `test_capabilities.py` | acceptance | what an agent can *accomplish* end-to-end (drift Q&A, network-wide snapshot, the safety gate, the full promote-and-verify loop, unknown-device handling, initial baseline) | no |
| `test_server.py` | wiring | FastMCP actually registers the registry's tools, and the internal `config_path` never leaks into the LLM-facing schema | **yes** |

The first three run anywhere (no SDK, no gear) and cover the surface and behavior fully.
`test_server.py` validates the real SDK integration and so `importorskip`s the `mcp`
package — it runs where the SDK is installed and skips cleanly where it isn't, the same
way Project 1 validates its live device path against real gear rather than a mock.

## Status

🚧 Scaffold. Tool logic is implemented and tested against Project 1's real functions.
The `server.py` SDK registration is written against the official `mcp` SDK (1.x) but
is unverified end-to-end until installed in an environment with the SDK. `backup_now`
is deferred until Project 1's physical-gear validation lands.
