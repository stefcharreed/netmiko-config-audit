# CLAUDE.md — netmiko-config-audit

Cisco running-config drift auditor + its MCP adapter. **Public portfolio repo** —
keep it honest, clean, and free of planning/strategy (that lives in the private
`network-platform-docs` repo).

## Commands
- Install (tool + tests): `pip install -e ".[dev]"`
- Install (+ MCP server): `pip install -e ".[mcp,dev]"`
- Test: `pytest tests/ -q` — expect **100 passing**; the 4 `test_mcp_server.py` tests
  skip unless the `mcp` SDK is installed (`.[mcp]`), in which case all 104 run.
- Lint: `ruff check .` (config in `pyproject.toml`) — run before committing.
- CLI: `config-audit backup | diff | promote <DEVICE> | report | configure`
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
- **If `secrets.env` already exists, the wizard asks `Re-enter credentials? [y/N]`**
  before doing anything else — declining (or the `[y/N]` default) leaves the file
  untouched with zero further prompts; confirming re-runs the full flow and
  overwrites it. Any test exercising the "file already exists" path now needs an
  `input()` value queued for that question too, not just for the username.
- **Passwords are confirmed (typed twice) via `_prompt_confirmed_password`**, capped
  at 3 attempts before aborting (`SystemExit(1)`) — masked input hides typos, so this
  catches a mistyped password before it's written to `secrets.env` and silently fails
  SSH later. The optional enable/secret prompt skips confirmation entirely on a blank
  first entry. Any test exercising this needs enough `getpass.getpass` return values
  queued for the confirm round-trip, not just one per prompt.
- **`_invalid_secret_reason()` rejects password shapes python-dotenv silently
  corrupts** — confirmed via direct testing: a `' #'` sequence gets truncated as an
  inline comment, and trailing whitespace gets silently stripped, both with zero
  error at read time. A blank required password is also rejected (the optional
  enable/secret can still be blank to skip it). If you touch this, re-verify against
  the actual installed `python-dotenv` version rather than assuming — its comment/
  whitespace handling is what's being defended against, not a made-up rule.
- **Every interactive wizard checks `_interactive()` (`sys.stdin.isatty()`) first.**
  This is load-bearing, not optional style: cron has no stdin, and `input()`/
  `getpass.getpass()` raise `EOFError` on it — confirmed live once already, when the
  secrets re-entry prompt above briefly broke unattended `backup` runs. The rule for
  any new wizard: missing file + non-interactive → fail fast with one clear line and
  `SystemExit(1)`, never a raw traceback; existing file + non-interactive → proceed
  silently, never prompt. Tests must monkeypatch `config_audit.cli._interactive`
  (not `sys.stdin` directly) to force either path — see `tests/test_cli.py`'s
  `_force_interactive` helper.
- **`config-audit configure` builds `config.yaml` interactively** (`_run_config_wizard`
  in `cli.py`), and `_ensure_config_file` auto-launches it only when `config.yaml` is
  missing — unlike secrets.env, an *existing* `config.yaml` is never auto-reprompted
  (it holds a whole device inventory, not one credential pair; nagging on every
  command would be worse than useful). `_prompt_directory` validates `backup_dir`/
  `baseline_dir` before writing anything: rejects a path that resolves inside this
  same code repo (via `gitstore.git_repo_root`, comparing against `Path.cwd()`'s repo
  root — must be a SEPARATE, private repo), and rejects a path that isn't already a
  git working tree (via `gitstore.is_git_repo`) since `commit_changes` requires one.
  `report_path` skips both checks (`require_git=False`) since `report.write_report`
  never commits. Reused `gitstore.is_git_repo`/`git_repo_root` rather than
  duplicating subprocess logic in `cli.py` — keep git subprocess calls in
  `gitstore.py`, not scattered across modules.
- **`config.example.yaml` must say `baseline_dir`, not `golden_dir`.** An earlier
  draft used `golden_dir`, which parses as valid YAML but is silently ignored by
  `inventory.load_config` (which reads `baseline_dir` specifically) — this caused a
  real, confusing failure during hardware validation where `backup_dir` silently
  fell back to a default nobody set. If you ever add a new `settings` key, make sure
  the example file's key name is byte-for-byte what `inventory.py` actually reads.
- **"No baseline yet" is not the same as "drift," and `_cmd_diff`/`_cmd_report` must
  keep treating them separately.** `drift.compare_to_baseline()` can't tell the
  difference itself — an empty baseline vs. a real config always comes back
  `has_drift=True` with the whole file as the "delta." Confirmed live on real
  hardware: a first-ever `diff` on a device with no promoted baseline printed
  almost the entire config as if every line had drifted, which is technically
  correct (nothing to compare against) but reads exactly like the drift-detection
  logic is broken. The CLI layer checks `(baseline_dir / f"{name}.cfg").exists()`
  itself and renders `NO BASELINE` distinctly (cyan, not yellow `DRIFT`) with a
  pointer to `config-audit promote <device>` — don't collapse this back into a
  single status, and don't "fix" it by changing `drift.py`'s comparison logic
  (that logic is correct; `promote.py`'s `is_initial` already handles this exact
  case the same way — this just brings `diff`/`report` in line with it).
  `report.py`'s `RunReport.drifted` JSON field intentionally still includes
  no-baseline devices (stable schema, don't change it) — only the console
  rendering in `cli.py` splits them apart.
- **Never manually copy a file into `baseline_dir`/`backup_dir` — always use
  `promote`/`backup`.** A manual copy skips the git commit (breaks the "who
  approved this and when" audit trail `commit_changes` exists to provide) and
  skips the confirmation diff. It also silently does nothing useful if the
  filename or directory doesn't exactly match what `config.yaml` points at —
  there's no validation on a manual file drop the way there is going through
  the CLI.
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
