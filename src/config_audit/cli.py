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

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .inventory import load_config

console = Console()


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
    """
    first_run = not secrets_path.exists()
    if not first_run:
        resp = input(f"{secrets_path} already exists. Re-enter credentials? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            return

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

    for device in cfg.devices:
        current_path = cfg.settings.backup_dir / f"{device.name}.cfg"
        current = current_path.read_text(encoding="utf-8") if current_path.exists() else ""
        baseline = drift.load_baseline(cfg.settings.baseline_dir, device.name)

        result = drift.compare_to_baseline(device.name, current, baseline)
        if result.has_drift:
            any_drift = True
            table.add_row(device.name, "[yellow]DRIFT[/yellow]")
            diffs[device.name] = result.diff_lines
        else:
            table.add_row(device.name, "[green]ok[/green]")

    console.print(table)
    for name, lines in diffs.items():
        console.print(Panel(_render_diff(lines), title=name, border_style="yellow"))
    return 1 if any_drift else 0


def _cmd_report(cfg) -> int:
    from . import collector, drift, report

    results = collector.collect_all(cfg.devices)
    drift_results = []
    for r in results:
        if r.ok:
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

    if run.drifted:
        console.print("[yellow]Drifted:[/yellow] " + ", ".join(run.drifted))
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

    args = parser.parse_args(argv)

    if args.command in ("backup", "report"):
        _ensure_secrets_file(Path("secrets.env"))

    cfg = load_config(args.config)

    if args.command == "promote":
        return _cmd_promote(cfg, args.device)

    dispatch = {"backup": _cmd_backup, "diff": _cmd_diff, "report": _cmd_report}
    return dispatch[args.command](cfg)


if __name__ == "__main__":
    sys.exit(main())
