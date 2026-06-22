"""Command-line entry point.

Subcommands:
    backup   Pull running-configs from all devices and commit them to git.
    diff     Compare current backups against the golden baseline (drift check).
    report   Emit a structured (JSON) summary of the latest run.
"""
from __future__ import annotations

import argparse
import sys


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
    sub.add_parser("diff", help="Drift check: compare backups vs. golden baseline.")
    sub.add_parser("report", help="Emit a JSON summary of the latest run.")

    args = parser.parse_args(argv)

    # Wiring is intentionally left for you to complete as you build each module:
    #   from .inventory import load_config
    #   from . import collector, gitstore, drift, report
    #   cfg = load_config(args.config)
    #   ... dispatch on args.command ...
    if args.command == "backup":
        print("[backup] not implemented yet — build src/config_audit/collector.py")
    elif args.command == "diff":
        print("[diff] not implemented yet — build src/config_audit/drift.py")
    elif args.command == "report":
        print("[report] not implemented yet — build src/config_audit/report.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
