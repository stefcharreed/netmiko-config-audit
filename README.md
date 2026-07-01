# netmiko-config-audit

*Built as part of a NetDevOps portfolio.*

A Python tool that pulls running-configs from Cisco devices over SSH, version-controls them in git, and flags configuration drift against a per-device baseline.

> [![tests](https://github.com/stefcharreed/netmiko-config-audit/actions/workflows/tests.yml/badge.svg)](https://github.com/stefcharreed/netmiko-config-audit/actions/workflows/tests.yml)

> **Status:** 🚧 v1.1 — feature-complete and tested offline. The full pipeline (collect → normalize → drift → promote → report) is implemented and covered by an 87-test suite that passes against sanitized fixtures (plus 4 SDK-gated MCP wiring tests that run when the mcp package is installed). The **one** remaining step before I call it production-ready is validating the live SSH pull and normalization against physical ISR/Catalyst gear — see the [Roadmap](#roadmap). I'm foregrounding that gap on purpose: a drift tool's whole credibility is that it doesn't cry drift when nothing changed, and that can only be proven against real hardware.

## Overview

Networks drift. Someone makes a "temporary" change at 2 a.m., a config gets fat-fingered, or a device reboots into an old startup-config — and nobody notices until something breaks. This tool gives a network its memory back:

- **Knows the intended state** — per-device baseline configs (version-controlled)
- **Captures the actual state** — scheduled SSH pull of every device's running-config
- **Explains the gap** — a normalized, line-by-line diff that flags exactly what drifted, on which device

It runs unattended (cron on an always-on host) and emits structured (JSON) output so the results can feed downstream tooling.

> This is **Project 1** of a larger self-hosted NetDevOps platform. It is built standalone, but with deliberate seams — structured output, a git backend, modular functions, and an MCP adapter — so it can later compose with a syslog event pipeline (stage 2) and an AI correlation layer (stage 3, in progress in a private repo — [message me on LinkedIn](https://www.linkedin.com/in/stefan-c-reed/) if you want to know more).

## How it works

```
inventory (config.yaml) ──> collector ──> gitstore ──> backup repo (git history = actual state over time)
                                  │
                                  └──> drift (normalize both sides) ──> report (JSON) ──> [future: AI layer]
```

| Module | Responsibility |
| --- | --- |
| `inventory.py` | Load device list + settings; merge credentials from `secrets.env` at runtime |
| `collector.py` | Connect to each device over SSH (Netmiko), pull `show running-config` |
| `normalize.py` | Strip volatile noise (headers, NTP clock, blank/`!` lines) from a config |
| `drift.py`     | Diff current vs. per-device baseline, after normalizing **both** sides |
| `gitstore.py`  | Write configs into the backup repo and commit them |
| `promote.py`   | Plan/apply a human-approved promotion of a drifted config into the baseline |
| `report.py`    | Emit a structured JSON summary of the run |
| `sanitize_check.py` | Lint a config for real IPs / hashes / SNMP strings before it's committed |

### On drift detection

Drift is computed against each device's **own** baseline (`baselines/<device>.cfg`) — never a single shared template. Heterogeneous gear means a shared template would flag everything as drift. `normalize()` is a pure function applied **identically to both the baseline and the current config** before diffing; normalizing only one side manufactures phantom drift, so this rule is load-bearing. It strips known-volatile lines (config header, `ntp clock-period`, blank/separator lines) but deliberately **keeps** password hashes (a changed credential is real drift) and line ordering (an ACL reorder is meaningful).

## Repo structure

```
netmiko-config-audit/
├── README.md
├── LICENSE
├── .gitignore
├── Dockerfile                    # multi-stage: test (runs the suite) + runtime (non-root)
├── .dockerignore
├── pyproject.toml
├── requirements.txt
├── secrets.env.example          # copy -> secrets.env (gitignored)
├── config/
│   └── config.example.yaml      # copy -> config/config.yaml
├── src/config_audit/
│   ├── __init__.py
│   ├── cli.py                   # `config-audit backup | diff | promote | report`
│   ├── inventory.py             # config + secrets loader
│   ├── collector.py             # Netmiko SSH pull (offline-testable via source_text)
│   ├── normalize.py             # config normalization (pure, both-sides)
│   ├── drift.py                 # per-device baseline diff
│   ├── gitstore.py              # git backend
│   ├── promote.py               # human-gated baseline promotion (plan / apply)
│   ├── report.py                # JSON run report
│   └── sanitize_check.py        # pre-commit config linter
├── src/config_audit_mcp/            ← MCP adapter (optional, see below)
│   ├── registry.py              # tool surface as data — single source of truth
│   ├── server.py                # FastMCP glue (thin — registers the registry)
│   ├── tools.py                 # pure tool logic; no MCP types; tested without SDK
│   └── README.md                # MCP tool surface + install/run instructions
└── tests/                       # 87 tests (pytest); no live gear, no network
    ├── test_*.py                # Project 1 — 62 tests
    ├── test_mcp_*.py            # MCP adapter — 25 offline + 4 SDK-gated
    └── fixtures/                # sanitized configs (RFC 5737 IPs, fake hosts, zero creds)
```

## Requirements

- Python 3.10+
- `git` available on PATH
- Network reachability to the target devices over SSH
- Docker (optional — see [Docker](#docker) below; not required to run natively)

Git is driven via the standard library (`subprocess`) — no extra git dependency.

## Installation

**Check your Python first** — `python3 -m venv` silently uses whatever `python3` resolves
to, which on a stock Mac is often the old Apple-bundled 3.9, not 3.10+. If that's what you
have, the failure mode is confusing: an old bundled `pip` can't even report the real
"wrong Python version" error clearly and instead fails on an unrelated-looking editable-
install message. Check before you start:

```bash
python3 --version   # need 3.10+; if not, install a newer one (e.g. `brew install python@3.12`
                     # on macOS) and use that interpreter explicitly below
```

```bash
git clone git@github.com:stefcharreed/netmiko-config-audit.git
cd netmiko-config-audit
python3 -m venv .venv && source .venv/bin/activate   # or python3.12 -m venv .venv, etc.
pip install --upgrade pip                             # old pip can't do pyproject-only editable installs
pip install -e .          # installs deps + the `config-audit` command
```

No newer Python available and don't want to manage one? Skip the venv entirely and use
[Docker](#docker) instead — `docker build -t netmiko-audit .` handles the Python version
for you.

## Configuration

1. Copy the config template:
   ```bash
   cp config/config.example.yaml config/config.yaml
   ```
   Edit it with device addresses and output paths. Point `backup_dir` and `baseline_dir` at your **separate, private** backup repo — not inside this code repo.
2. Credentials in `secrets.env` — two ways to set them up:
   - **Interactive (first run):** just run `config-audit backup` or `config-audit report` with no `secrets.env` present — you'll be prompted for a default username/password (and optional enable secret), and the file is written for you.
   - **Manual:** `cp secrets.env.example secrets.env` and edit it directly.

   Either way, `secrets.env` is **gitignored — never commit it.**

## Usage

```bash
config-audit backup     # pull running-configs and commit them to the backup repo
config-audit diff       # drift check: current backups vs. per-device baseline
config-audit promote <DEVICE>   # review a device's drift, then approve it into the baseline
config-audit report     # pull, drift-check, and write a JSON run summary
```

Output is rendered with [`rich`](https://github.com/Textualize/rich) — colored tables for
per-device status, colored unified diffs for drift (green additions, red removals). This is
presentation only: every function under `src/config_audit/` still returns plain
JSON-serializable data and never prints (see Architecture rules in `CLAUDE.md`) — the CLI is
the only place rendering happens, so a future MCP/AI layer reads the same unstyled data.

`promote` shows the exact diff and waits for an interactive `y/N` before it writes — there is **no `--yes` flag, by design.** Promoting a config into the baseline is a human/policy judgment, not something a script should do unattended. Exit codes: `0` promoted or already in sync, `1` drift found but you declined, `2` no backup to promote.

Run nightly via cron on the always-on host:

```cron
0 2 * * *  cd /opt/netmiko-config-audit && .venv/bin/config-audit backup
```

## Development / offline testing

The collector takes an optional `source_text`, so the whole pipeline can be developed
and unit-tested against saved configs with **no live device** — develop against a saved
config by day, point at real gear at night:

```python
from config_audit.collector import fetch_running_config
result = fetch_running_config(device, source_text=open("tests/fixtures/ISR1.cfg").read())
```

The `diff` command is entirely file-based and needs no device at all.

## Testing

87 tests cover the offline pipeline end to end (62 for the tool, 25 for the MCP adapter). They need no live gear, no network, and no Netmiko — the collector's `source_text` seam lets the whole pipeline run against saved configs, so the suite is pure and fast:

```bash
pip install -e ".[dev]"          # tool + tests
pip install -e ".[mcp,dev]"     # also installs MCP SDK; adds 4 SDK-wiring tests
pytest tests/ -q
```

What the suite actually proves, rather than just exercising lines:

- **Phantom-drift guard** (`test_normalize`) — a baseline and a noise-only-different current config normalize to *identical* text, so the tool reports no drift when nothing meaningful changed. This is the single most important property: it's what separates a useful drift tool from a noisy one.
- **Real drift is caught** (`test_drift`) — description edits, added ACL lines, a removed `passive-interface`, and a VLAN move on a switchport are each detected.
- **The promotion gate is sound** (`test_promote`) — plan is pure analysis across all four states (no backup / initial baseline / in sync / drifted) and never writes; apply is the single write and round-trips exactly.
- **Nothing sensitive leaks** (`test_sanitize_check`) — every fixture is clean, while type-7/secret/`$9$` hashes, SNMP strings, and real public/RFC 1918 IPs are all flagged. Wildcard masks (`0.0.0.255`) are correctly treated as masks, not private hosts.

All fixtures are sanitized — RFC 5737 documentation IPs, fake hostnames, zero credentials — and every one must pass `sanitize_check.py` before it's allowed into `tests/fixtures/`.

## Docker

A multi-stage `Dockerfile` packages the CLI for containerized runs. `git` is installed
in the image (`gitstore.py` shells out to it for every commit), and the runtime stage
drops root, running as a dedicated `appuser`.

```bash
docker build -t netmiko-audit .                        # runtime image (default target)
docker build --target test -t netmiko-audit:test .     # runs the 87-test suite inside the image; build fails on any failure
```

`config.yaml`, `secrets.env`, and the backup/baseline directories are gitignored and
external to this repo **by design** (see [Security](#security) below) — mount them in
at run time, never bake them into the image:

```bash
docker run --rm \
  -v $(pwd)/config/config.yaml:/app/config/config.yaml:ro \
  -v $(pwd)/secrets.env:/app/secrets.env:ro \
  -v /path/to/private-backup-repo:/path/to/private-backup-repo \
  netmiko-audit -c config/config.yaml diff
```

The third mount's host and container paths should match whatever `backup_dir` /
`baseline_dir` point to in `config.yaml` — those paths are read as-given, not remapped.

CI builds and tests the image on every push/PR (the `docker` job in
[`tests.yml`](.github/workflows/tests.yml)), in addition to the host-based test matrix
across Python 3.10–3.12.

## Security

- **Two repos, by design.** This *code* repo is public. The *config backups* live in a separate, private repo. Real running-configs contain SNMP strings, password hashes, VPN keys, and your IP plan — they must never land in a public repo. Git history is permanent, so this separation matters from the first commit.
- Credentials live in `secrets.env` (gitignored), read at runtime. The repo only ever contains `secrets.env.example` with dummy values.
- `*.cfg` is gitignored so a stray local run can't commit a real config here. Sanitized test fixtures are the one exception (re-included under `tests/fixtures/`), and every fixture must pass `sanitize_check.py` first.

## MCP server (optional)

This repo also ships an MCP adapter at `src/config_audit_mcp/` that exposes the
drift/promote tools to an LLM, so an assistant can answer "did anything change on
CORE1?" by calling real tools. It's a subpackage, not a separate repo — it imports
`config_audit` directly. The MCP SDK is an optional dependency, pulled only when you
run the server:

```bash
pip install -e ".[mcp]"
CONFIG_AUDIT_CONFIG=config/config.yaml config-audit-mcp
```

See `src/config_audit_mcp/README.md` for the tool surface and design.

## Roadmap

- [x] Repo scaffold, packaging, config + secrets loader, git backend
- [x] Netmiko collector with offline `source_text` seam
- [x] Per-device baseline drift detection with shared `normalize()`
- [x] Structured JSON run report
- [x] Pre-commit config sanitizer (`sanitize_check.py`)
- [x] Human-gated `promote` (approve a drift into the baseline)
- [x] 87-test suite: 62 tool tests (phantom-drift guard, drift detection, promote gate, sanitizer, secrets wizard + confirmation + validation) + 25 MCP adapter tests
- [x] Containerized: multi-stage `Dockerfile` (test stage runs the real suite inside the image; runtime stage drops root), wired into CI
- [x] Terminal UX: `rich`-rendered tables/colored diffs, interactive first-run secrets setup for `backup`/`report` — presentation only, no change to the underlying JSON-serializable data
- [ ] Validate collector + normalization against physical ISR/Catalyst *(the one open item before production-ready)*
- [ ] Scheduled nightly run on the always-on host
- [ ] **Platform stage 2:** syslog event pipeline (actual behavior) — not started, no repo yet
- [ ] **Platform stage 3:** AI correlation layer — composes this tool's MCP server with a CCNP-grounded knowledge base to diagnose real network problems end-to-end. In progress in a private repo, not described here to avoid two copies of the same plan drifting out of sync — [message me on LinkedIn](https://www.linkedin.com/in/stefan-c-reed/) if you want to know more.

## License

MIT — see [LICENSE](LICENSE).
