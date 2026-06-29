"""sanitize_check is the publish-safety gate every fixture must pass before it can
live in the repo. These tests confirm it passes clean documentation-IP configs,
catches the things that must never be published, and (D13) treats wildcard masks
as masks rather than private hosts.
"""
from pathlib import Path

from config_audit.sanitize_check import check_config

FIXTURES = Path(__file__).parent / "fixtures"


def _fx(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _cats(findings):
    return {f["category"] for f in findings}


def test_all_fixtures_are_clean():
    """Every committed fixture must be publish-safe, or it has no business in the repo."""
    for cfg in sorted(FIXTURES.glob("*.cfg")):
        assert check_config(cfg.read_text(encoding="utf-8")) == [], f"{cfg.name} not clean"


def test_flags_type7_password():
    """A type-7 (reversible) password is flagged as a credential."""
    # synthetic placeholder hex string, not a real device secret
    assert "password_hash" in _cats(check_config("username x password 7 0123456789ABCDEF"))


def test_flags_enable_secret():
    """An enable secret line is flagged as a credential."""
    assert "password_hash" in _cats(check_config("enable secret 9 PLACEHOLDER"))


def test_flags_inline_hash_blob():
    """An inline $9$-style hash blob is flagged as a credential."""
    assert "password_hash" in _cats(check_config("username x secret 9 $9$PLACEHOLDERHASH"))


def test_flags_snmp_community():
    """An SNMP community string is flagged — it is effectively a password."""
    assert "snmp_community" in _cats(check_config("snmp-server community PUBLICSTRING ro"))


def test_flags_real_public_ip():
    """A routable public IP outside the doc ranges is flagged."""
    assert "real_ip" in _cats(check_config("ip name-server 8.8.8.8"))


def test_flags_rfc1918_private_ip():
    """An RFC 1918 private address is flagged as real topology."""
    assert "private_ip" in _cats(check_config("ip address 10.1.1.1 255.0.0.0"))


def test_doc_ip_subnet_mask_and_default_route_are_clean():
    """Doc-range IPs, subnet masks, and a 0.0.0.0 default route are all publish-safe."""
    clean = "ip address 192.0.2.1 255.255.255.0\nip route 0.0.0.0 0.0.0.0 192.0.2.254"
    assert check_config(clean) == []


def test_wildcard_mask_in_ospf_is_not_flagged():
    """D13: an OSPF network statement with a 0.0.0.255 wildcard comes back clean."""
    # Wildcards parse as 0.0.0.0/8 addresses, which Python 3.12 flags is_private;
    # they are masks, not hosts, so the sanitizer skips the whole /8.
    findings = check_config("network 192.0.2.0 0.0.0.255 area 0")
    assert findings == []


def test_wildcard_mask_in_acl_is_not_flagged():
    """D13: an ACL entry with a wildcard mask is also clean."""
    assert check_config("access-list 10 permit 192.0.2.0 0.0.0.255") == []


def test_real_private_host_still_flagged():
    """Guard: the 0.0.0.0/8 wildcard skip must NOT swallow genuine RFC 1918 hosts."""
    assert "private_ip" in _cats(check_config("ip address 10.1.1.1 255.255.255.0"))
