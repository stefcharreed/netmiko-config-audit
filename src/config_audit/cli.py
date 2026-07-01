"""Command-line entry point.

    backup   Pull running-configs from all devices and commit them to git.
    diff     Compare current backups against the per-device baseline (drift check).
    report   Pull, drift-check, and emit a structured JSON summary of the run.
    promote  Bless a device's current backup as its new baseline (human-gated).

Rendering lives entirely in this module — every function it calls into
(collector, drift, report, promote, gitstore) returns plain data and never
prints. Swapping the renderer (plain print -> rich) never touches that data,
which is also what a later MCP/AI layer reads unchanged.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .inventory import load_config

console = Console()


def _interactive() -> bool:
    """True if a real user is at the keyboard, not cron/a script with no stdin.

    Any wizard that only makes sense with a human present must check this first --
    input()/getpass.getpass() raise EOFError on closed/absent stdin, which would
    otherwise crash an unattended `backup` run that cron is depending on.
    """
    return sys.stdin.isatty()


def _render_diff(lines: list[str]) -> Text:
    """Color a unified diff's lines: additions green, removals red, hunk headers dim."""
    text = Text()
    for line in lines:
        if line.startswith("+"):
            style = "green"
        elif line.startswith("-"):
            style = "red"
        elif line.startswith("@@"):
            style = "cyan"
        else:
            style = None
        text.append(line + "\n", style=style)
    return text


_MAX_PASSWORD_ATTEMPTS = 3


def _invalid_secret_reason(value: str) -> str | None:
    """Return why `value` is unsafe to write as a raw secrets.env value, or None if fine.

    secrets.env is read back by python-dotenv, which silently mangles some shapes
    instead of erroring -- confirmed: a ' #' sequence truncates everything after it
    (treated as an inline comment), and trailing whitespace is silently stripped.
    Both would corrupt a real credential with no visible error until a confusing
    SSH auth failure much later.
    """
    if " #" in value:
        return (
            "contains ' #' (space then #) -- python-dotenv reads that as a comment "
            "and would silently cut it off there"
        )
    if value != value.rstrip():
        return "has trailing whitespace -- python-dotenv silently strips it when read back"
    if "\n" in value or "\r" in value:
        return "contains a newline -- can't be stored on a single secrets.env line"
    return None


def _prompt_confirmed_password(label: str, *, optional: bool = False) -> str:
    """Prompt for a password twice and require they match; retries on mismatch,
    a blank required value, or a value that secrets.env would silently corrupt.

    Masked input means a typo is invisible until it fails an SSH login later --
    catch it here instead. `optional=True` lets an empty first entry skip
    confirmation and validation entirely (used for the enable/secret prompt,
    which is skippable).
    """
    for attempt in range(_MAX_PASSWORD_ATTEMPTS):
        value = getpass.getpass(f"{label}: ")
        if optional and not value:
            return value
        remaining = _MAX_PASSWORD_ATTEMPTS - attempt - 1
        suffix = f" {remaining} attempt(s) left." if remaining else ""

        if not value:
            console.print(f"[red]Can't be blank.[/red]{suffix}")
            continue
        confirm = getpass.getpass(f"Confirm {label.lower()}: ")
        if value != confirm:
            console.print(f"[red]Didn't match.[/red]{suffix}")
            continue
        problem = _invalid_secret_reason(value)
        if problem:
            console.print(f"[red]{label} {problem}.[/red]{suffix}")
            continue
        return value
    console.print("[red]Too many failed attempts — aborting setup. Run the command again.[/red]")
    raise SystemExit(1)


def _ensure_secrets_file(secrets_path: Path) -> None:
    """First-run setup: prompt for default device credentials if secrets.env is
    missing, or offer to re-enter (overwrite) them if it already exists.

    Only called by commands that actually talk to live devices (backup, report) —
    diff/promote are file-only and need no credentials. Plain input()/getpass, not a
    rich Prompt, so this stays trivially monkeypatchable in tests the same way the
    existing promote confirmation gate already is.

    Every prompt here is gated on `_interactive()` first: cron has no stdin, so any
    of these would otherwise crash a scheduled `backup` run with EOFError instead of
    either running quietly (file already exists) or failing with a clear message
    (file missing) -- this bit us once already, see CLAUDE.md.
    """
    first_run = not secrets_path.exists()
    if not first_run:
        if not _interactive():
            return  # unattended: never nag, just proceed with what's already there
        resp = input(f"{secrets_path} already exists. Re-enter credentials? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            return
    elif not _interactive():
        console.print(
            f"[red]{secrets_path} not found[/red] and no interactive terminal attached. "
            f"Run `config-audit backup` (or `report`) interactively once to set it up, "
            f"or create it manually — see README."
        )
        raise SystemExit(1)

    console.print(
        Panel(
            (
                f"No secrets.env found. Enter default device credentials — saved to "
                f"[bold]{secrets_path}[/bold] (gitignored, never committed)."
            )
            if first_run
            else (
                f"Re-entering default device credentials — this overwrites "
                f"[bold]{secrets_path}[/bold]."
            ),
            title="First-run setup" if first_run else "Re-enter credentials",
            border_style="cyan",
        )
    )
    username = input("Default username: ").strip()
    password = _prompt_confirmed_password("Default password")
    secret = _prompt_confirmed_password(
        "Enable/secret password (optional, Enter to skip)", optional=True
    )

    lines = [
        "# Written by config-audit's first-run setup. Never commit this file.",
        f"NET_USERNAME={username}",
        f"NET_PASSWORD={password}",
    ]
    if secret:
        lines.append(f"NET_SECRET={secret}")
    secrets_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"[green]Wrote {secrets_path}[/green]\n")


def _prompt_directory(label: str, *, require_git: bool, code_repo_root: Path | None) -> Path:
    """Prompt for a directory path and validate it before accepting it.

    require_git=True (backup_dir/baseline_dir): rejects a path that resolves inside
    this same code repo (must be a SEPARATE, private repo per the README's Security
    section), offers to create the directory if missing, then rejects it unless it's
    already a git working tree -- commit_changes requires that, and discovering it
    only when `backup` fails deep in a subprocess call is a worse experience than
    catching it here, before a single device is even contacted.

    require_git=False (report_path): no git requirement, just a writable directory --
    report.write_report only ever mkdir's + writes JSON, no commit.
    """
    from . import gitstore

    while True:
        raw = input(f"{label}: ").strip()
        if not raw:
            console.print("[red]Can't be blank.[/red]")
            continue
        resolved = Path(raw).expanduser().resolve()

        if require_git and code_repo_root is not None:
            same_repo = resolved == code_repo_root or code_repo_root in resolved.parents
            if not same_repo and resolved.exists():
                same_repo = gitstore.git_repo_root(resolved) == code_repo_root
            if same_repo:
                console.print(
                    f"[red]{raw} is inside this same code repo[/red] — point it at a "
                    f"SEPARATE, private backup repo instead (see README's Security section)."
                )
                continue

        if not resolved.exists():
            make = input(f"{resolved} doesn't exist yet. Create it? [y/N] ").strip().lower()
            if make not in ("y", "yes"):
                continue
            resolved.mkdir(parents=True, exist_ok=True)

        if require_git and not gitstore.is_git_repo(resolved):
            console.print(
                f"[red]{resolved} isn't a git repository[/red] — `backup`/`promote` need it "
                f"to be one. Run `git init` there yourself (or point at an existing repo), "
                f"then try again."
            )
            continue

        return resolved


def _prompt_devices() -> list[dict[str, str]]:
    """Loop collecting devices (name/host/device_type) until a blank name ends it."""
    console.print("\nAdd devices (blank name when done):")
    devices: list[dict[str, str]] = []
    while True:
        name = input(f"  Device #{len(devices) + 1} name: ").strip()
        if not name:
            break
        host = input(f"    {name} host/IP: ").strip()
        if not host:
            console.print("[red]Host can't be blank — skipping this device.[/red]")
            continue
        device_type = input(f"    {name} device_type [cisco_ios]: ").strip() or "cisco_ios"
        devices.append({"name": name, "host": host, "device_type": device_type})
    return devices


def _run_config_wizard(config_path: Path) -> None:
    """Interactively build config.yaml: backup/baseline/report locations + device list.

    backup_dir/baseline_dir are validated before anything is written (see
    _prompt_directory) instead of only surfacing a problem once `backup` actually
    runs against real devices.
    """
    from . import gitstore

    console.print(
        Panel(
            "Let's set up config.yaml — where backups/baselines/reports live, and "
            "which devices to manage.",
            title="Configure",
            border_style="cyan",
        )
    )

    code_repo_root = gitstore.git_repo_root(Path.cwd())

    backup_dir = _prompt_directory(
        "Backup directory (current running-configs)",
        require_git=True, code_repo_root=code_repo_root,
    )
    baseline_dir = _prompt_directory(
        "Baseline directory (approved intended state)",
        require_git=True, code_repo_root=code_repo_root,
    )
    report_path = _prompt_directory(
        "Report directory (JSON run summaries)",
        require_git=False, code_repo_root=code_repo_root,
    )
    devices = _prompt_devices()
    if not devices:
        console.print(
            "[yellow]No devices added — config.yaml will have an empty device list.[/yellow]"
        )

    data = {
        "settings": {
            "backup_dir": str(backup_dir),
            "baseline_dir": str(baseline_dir),
            "report_path": str(report_path),
        },
        "devices": devices,
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "# Written by config-audit's configure wizard.\n" + yaml.safe_dump(data, sort_keys=False),
        encoding="utf-8",
    )
    console.print(f"[green]Wrote {config_path}[/green]\n")


def _ensure_config_file(config_path: Path) -> None:
    """Auto-launch the config wizard only when config.yaml is missing.

    Unlike secrets.env, an existing config.yaml is never auto-reprompted -- it holds
    a whole device inventory, not a single credential pair, so nagging on every
    command would be worse than useful. Reconfiguring is the explicit
    `config-audit configure` command instead (see main()).
    """
    if config_path.exists():
        return
    if not _interactive():
        console.print(
            f"[red]{config_path} not found[/red] and no interactive terminal attached. "
            f"Run `config-audit configure` interactively once to create it."
        )
        raise SystemExit(1)
    console.print(
        Panel(
            f"No config.yaml found at [bold]{config_path}[/bold]. Let's create one.",
            title="First-run setup",
            border_style="cyan",
        )
    )
    _run_config_wizard(config_path)


def _cmd_backup(cfg) -> int:
    from . import collector, gitstore

    results = collector.collect_all(cfg.devices)
    table = Table(title="Backup")
    table.add_column("Device")
    table.add_column("Status")
    for r in results:
        if r.ok:
            gitstore.write_config(cfg.settings.backup_dir, r.device, r.config_text)
            table.add_row(r.device, "[green]ok[/green]")
        else:
            table.add_row(r.device, f"[red]FAIL[/red]  {r.error}")
    console.print(table)

    committed = gitstore.commit_changes(cfg.settings.backup_dir)
    if committed:
        console.print("[green]committed new backups[/green]")
    else:
        console.print("[dim]no changes since last run[/dim]")
    return 0


def _cmd_diff(cfg) -> int:
    from . import drift

    any_drift = False
    table = Table(title="Drift Check")
    table.add_column("Device")
    table.add_column("Status")
    diffs: dict[str, list[str]] = {}
    no_baseline: list[str] = []

    for device in cfg.devices:
        current_path = cfg.settings.backup_dir / f"{device.name}.cfg"
        current = current_path.read_text(encoding="utf-8") if current_path.exists() else ""
        baseline_path = cfg.settings.baseline_dir / f"{device.name}.cfg"
        baseline_exists = baseline_path.exists()
        baseline = drift.load_baseline(cfg.settings.baseline_dir, device.name)

        result = drift.compare_to_baseline(device.name, current, baseline)
        if not baseline_exists:
            # No baseline yet != drift -- there's nothing to compare against, so
            # `compare_to_baseline` reporting the whole config as "changed" is
            # correct but misleading if shown the same way as real drift.
            no_baseline.append(device.name)
            table.add_row(device.name, "[cyan]NO BASELINE[/cyan]")
        elif result.has_drift:
            any_drift = True
            table.add_row(device.name, "[yellow]DRIFT[/yellow]")
            diffs[device.name] = result.diff_lines
        else:
            table.add_row(device.name, "[green]ok[/green]")

    console.print(table)
    for name, lines in diffs.items():
        console.print(Panel(_render_diff(lines), title=name, border_style="yellow"))
    if no_baseline:
        console.print(
            Panel(
                "No baseline exists yet for: " + ", ".join(no_baseline) + ". This isn't "
                "drift — there's nothing to compare against. Run `config-audit promote "
                "<device>` to establish the initial baseline from the current backup.",
                title="No baseline",
                border_style="cyan",
            )
        )
    return 1 if (any_drift or no_baseline) else 0


def _cmd_report(cfg) -> int:
    from . import collector, drift, report

    results = collector.collect_all(cfg.devices)
    drift_results = []
    no_baseline: list[str] = []
    for r in results:
        if r.ok:
            baseline_path = cfg.settings.baseline_dir / f"{r.device}.cfg"
            if not baseline_path.exists():
                no_baseline.append(r.device)
            baseline = drift.load_baseline(cfg.settings.baseline_dir, r.device)
            drift_results.append(drift.compare_to_baseline(r.device, r.config_text, baseline))

    run = report.build_report(results, drift_results)
    path = report.write_report(run, cfg.settings.report_path)

    summary = Table.grid(padding=(0, 2))
    summary.add_row("Devices:", str(run.devices_total))
    summary.add_row("[green]OK:[/green]", str(run.devices_ok))
    summary.add_row("[red]Failed:[/red]", str(run.devices_failed))
    summary.add_row("[yellow]Drifted:[/yellow]", str(len(run.drifted)))
    console.print(Panel(summary, title=f"Run report — {run.timestamp}"))

    # run.drifted (the JSON schema) doesn't distinguish "no baseline yet" from real
    # drift -- that's fine for the stable report file, but the console should, so
    # this isn't confused for an actual config change.
    real_drift = [d for d in run.drifted if d not in no_baseline]
    if real_drift:
        console.print("[yellow]Drifted:[/yellow] " + ", ".join(real_drift))
    if no_baseline:
        console.print(
            "[cyan]No baseline yet[/cyan] (not drift — nothing to compare against): "
            + ", ".join(no_baseline)
            + ". Run `config-audit promote <device>` to establish one."
        )
    for name, err in run.failures.items():
        console.print(f"[red]{name}[/red]: {err}")

    console.print(f"[dim]wrote {path}[/dim]")
    return 0


def _cmd_promote(cfg, device_name: str) -> int:
    """Human-gated: promote a device's current backup to its baseline (D6).

    Shows the delta, requires an explicit y/N, then (on yes) overwrites the
    device's baseline and git-commits it. Deterministic, no AI, no device write.
    """
    from . import promote, gitstore

    plan = promote.plan_promotion(
        device_name, cfg.settings.backup_dir, cfg.settings.baseline_dir
    )

    if not plan.backup_exists:
        backup_path = cfg.settings.backup_dir / f"{device_name}.cfg"
        console.print(f"[red]no backup found[/red] for {device_name} at {backup_path}")
        console.print("run `config-audit backup` first.")
        return 2

    if plan.is_initial:
        console.print(f"{device_name}: no baseline yet — this establishes the initial baseline.")
    elif not plan.has_drift:
        console.print(
            f"[green]{device_name}[/green]: already in sync with baseline — nothing to promote."
        )
        return 0
    else:
        console.print(f"{device_name}: drift vs current baseline —")

    console.print(_render_diff(plan.diff_lines))

    verb = "Establish initial baseline" if plan.is_initial else "Promote this into the baseline"
    # Plain input(), not a rich Prompt — keeps this trivially monkeypatchable in tests
    # and matches the "no --yes flag, no auto-approve path" rule: a human types y/N.
    resp = input(f"\n{verb} for {device_name}? [y/N] ").strip().lower()
    if resp not in ("y", "yes"):
        console.print("[dim]aborted — baseline unchanged.[/dim]")
        return 1

    path = promote.apply_promotion(device_name, plan.current_text, cfg.settings.baseline_dir)
    committed = gitstore.commit_changes(
        cfg.settings.baseline_dir, message=f"Promote baseline — {device_name}"
    )
    console.print(f"[green]baseline updated:[/green] {path}")
    console.print("committed" if committed else "written (git reported no change)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="config-audit",
        description="Pull, version-control, and drift-check Cisco running-configs.",
    )
    parser.add_argument(
        "-c", "--config", default="config/config.yaml",
        help="Path to config.yaml (default: config/config.yaml)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backup", help="Pull running-configs and commit to git.")
    sub.add_parser("diff", help="Drift check: backups vs. per-device baseline.")
    sub.add_parser("report", help="Emit a JSON summary of the latest run.")
    p_promote = sub.add_parser(
        "promote", help="Promote a device's current backup to its baseline (human-gated)."
    )
    p_promote.add_argument("device", help="Device name (must match a name in config.yaml).")
    sub.add_parser("configure", help="Interactively create or replace config.yaml.")

    args = parser.parse_args(argv)

    if args.command == "configure":
        config_path = Path(args.config)
        if config_path.exists():
            resp = input(f"{config_path} already exists. Overwrite? [y/N] ").strip().lower()
            if resp not in ("y", "yes"):
                return 1
        _run_config_wizard(config_path)
        return 0

    _ensure_config_file(Path(args.config))

    if args.command in ("backup", "report"):
        _ensure_secrets_file(Path("secrets.env"))

    cfg = load_config(args.config)

    if args.command == "promote":
        return _cmd_promote(cfg, args.device)

    dispatch = {"backup": _cmd_backup, "diff": _cmd_diff, "report": _cmd_report}
    return dispatch[args.command](cfg)


if __name__ == "__main__":
    sys.exit(main())
