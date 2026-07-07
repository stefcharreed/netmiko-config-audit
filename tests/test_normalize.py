"""normalize() is load-bearing: it must erase volatile noise IDENTICALLY on both
sides of a diff while preserving anything that represents real drift. Get this
wrong and every run reports phantom drift.
"""
from pathlib import Path

from config_audit.normalize import normalize

FIXTURES = Path(__file__).parent / "fixtures"


def _fx(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_clean_pair_normalizes_identically_isr():
    """ISR baseline and a noise-only-different current normalize to the same lines."""
    assert normalize(_fx("ISR1_baseline.cfg")) == normalize(_fx("ISR1_current_clean.cfg"))


def test_clean_pair_normalizes_identically_cat():
    """Same phantom-drift guard for the Catalyst fixture pair."""
    assert normalize(_fx("CAT1_baseline.cfg")) == normalize(_fx("CAT1_current_clean.cfg"))


def test_strips_known_volatile_lines():
    """Header block, ntp clock-period, blanks, bare '!' and trailing 'end' are removed."""
    out = normalize(_fx("ISR1_baseline.cfg"))
    joined = "\n".join(out)
    assert "Building configuration" not in joined
    assert "Current configuration" not in joined
    assert not any(ln.startswith("! Last configuration change") for ln in out)
    assert not any(ln.startswith("ntp clock-period") for ln in out)
    assert "!" not in out          # bare separators gone
    assert "" not in out           # blank lines gone
    assert "end" not in out        # trailing 'end' gone


def test_keeps_password_hash_and_order():
    """A changed credential is real drift, so hash lines stay and order is preserved."""
    # Synthetic placeholder hash — never a real secret, lives only in this test.
    cfg = (
        "hostname X\n"
        "!\n"
        "enable secret 9 PLACEHOLDER_NOT_A_REAL_HASH\n"
        "username admin privilege 15\n"
        "interface Loopback0\n"
        " ip address 192.0.2.1 255.255.255.255\n"
        "end\n"
    )
    out = normalize(cfg)
    assert "enable secret 9 PLACEHOLDER_NOT_A_REAL_HASH" in out
    assert out.index("hostname X") < out.index("enable secret 9 PLACEHOLDER_NOT_A_REAL_HASH")
    secret_pos = out.index("enable secret 9 PLACEHOLDER_NOT_A_REAL_HASH")
    assert secret_pos < out.index("interface Loopback0")


def test_does_not_sort_lines():
    """Line order is meaningful (an ACL reorder is drift), so normalize never sorts."""
    cfg = "zzz line\naaa line\nmmm line\n"
    assert normalize(cfg) == ["zzz line", "aaa line", "mmm line"]
