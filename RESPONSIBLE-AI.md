# RESPONSIBLE-AI.md — what the LLM is allowed to do here, and why

This repo exposes real network-automation functions to an LLM through the MCP
adapter (`src/config_audit_mcp/`). That makes "what can the model actually cause
to happen?" a design question, not an afterthought. This document records the
risk assessment and the controls — all of which live in deterministic code and
tests, none of which depend on the model behaving well.

**The governing principle: the model never decides whether an action is safe.**
Gates are code. The LLM can request; only code and humans approve.

## Scope of model authority

What an LLM connected to `config-audit-mcp` can and cannot do, as built:

| Capability | Allowed? | Enforced by |
| --- | --- | --- |
| Read inventory, drift, stored configs | yes (read-only) | 5 of 7 tools are file-based reads |
| See device credentials | **no** | `list_devices` excludes them — pinned by test |
| Write a baseline | only with explicit `confirm=True` | `promote_baseline` returns a plan and refuses otherwise — pinned by test |
| Pull live configs over SSH | yes (`backup_now`) | writes only to the backup repo — the same thing cron does unattended |
| **Write to a network device** | **no — not exposed at all** | the CLI's `push` is deliberately absent from the MCP registry |

The last row is the one that matters most. Config push exists in this repo as a
human-driven CLI command with its own diff-preview and gate; it is intentionally
not registered as an MCP tool. An LLM caller has no path — however it phrases
the request — to change device state.

## Risk assessment

**Prompt injection via tool results.** Device configs contain operator-authored
free text (interface descriptions, banners, remarks). When a tool returns a
config, that text enters the model's context — meaning anyone who can edit a
device config can put words in front of the model. Mitigations: the tool surface
gives injected text nothing dangerous to invoke (no device writes, and the one
mutating tool needs a deliberate `confirm=True` argument); worst-case blast
radius is a bad baseline promotion, which is a git commit — visible in history
and revertible.

**Wrong answers / automation bias.** The bias risk in this system isn't
demographic — it's an operator trusting a fluent model summary over the actual
diff. Mitigation: drift tools return the raw `unified_diff` alongside the
reshaped added/removed lists, so the human can always check the model's claim
against ground truth, and git history — not the model's narrative — remains the
authoritative timeline of what changed when.

**Credential and secret exposure.** Credentials come from the environment at
runtime, are never stored in configs or repos (SECURITY.md), and are stripped
from the one tool that describes devices. A model can't leak what it never sees.

**Unvalidated paths reaching gear.** Ordering rule: live-device tools stay
unregistered from the MCP server until the underlying path is validated on real
hardware. `backup_now` was excluded from the tool surface until Project 1's
hardware validation was done, then registered — in that order, on purpose.

## Quality and accuracy reviews

- The MCP layer has its own test suite proving the wrapping honest:
  credentials excluded, unknown devices flagged rather than guessed,
  `promote_baseline` refusing to write without `confirm=True`.
- Drift detection itself is covered by the core suite against sanitized
  fixtures; the MCP layer adds no logic to get wrong — `tools.py` delegates and
  reshapes, nothing more.
- CI runs lint + tests on every push; fixtures pass the sanitizer
  (`sanitize_check.py`) before they're committed.

## Adding a tool — the review it must pass

1. Classify it: read-only, mutating, or live. Mutating tools get an explicit
   confirm-style parameter (never a default of `True`); live tools stay
   unregistered until hardware-validated.
2. Write the refusal tests before registering it — the gate's behavior is
   pinned in the suite, not in a prompt.
3. Ask what an injected string in its output could cause the model to do next,
   given the rest of the tool surface. If the answer is "a device write," the
   design is wrong.

See also: [SECURITY.md](SECURITY.md), [`src/config_audit_mcp/README.md`](src/config_audit_mcp/README.md).
