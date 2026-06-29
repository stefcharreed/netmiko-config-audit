#!/usr/bin/env python3
"""
sanitize_check.py — scan a Cisco config for content unsafe to commit to a public repo.

A config is "safe to publish" only if it uses RFC 5737 documentation IPs, contains no
password hashes, and exposes no SNMP community strings. This is a guard, not the audit
tool: run it before a sample config earns a place in tests/fixtures/.
"""

import ipaddress
import re
import sys

# ---------------------------------------------------------------------------
# Constants — compiled once at import, reused on every line.
# ---------------------------------------------------------------------------

# RFC 5737 documentation ranges: the only IPv4 space that's safe to publish.
DOC_NETWORKS = [
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
]

# RFC 6890 "this network." Wildcard masks (0.0.0.255, 0.0.255.255, ...) parse as
# addresses in this block; Python 3.12's ipaddress flags them is_private. They are
# masks, not hosts, and nothing routable lives here — so skip the whole /8.
_THIS_NETWORK = ipaddress.ip_network("0.0.0.0/8")

# Anything shaped like an IPv4 address: four dot-separated groups of 1–3 digits.
IPV4_PATTERN = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")

# Credential / hash signatures. If any matches a line, the line carries a secret.
CREDENTIAL_PATTERNS = [
    re.compile(r"\bpassword\s+7\s+[0-9A-Fa-f]+"),  # type 7 (reversible)
    re.compile(r"\bsecret\s+[5-9]\s+\S+"),         # secret 5/8/9 + hash
    re.compile(r"\$[0-9]\$"),                       # inline hash blob: $1$ $5$ $8$ $9$
    re.compile(r"\benable\s+secret\s+\S+"),         # enable secret ...
]

# snmp-server community <string> — the community string is effectively a password.
SNMP_PATTERN = re.compile(r"\bsnmp-server\s+community\s+(\S+)")


# ---------------------------------------------------------------------------
# Per-line checks. Each takes one line, returns a list of findings (dicts).
# A finding has "category" and "detail"; the caller adds line_number + line.
# ---------------------------------------------------------------------------

def _check_real_ips(line: str) -> list[dict]:
    """Flag IPv4 addresses that aren't safe documentation IPs."""
    findings = []
    for candidate in IPV4_PATTERN.findall(line):
        try:
            ip = ipaddress.ip_address(candidate)
        except ValueError:
            continue  # looked like an IP (e.g. "999.1.1.1") but isn't valid

        # Skip addresses that are never sensitive to publish.
        if ip.is_unspecified or ip.is_loopback or ip.is_multicast or ip.is_reserved:
            continue
        if candidate.startswith("255."):       # subnet masks: 255.255.255.0, etc.
            continue
        if ip in _THIS_NETWORK:                 # 0.0.0.0/8: wildcard masks (0.0.0.255)
            continue                            # live here — "this network", never a real host
        if any(ip in net for net in DOC_NETWORKS):  # already a doc IP — safe
            continue

        if ip.is_private:
            findings.append({
                "category": "private_ip",
                "detail": f"{candidate} is RFC 1918 private space — real topology; "
                          f"use a 5737 doc range",
            })
        else:
            findings.append({
                "category": "real_ip",
                "detail": f"{candidate} is a real-looking public IP — not in an "
                          f"RFC 5737 doc range",
            })
    return findings


def _check_credentials(line: str) -> list[dict]:
    """Flag a line once if it carries any credential/hash."""
    for pattern in CREDENTIAL_PATTERNS:
        if pattern.search(line):
            return [{
                "category": "password_hash",
                "detail": "line contains a credential/hash — fixtures must have zero "
                          "real hashes (not even type 7)",
            }]
    return []


def _check_snmp(line: str) -> list[dict]:
    """Flag exposed SNMP community strings."""
    match = SNMP_PATTERN.search(line)
    if match:
        community = match.group(1)
        return [{
            "category": "snmp_community",
            "detail": f"SNMP community '{community}' is effectively a password — "
                      f"remove or fake it",
        }]
    return []


# ---------------------------------------------------------------------------
# The pure public function: text in, list of findings out. No printing, no I/O.
# ---------------------------------------------------------------------------

def check_config(config_text: str) -> list[dict]:
    """
    Scan a config for content unsafe to commit to a public repo.
    Returns a list of finding dicts (empty list == clean). Each finding:
        {"line_number": int, "line": str, "category": str, "detail": str}
    """
    checks = (_check_real_ips, _check_credentials, _check_snmp)
    findings = []
    for line_number, raw_line in enumerate(config_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        for check in checks:
            for finding in check(line):
                finding["line_number"] = line_number
                finding["line"] = line
                findings.append(finding)
    return findings


# ---------------------------------------------------------------------------
# CLI wrapper. Rendering lives HERE, never in check_config.
# ---------------------------------------------------------------------------

def _print_report(path: str, findings: list[dict]) -> None:
    if not findings:
        print(f"OK  {path}: clean — safe to commit")
        return
    print(f"FAIL  {path}: {len(findings)} issue(s) found\n")
    for f in findings:
        print(f"  line {f['line_number']:>4}  [{f['category']}]  {f['line']}")
        print(f"             -> {f['detail']}")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python sanitize_check.py <config-file>")
        return 2
    path = sys.argv[1]
    with open(path, "r", encoding="utf-8") as fh:
        config_text = fh.read()
    findings = check_config(config_text)
    _print_report(path, findings)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
