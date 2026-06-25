"""Command-line entry point.

    backup   Pull running-configs from all devices and commit them to git.
    diff     Compare current backups against the per-device baseline (drift check).
    report   Pull, drift-check, and emit a structured JSON summary of the run.
"""
from __future__ import annotations

import argparse
import sys

from .inventory import load_config


def _cmd_backup(cfg) -> int:
    from . import collector, gitstore

    results = collector.collect_all(cfg.devices)
    for r in results:
        if r.ok:
            gitstore.write_config(cfg.settings.backup_dir, r.device, r.config_text)
            print(f"ok     {r.device}")
        else:
            print(f"FAIL   {r.device}: {r.error}")

    committed = gitstore.commit_changes(cfg.settings.backup_dir)
    print("committed new backups" if committed else "no changes since last run")
    return 0


def _cmd_diff(cfg) -> int:
    from . import drift

    any_drift = False
    for device in cfg.devices:
        current_path = cfg.settings.backup_dir / f"{device.name}.cfg"
        current = current_path.read_text(encoding="utf-8") if current_path.exists() else ""
        baseline = drift.load_baseline(cfg.settings.baseline_dir, device.name)

        result = drift.compare_to_baseline(device.name, current, baseline)
        if result.has_drift:
            any_drift = True
            print(f"DRIFT  {device.name}")
            for line in result.diff_lines:
                print("   " + line)
        else:
            print(f"ok     {device.name}")
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
    print(f"wrote {path}")
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

    args = parser.parse_args(argv)
    cfg = load_config(args.config)

    dispatch = {"backup": _cmd_backup, "diff": _cmd_diff, "report": _cmd_report}
    return dispatch[args.command](cfg)


if __name__ == "__main__":
    sys.exit(main())
