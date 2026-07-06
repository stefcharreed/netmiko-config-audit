"""push: plan_push is pure analysis (no SSH), apply_push/save_running_config are
the only functions that write to a device. These tests cover plan_push's states
using the same fixtures as test_drift/test_promote -- no gear and no stdin, since
the human gates and rendering live in the CLI.
"""
from pathlib import Path

from config_audit.push import _build_config_lines, plan_push

FIXTURES = Path(__file__).parent / "fixtures"


def _fx(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _baseline_dir(tmp_path: Path) -> Path:
    baseline = tmp_path / "baselines"
    baseline.mkdir()
    return baseline


def test_no_baseline_means_nothing_to_push(tmp_path):
    """With no baseline for the device, the plan reports nothing to push."""
    baseline = _baseline_dir(tmp_path)
    plan = plan_push("ISR1", baseline, live_config="hostname ISR1\n")
    assert plan.baseline_exists is False
    assert plan.no_changes is True
    assert plan.config_lines == []


def test_live_matching_baseline_is_no_changes(tmp_path):
    """When the live config already matches the baseline, nothing would be sent."""
    baseline = _baseline_dir(tmp_path)
    (baseline / "ISR1.cfg").write_text(_fx("ISR1_baseline.cfg"), encoding="utf-8")
    plan = plan_push("ISR1", baseline, live_config=_fx("ISR1_current_clean.cfg"))
    assert plan.baseline_exists is True
    assert plan.no_changes is True
    assert plan.config_lines == []


def test_drifted_live_produces_config_lines_and_diff(tmp_path):
    """A live config that has drifted from the baseline produces the full baseline
    as config_lines to send, plus a non-empty diff for human review."""
    baseline = _baseline_dir(tmp_path)
    (baseline / "ISR1.cfg").write_text(_fx("ISR1_baseline.cfg"), encoding="utf-8")
    plan = plan_push("ISR1", baseline, live_config=_fx("ISR1_current_drift.cfg"))
    assert plan.baseline_exists is True
    assert plan.no_changes is False
    assert plan.diff_lines
    assert plan.config_lines  # non-empty: this is what would be sent
    # no blank lines -- send_config_set doesn't need them and they add noise
    assert all(line.strip() for line in plan.config_lines)


def test_changed_child_line_gets_explicit_no_before_the_new_value(tmp_path):
    """ISR1_current_drift.cfg has `description LAN-SEGMENT-A-PRINTERS` on Gi0/0/1
    where the baseline has `description LAN-SEGMENT-A` -- same parent on both
    sides, differing child. The stale value must be explicitly removed (`no
    description LAN-SEGMENT-A-PRINTERS`) before the baseline's own `description
    LAN-SEGMENT-A` is sent, not just left for the device to reconcile itself."""
    baseline = _baseline_dir(tmp_path)
    (baseline / "ISR1.cfg").write_text(_fx("ISR1_baseline.cfg"), encoding="utf-8")
    plan = plan_push("ISR1", baseline, live_config=_fx("ISR1_current_drift.cfg"))

    lines = plan.config_lines
    parent_idx = lines.index("interface GigabitEthernet0/0/1")
    no_idx = lines.index("no description LAN-SEGMENT-A-PRINTERS")
    new_idx = lines.index(" description LAN-SEGMENT-A")
    assert parent_idx < no_idx < new_idx


def test_extra_acl_line_on_device_gets_explicit_no(tmp_path):
    """The ACL MGMT-IN on the live device has an extra permit line the baseline
    doesn't -- ACLs are additive by nature (a bare resend doesn't remove a
    stale entry), so this is the case the feature exists for."""
    baseline = _baseline_dir(tmp_path)
    (baseline / "ISR1.cfg").write_text(_fx("ISR1_baseline.cfg"), encoding="utf-8")
    plan = plan_push("ISR1", baseline, live_config=_fx("ISR1_current_drift.cfg"))

    lines = plan.config_lines
    parent_idx = lines.index("ip access-list extended MGMT-IN")
    no_idx = lines.index("no permit tcp host 198.51.100.51 any eq 443")
    assert parent_idx < no_idx


def test_preexisting_baseline_no_lines_are_never_flagged_as_removals():
    """A baseline can legitimately contain its own `no ...` child lines (`no
    shutdown` to bring an interface up, `no ip proxy-arp` to disable a
    feature). Those must never end up in removal_indices -- only lines push
    itself synthesized to reconcile a device-only child count as removals."""
    baseline = (
        "interface GigabitEthernet0/0/1\n"
        " description LAN-SEGMENT-A\n"
        " no shutdown\n"
    )
    live = baseline  # identical -- nothing for push to reconcile

    lines, removal_indices = _build_config_lines(baseline, live)
    assert removal_indices == set()
    assert " no shutdown" in lines


def test_removing_a_device_only_no_line_inverts_correctly_not_double_negated():
    """The device has an explicit `no ip proxy-arp` under an interface the
    baseline shares but doesn't mention that line for. Reconciling it means
    turning the feature back on -- `ip proxy-arp`, not `no no ip proxy-arp`.
    The baseline's own unrelated `no shutdown` child must be left alone and
    not confused with the synthesized removal."""
    baseline = (
        "interface GigabitEthernet0/0/1\n"
        " description LAN-SEGMENT-A\n"
        " no shutdown\n"
    )
    live = (
        "interface GigabitEthernet0/0/1\n"
        " description LAN-SEGMENT-A\n"
        " no shutdown\n"
        " no ip proxy-arp\n"
    )

    lines, removal_indices = _build_config_lines(baseline, live)
    assert len(removal_indices) == 1
    removal_idx = next(iter(removal_indices))
    assert lines[removal_idx] == "ip proxy-arp"          # not "no no ip proxy-arp"
    assert " no shutdown" in lines                        # baseline's own line, untouched
    # the baseline's own " no shutdown" line is never the one flagged
    assert lines.index(" no shutdown") not in removal_indices


def test_device_missing_a_baseline_no_line_reconciles_via_its_own_inverse():
    """The device is admin-down (`shutdown` in its live config) where the
    baseline expects it up (`no shutdown`). Reconciling that extra `shutdown`
    child means sending `no shutdown` to turn it back on -- and that
    synthesized line must be distinguishable from the baseline's own (later,
    redundant-but-harmless) `no shutdown` line by origin, not by text, since
    both end up being the literal string `no shutdown` / ` no shutdown`."""
    baseline = (
        "interface GigabitEthernet0/0/1\n"
        " description LAN-SEGMENT-A\n"
        " no shutdown\n"
    )
    live = (
        "interface GigabitEthernet0/0/1\n"
        " description LAN-SEGMENT-A\n"
        " shutdown\n"
    )

    lines, removal_indices = _build_config_lines(baseline, live)
    assert len(removal_indices) == 1
    removal_idx = next(iter(removal_indices))
    assert lines[removal_idx] == "no shutdown"            # synthesized: turn it back on
    # the baseline's own " no shutdown" (with leading space) is a separate,
    # unflagged line further down -- not the same list entry as the removal
    baseline_no_shutdown_idx = lines.index(" no shutdown")
    assert baseline_no_shutdown_idx not in removal_indices
    assert baseline_no_shutdown_idx != removal_idx


def test_whole_extra_block_on_device_is_not_auto_removed(tmp_path):
    """A whole parent block the device has that the baseline never mentions at
    all (a stray extra interface, here) stays a human decision -- deleting a
    whole block is a different risk class than fixing up one child line, so
    it must never show up as an auto-generated removal."""
    baseline_text = _fx("ISR1_baseline.cfg")
    baseline = _baseline_dir(tmp_path)
    (baseline / "ISR1.cfg").write_text(baseline_text, encoding="utf-8")

    live_config = baseline_text.replace(
        "end\n",
        "interface GigabitEthernet0/0/2\n"
        " description ROGUE-UNMANAGED-PORT\n"
        " shutdown\n"
        "end\n",
    )
    plan = plan_push("ISR1", baseline, live_config=live_config)

    assert "interface GigabitEthernet0/0/2" not in plan.config_lines
    assert not any("ROGUE-UNMANAGED-PORT" in line for line in plan.config_lines)
    assert not any(line.startswith("no interface") for line in plan.config_lines)
