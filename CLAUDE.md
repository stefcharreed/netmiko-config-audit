# CLAUDE.md — netmiko-config-audit

Cisco running-config drift auditor + its MCP adapter. **Public portfolio repo** —
keep it honest, clean, and free of planning/strategy (that lives in the private
`network-platform-docs` repo).

## Commands
- Install (tool + tests): `pip install -e ".[dev]"`
- Install (+ MCP server): `pip install -e ".[mcp,dev]"`
- Test: `pytest tests/ -q` — expect **79 passing**; the 4 `test_mcp_server.py` tests
  skip unless the `mcp` SDK is installed (`.[mcp]`), in which case all 83 run.
- Lint: `ruff check .` (config in `pyproject.toml`) — run before committing.
- CLI: `config-audit backup | diff | promote <DEVICE> | report`
- MCP server: `CONFIG_AUDIT_CONFIG=config/config.yaml config-audit-mcp`
- Docker runtime image: `docker build -t netmiko-audit .`
- Docker test stage (runs the real suite inside the image): `docker build --target test -t netmiko-audit:test .`

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
- **`rich` rendering stays inside `cli.py`, never in the logic layer.** `cli.py` uses
  `rich` (tables, colored diffs, panels) purely as presentation on top of the plain
  data the logic functions already return — it does not change what those functions
  return. If you add a new rendered view, format it in `cli.py`'s `_cmd_*` functions,
  not by changing `drift.py`/`report.py`/etc. to know about styling.
- **The `promote` y/N gate uses plain `input()`, not a `rich.Prompt`.** Deliberate:
  `tests/test_cli.py` monkeypatches `builtins.input` directly to test the abort path,
  and `rich.Prompt`'s input handling isn't guaranteed to route through the same hook.
  Keep it as `input()` unless you also update that test's approach.
- **The first-run secrets wizard (`_ensure_secrets_file`) only fires for `backup` and
  `report`** (the two commands that actually need live-device credentials) — `diff`
  and `promote` are file-only and would be prompted for nothing useful. It also uses
  plain `input()`/`getpass.getpass()`, not rich prompts, for the same testability
  reason as the promote gate above. If a future test calls `main()` with `backup` or
  `report` against a temp dir with no `secrets.env`, it must monkeypatch both or the
  test will hang/error waiting on real stdin.
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
- **The Docker image needs `git` on PATH** (base stage installs it via apt) — `gitstore.py`
  shells out to `git` for every commit, so a base image without it builds fine but fails
  the moment `backup`/`promote` actually runs. This bit us once already; if the base image
  ever changes, re-verify `git` is still installed, don't assume.
- `config.yaml`, `secrets.env`, and the backup/baseline dirs are gitignored and external
  to the repo — mount them into the container at run time, never `COPY` them into the
  image (secrets baked into an image layer are recoverable from history even after a
  later layer deletes them).

## Before saying "done"
1. `pytest tests/ -q` green.
2. `ruff check .` clean.
3. `git status` — confirm no `secrets.env`, real IPs, or hashes staged.
4. If the `Dockerfile` changed: `docker build --target test -t netmiko-audit:test .` green,
   and a real command (not just `--help`) run against mounted fixtures still gives the
   expected output — building/`--help` alone does not prove the image works.
