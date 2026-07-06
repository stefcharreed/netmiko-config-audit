"""Command-line entry point.

    backup   Pull running-configs from all devices and commit them to git.
    diff     Compare current backups against the per-device baseline (drift check).
    report   Pull, drift-check, and emit a structured JSON summary of the run.
    promote      Bless a device's current backup as its new baseline (human-gated).
    push         Push a device's baseline TO the device (human-gated, two confirms).
    set-baseline Author a device's baseline from a file, no live pull needed (ZTP).

Rendering lives entirely in this module — every function it calls into
(collector, drift, report, promote, push, set_baseline, gitstore) returns plain
data and never prints. Swapping the renderer (plain print -> rich) never touches
that data, which is also what a later MCP/AI layer reads unchanged.
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

from .gitstore import GitIdentityError
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


def _render_push_commands(lines: list[str], removal_indices: set[int]) -> Text:
    """Render the exact commands `push` is about to send, in order.

    removal_indices marks lines push *synthesized* to reconcile a child the
    device has that the baseline doesn't (see push._build_config_lines) --
    not just any line that happens to start with `no`. The baseline itself
    legitimately has plenty of those (`no ip domain lookup`, `no shutdown`),
    and those must render as ordinary lines, not flagged removals.
    """
    text = Text()
    for i, line in enumerate(lines):
        is_removal = i in removal_indices
        style = "red" if is_removal else "green"
        prefix = "- " if is_removal else "+ "
        text.append(prefix + line.strip() + "\n", style=style)
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


def _prompt_subdirectory(repo_root: Path, default_name: str, label: str) -> Path:
    """Offer to create repo_root/default_name for `label` (the recommended default,
    matching this project's own real convention: snapshots/baselines/reports as
    siblings in one private repo). Lets the user pick a different subdirectory name
    under repo_root instead, if they already have one they want to reuse.

    No separate git-repo/same-repo validation here -- any subdirectory of an
    already-validated repo_root inherits its validity, so re-checking would just
    be redundant work for no benefit.
    """
    default_path = repo_root / default_name
    while True:
        resp = input(f"Create {default_path} for {label}? [Y/n] ").strip().lower()
        if resp in ("", "y", "yes"):
            default_path.mkdir(parents=True, exist_ok=True)
            return default_path
        if resp in ("n", "no"):
            while True:
                name = input(f"  Subdirectory name under {repo_root} to use instead: ").strip()
                if not name:
                    console.print("[red]Can't be blank.[/red]")
                    continue
                target = (repo_root / name).resolve()
                target.mkdir(parents=True, exist_ok=True)
                return target
        console.print("[red]Please answer y or n.[/red]")


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
    """Interactively build config.yaml: one private repo root, its recommended
    backup/baseline/report subdirectories, and the device list.

    Asking for the repo root once (instead of three independent paths) removes
    the main way this used to go wrong in practice: a typo in any of three
    separately-typed paths (e.g. `..config-backups` instead of `../config-backups`)
    silently resolving somewhere unintended. Validated before anything is written
    (see _prompt_directory) instead of only surfacing a problem once `backup`
    actually runs against real devices.
    """
    from . import gitstore

    console.print(
        Panel(
            "Let's set up config.yaml. First, the private repo where backups/"
            "baselines/reports live; then the recommended subdirectories under it; "
            "then your device list.",
            title="Configure",
            border_style="cyan",
        )
    )

    code_repo_root = gitstore.git_repo_root(Path.cwd())

    repo_root = _prompt_directory(
        "Private backup repo (git repo root)",
        require_git=True, code_repo_root=code_repo_root,
    )
    console.print(f"[dim]Using {repo_root} — recommended subdirectories below.[/dim]")

    backup_dir = _prompt_subdirectory(repo_root, "snapshots", "current running-configs")
    baseline_dir = _prompt_subdirectory(repo_root, "baselines", "approved intended state")
    report_path = _prompt_subdirectory(repo_root, "reports", "JSON run summaries")

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


def _cmd_backup(cfg, device_name: str | None = None) -> int:
    from . import collector, gitstore

    devices = cfg.devices
    if device_name is not None:
        device = _find_device(cfg, device_name)
        if device is None:
            console.print(f"[red]no device named[/red] {device_name} in config.yaml")
            return 2
        devices = [device]

    results = collector.collect_all(devices)
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
    if diffs:
        # Only a suggestion, never auto-run: push is a live device write and stays
        # human-initiated, same as promote -- diff just points at the next command.
        console.print(
            "[dim]To reconcile: "
            + ", ".join(f"`config-audit push {name}`" for name in diffs)
            + "[/dim]"
        )
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


def _cmd_set_baseline(cfg, device_name: str, source_path: str) -> int:
    """Human-gated: author a device's baseline from a config file, with no live
    device pull first -- the ZTP path. File-only, same risk class as promote/diff;
    never touches a device, so no secrets/live gear needed.
    """
    from . import gitstore, promote, set_baseline

    source = Path(source_path)
    if not source.exists():
        console.print(f"[red]no such file:[/red] {source}")
        return 2

    plan = set_baseline.plan_set_baseline(device_name, source, cfg.settings.baseline_dir)

    if plan.is_initial:
        console.print(f"{device_name}: no baseline yet — this establishes it from {source}.")
    elif not plan.has_drift:
        console.print(
            f"[green]{device_name}[/green]: baseline already matches {source} — nothing to do."
        )
        return 0
    else:
        console.print(f"{device_name}: {source} differs from the current baseline —")

    console.print(_render_diff(plan.diff_lines))

    verb = "Establish baseline" if plan.is_initial else "Overwrite the baseline"
    resp = input(f"\n{verb} for {device_name} from {source}? [y/N] ").strip().lower()
    if resp not in ("y", "yes"):
        console.print("[dim]aborted — baseline unchanged.[/dim]")
        return 1

    path = promote.apply_promotion(device_name, plan.source_text, cfg.settings.baseline_dir)
    committed = gitstore.commit_changes(
        cfg.settings.baseline_dir, message=f"Set baseline (authored) — {device_name}"
    )
    console.print(f"[green]baseline updated:[/green] {path}")
    console.print("committed" if committed else "written (git reported no change)")
    return 0


def _find_device(cfg, device_name: str):
    for device in cfg.devices:
        if device.name == device_name:
            return device
    return None


def _cmd_push(cfg, device_name: str) -> int:
    """Human-gated: push a device's baseline onto the device itself.

    Two separate confirms, deliberately -- push (reversible with a reload) and
    save (not reversible) are different amounts of risk and get different gates.
    No `--yes`/auto-approve for either, matching promote's rule (D6).
    """
    from . import collector, drift, push

    device = _find_device(cfg, device_name)
    if device is None:
        console.print(f"[red]no device named[/red] {device_name} in config.yaml")
        return 2

    live = collector.fetch_running_config(device)
    if not live.ok:
        console.print(f"[red]couldn't reach {device_name}:[/red] {live.error}")
        return 2

    plan = push.plan_push(device_name, cfg.settings.baseline_dir, live.config_text)

    if not plan.baseline_exists:
        console.print(
            f"[cyan]no baseline yet[/cyan] for {device_name} — nothing to push. "
            f"Run `config-audit promote {device_name}` first to establish one."
        )
        return 2

    if plan.no_changes:
        console.print(f"[green]{device_name}[/green]: already matches baseline — nothing to push.")
        return 0

    console.print(f"{device_name}: live config differs from baseline —")
    console.print(_render_diff(plan.diff_lines))

    if plan.removal_indices:
        console.print(
            f"\n[yellow]{len(plan.removal_indices)} line(s) will be explicitly "
            f"removed[/yellow] from {device_name} (device has these, baseline doesn't):"
        )
    console.print("\nExact commands to be sent:")
    console.print(_render_push_commands(plan.config_lines, plan.removal_indices))

    resp = input(f"Push baseline to {device_name}? [y/N] ").strip().lower()
    if resp not in ("y", "yes"):
        console.print("[dim]aborted — device unchanged.[/dim]")
        return 1

    post_push_text = push.apply_push(device, plan.config_lines)
    baseline_text = (Path(cfg.settings.baseline_dir) / f"{device_name}.cfg").read_text(
        encoding="utf-8"
    )
    post_result = drift.compare_to_baseline(device_name, post_push_text, baseline_text)
    if post_result.has_drift:
        console.print(
            "[yellow]pushed, but the device still doesn't fully match the baseline:[/yellow]"
        )
        console.print(_render_diff(post_result.diff_lines))
    else:
        console.print("[green]pushed — device now matches baseline.[/green]")

    save_resp = input(
        f"\nSave this config on {device_name} so it survives a reload? [y/N] "
    ).strip().lower()
    if save_resp in ("y", "yes"):
        push.save_running_config(device)
        console.print("[green]saved.[/green]")
    else:
        console.print("[yellow]NOT saved[/yellow] — a reload will revert this push.")

    return 1 if post_result.has_drift else 0


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
    p_backup = sub.add_parser("backup", help="Pull running-configs and commit to git.")
    p_backup.add_argument(
        "device", nargs="?", default=None,
        help="Only back up this device (default: all devices in config.yaml).",
    )
    sub.add_parser("diff", help="Drift check: backups vs. per-device baseline.")
    sub.add_parser("report", help="Emit a JSON summary of the latest run.")
    p_promote = sub.add_parser(
        "promote", help="Promote a device's current backup to its baseline (human-gated)."
    )
    p_promote.add_argument("device", help="Device name (must match a name in config.yaml).")
    p_push = sub.add_parser(
        "push", help="Push a device's baseline to the device (human-gated, two confirms)."
    )
    p_push.add_argument("device", help="Device name (must match a name in config.yaml).")
    p_set_baseline = sub.add_parser(
        "set-baseline",
        help="Author a device's baseline from a config file, no live pull needed (ZTP).",
    )
    p_set_baseline.add_argument("device", help="Device name (must match a name in config.yaml).")
    p_set_baseline.add_argument("file", help="Path to the config file/template to use.")
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

    if args.command in ("backup", "report", "push"):
        _ensure_secrets_file(Path("secrets.env"))

    cfg = load_config(args.config)

    try:
        if args.command == "promote":
            return _cmd_promote(cfg, args.device)
        if args.command == "push":
            return _cmd_push(cfg, args.device)
        if args.command == "set-baseline":
            return _cmd_set_baseline(cfg, args.device, args.file)
        if args.command == "backup":
            return _cmd_backup(cfg, args.device)

        dispatch = {"diff": _cmd_diff, "report": _cmd_report}
        return dispatch[args.command](cfg)
    except GitIdentityError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2


if __name__ == "__main__":
    sys.exit(main())
