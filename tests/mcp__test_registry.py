"""The tool surface as a contract. These pin what the server exposes and how each
tool is classified (read-only / mutating / needs-gear), so the surface can't silently
change and the safety classification can't drift. Verifiable with no SDK installed.
"""
from config_audit_mcp import registry


def test_exposes_exactly_the_expected_tools():
    """The registry exposes the agreed capability set — no more, no fewer."""
    names = {s.name for s in registry.REGISTRY}
    assert names == {
        "list_devices", "get_drift", "get_drift_all",
        "plan_promotion", "get_config", "promote_baseline",
    }


def test_tool_names_are_unique():
    """No two tools share a name (a duplicate would shadow at registration)."""
    names = [s.name for s in registry.REGISTRY]
    assert len(names) == len(set(names))


def test_every_tool_has_a_nonempty_description():
    """An LLM picks tools by description, so every tool must carry one."""
    for s in registry.REGISTRY:
        assert s.description and s.description.strip(), f"{s.name} has no description"


def test_only_promote_baseline_is_mutating():
    """Exactly one tool writes state; everything else must be read-only."""
    mutating = {s.name for s in registry.REGISTRY if s.mutating}
    assert mutating == {"promote_baseline"}


def test_no_tool_needs_gear_yet():
    """backup_now (live SSH) is deferred until P1 hardware validation, so nothing
    in the current surface should require gear."""
    assert not any(s.needs_gear for s in registry.REGISTRY)


def test_all_handlers_are_callable():
    """Every registry entry points at a real callable handler."""
    for s in registry.REGISTRY:
        assert callable(s.handler), f"{s.name} handler is not callable"
