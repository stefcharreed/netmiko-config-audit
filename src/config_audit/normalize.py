"""Normalize a running-config so a diff fires on real change, not noise.

CRITICAL: normalize() is pure and MUST be applied IDENTICALLY to BOTH sides
(baseline and current) before difflib sees them. Normalizing only one side
manufactures phantom drift — the single most common false-positive source.

What it strips (v1, line-based):
    - the `show run` header block (Building configuration / Current configuration
      / ! Last configuration change / ! NVRAM config last updated)
    - `ntp clock-period N`  (IOS auto-tunes this constantly)
    - blank lines, bare `!` separators, trailing whitespace, CRLF
What it deliberately KEEPS:
    - password hashes (a changed credential IS real drift worth catching)
    - line ordering (an ACL reorder is meaningful — never sort)

Multi-line crypto/PKI certificate blobs are NOT stripped yet; add that only once
you actually observe cert noise on real gear (validate against physical IOS-XE first).
"""
from __future__ import annotations

import re

_VOLATILE_PREFIXES = (
    "Building configuration",
    "Current configuration",
    "! Last configuration change",
    "! NVRAM config last updated",
)
_NTP_CLOCK = re.compile(r"^ntp clock-period \d+$")


def normalize(config_text: str) -> list[str]:
    """Return the config as a list of meaningful, comparable lines."""
    lines = config_text.replace("\r\n", "\n").split("\n")

    out: list[str] = []
    for raw in lines:
        line = raw.rstrip()                 # drop trailing whitespace / CR
        if not line:
            continue                        # drop blank lines
        if line == "!":
            continue                        # drop bare section separators
        if line.strip() == "end":
            continue
        if line.startswith(_VOLATILE_PREFIXES):
            continue
        if _NTP_CLOCK.match(line):
            continue
        out.append(line)
    return out
