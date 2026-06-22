"""Smoke test: the package imports and exposes a version."""
import config_audit


def test_version():
    assert config_audit.__version__
