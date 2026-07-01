"""The tool surface as a contract. These pin what the server exposes and how each
tool is classified (read-only / mutating / needs-gear), so the surface can't silently
change and the safety classification can't drift. Verifiable with no SDK installed.
"""
from config_audit_mcp import registry


def test_exposes_exactly_the_expected_tools():
    """The registry exposes the agreed capability set — no more, no fewer."""
    names = {s.name for s in registry.REGISTRY}
    assert names == {
        "list_devices", "get_drift", "get_drift_all", "plan_promotion",
        "get_config", "promote_baseline", "backup_now",
    }


def test_tool_names_are_unique():
    """No two tools share a name (a duplicate would shadow at registration)."""
    names = [s.name for s in registry.REGISTRY]
    assert len(names) == len(set(names))


def test_every_tool_has_a_nonempty_description():
    """An LLM picks tools by description, so every tool must carry one."""
    for s in registry.REGISTRY:
        assert s.description and s.description.strip(), f"{s.name} has no description"


def test_only_promote_baseline_and_backup_now_are_mutating():
    """Exactly two tools write state; everything else must be read-only."""
    mutating = {s.name for s in registry.REGISTRY if s.mutating}
    assert mutating == {"promote_baseline", "backup_now"}


def test_only_backup_now_needs_gear():
    """backup_now (live SSH) is the one tool needing real hardware -- registered
    now that Project 1's hardware validation is done. Everything else is
    file-based and needs no device."""
    needs_gear = {s.name for s in registry.REGISTRY if s.needs_gear}
    assert needs_gear == {"backup_now"}


def test_all_handlers_are_callable():
    """Every registry entry points at a real callable handler."""
    for s in registry.REGISTRY:
        assert callable(s.handler), f"{s.name} handler is not callable"
