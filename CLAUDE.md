# CLAUDE.md — netmiko-config-audit

Cisco running-config drift auditor + its MCP adapter. **Public portfolio repo** —
keep it honest, clean, and free of planning/strategy (that lives in the private
`network-platform-docs` repo).

## Commands
- Install (tool + tests): `pip install -e ".[dev]"`
- Install (+ MCP server): `pip install -e ".[mcp,dev]"`
- Test: `pytest tests/ -q` — expect **76 passing**; the 4 `test_mcp_server.py` tests
  skip unless the `mcp` SDK is installed (`.[mcp]`), in which case all 80 run.
- Lint: `ruff check .` (config in `pyproject.toml`) — run before committing.
- CLI: `config-audit backup | diff | promote <DEVICE> | report`
- MCP server: `CONFIG_AUDIT_CONFIG=config/config.yaml config-audit-mcp`

## Layout
- `src/config_audit/` — the tool: inventory, collector, normalize, drift, gitstore,
  promote, report, sanitize_check, cli.
- `src/config_audit_mcp/` — MCP adapter (tools, registry, server). A **subpackage**
  that imports `config_audit` directly — not a separate repo.
- `tests/` — pytest. `test_*.py` = tool, `test_mcp_*.py` = adapter. Sanitized fixtures
  in `tests/fixtures/`.

## Architecture rules (these govern how you edit — don't break them)
- **Seam discipline:** functions return plain JSON-serializable data (dict/list/str/
  num/bool) and **never print**. Rendering lives in the caller (CLI / MCP). This is
  what lets one function feed both the CLI and the MCP server.
- **normalize() applies to BOTH sides** (baseline and current) before diffing.
  Normalizing one side manufactures phantom drift. Never sort lines — ACL order is
  meaningful.
- **Per-device baselines.** Drift = "did this box change from its own last-approved
  config," not "does it match a fleet template."
- **promote is human-gated.** No `--yes` flag in the CLI; MCP `promote_baseline`
  requires `confirm=True`. Do not add an auto-approve path.
- **git history is the timeline.** One file per device, overwritten each run;
  `git log <device>.cfg` is the change log. No timestamped filenames.

## Safety / risk zones
- **Never commit secrets.** Credentials come from `secrets.env` (gitignored) at
  runtime, merged in `inventory.py`. `config.yaml` holds addressing only.
- **Fixtures must be publish-safe:** RFC 5737 doc IPs, fake hostnames, zero real
  hashes. Run `python -m config_audit.sanitize_check <file>` before any `.cfg` enters
  `tests/fixtures/`.
- **Device baselines/snapshots live in a separate private repo**, never here.
- The live `fetch_running_config` Netmiko body is validated against physical gear, not
  fixtures — don't claim it's "tested" without a hardware run. The owner retypes this
  body himself for muscle memory; **do not rewrite it wholesale.**

## Before saying "done"
1. `pytest tests/ -q` green.
2. `ruff check .` clean.
3. `git status` — confirm no `secrets.env`, real IPs, or hashes staged.
