# netmiko-config-audit

A Python tool that pulls running-configs from Cisco devices over SSH, version-controls them in git, and flags configuration drift against a golden baseline.

> **Status:** 🚧 In active development (v0.1 scaffold). This README describes the intended design. See the [Roadmap](#roadmap) for what is implemented vs. planned.

## Overview

Networks drift. Someone makes a "temporary" change at 2 a.m., a config gets fat-fingered, or a device reboots into an old startup-config — and nobody notices until something breaks. This tool gives a network its memory back:

- **Knows the intended state** — golden baseline configs (version-controlled)
- **Captures the actual state** — scheduled SSH pull of every device's running-config
- **Explains the gap** — a line-by-line diff that flags exactly what drifted, when, and on which device

It is designed to run unattended (cron on an always-on host) and to emit structured (JSON) output so the results can feed downstream tooling.

> This is **Project 1** of a larger self-hosted NetDevOps platform. It is built standalone, but with deliberate seams — structured output, a git backend, and modular functions — so it can later compose with a syslog event pipeline and an AI correlation layer. See [Roadmap](#roadmap).

## How it works

```
inventory (config.yaml) ──> collector ──> gitstore ──> backup repo (git history = intended/actual state)
                                  │
                                  └──────> drift ──> report (JSON) ──> [future: AI correlation layer]
```

| Module | Responsibility |
| --- | --- |
| `inventory.py` | Load device list + settings; merge credentials from `secrets.env` at runtime |
| `collector.py` | Connect to each device over SSH (Netmiko), pull `show running-config` |
| `gitstore.py`  | Write configs into the backup repo and commit them |
| `drift.py`     | Diff current config vs. golden baseline; flag meaningful changes |
| `report.py`    | Emit a structured JSON summary of the run |

## Repo structure

```
netmiko-config-audit/
├── README.md
├── LICENSE
├── .gitignore
├── pyproject.toml
├── requirements.txt
├── secrets.env.example          # copy -> secrets.env (gitignored)
├── config/
│   └── config.example.yaml      # copy -> config.yaml
├── src/config_audit/
│   ├── __init__.py
│   ├── cli.py                   # `config-audit backup | diff | report`
│   ├── inventory.py             # [implemented] config + secrets loader
│   ├── collector.py             # [skeleton]   Netmiko SSH pull  <- you build
│   ├── gitstore.py              # [implemented] git backend
│   ├── drift.py                 # [skeleton]   golden-baseline diff  <- you build
│   └── report.py                # [skeleton]   JSON run report  <- you build
└── tests/
    └── test_smoke.py
```

## Requirements

- Python 3.10+
- `git` available on PATH
- Network reachability to the target devices over SSH

Python dependencies are in `requirements.txt`. Git is driven via the standard library (`subprocess`) — no extra git dependency.

## Installation

```bash
git clone git@github.com:<you>/netmiko-config-audit.git
cd netmiko-config-audit
python3 -m venv .venv && source .venv/bin/activate
pip install -e .          # installs deps + the `config-audit` command
```

## Configuration

1. Copy the templates:
   ```bash
   cp secrets.env.example secrets.env
   cp config/config.example.yaml config/config.yaml
   ```
2. Edit `secrets.env` with device credentials. **This file is gitignored — never commit it.**
3. Edit `config/config.yaml` with your device addresses and the output paths. Point `backup_dir` at your **separate, private** backup repo — not inside this code repo.

## Usage

```bash
config-audit backup     # pull running-configs and commit them to the backup repo
config-audit diff       # drift check: current configs vs. golden baseline
config-audit report     # emit a JSON summary of the latest run
```

Run nightly via cron on the always-on host, e.g.:

```cron
0 2 * * *  cd /opt/netmiko-config-audit && .venv/bin/config-audit backup
```

## Security

- **Two repos, by design.** This *code* repo is public. The *config backups* live in a separate, private (or local-only) repo. Real running-configs contain SNMP strings, password hashes, VPN pre-shared keys, and your IP plan — they must never land in a public repo. Git history is permanent, so this separation matters from the first commit.
- Credentials live in `secrets.env` (gitignored) and are read at runtime. The repo only ever contains `secrets.env.example` with dummy values.
- `*.cfg` is gitignored by default so a stray local test run can't accidentally commit a real device config here.

## Roadmap

- [x] Repo scaffold, packaging, config + secrets loader, git backend
- [ ] Netmiko collector (`collector.py`)
- [ ] Golden-baseline drift detection (`drift.py`)
- [ ] Structured JSON run report (`report.py`)
- [ ] Scheduled nightly run on the always-on host
- [ ] **Platform stage 2:** syslog event pipeline (actual behavior)
- [ ] **Platform stage 3:** AI correlation layer — read config diffs + logs, summarize changes and likely causes in plain English

## License

MIT — see [LICENSE](LICENSE).
