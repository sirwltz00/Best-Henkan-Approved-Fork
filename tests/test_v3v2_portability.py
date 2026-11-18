"""Platform-independent parts of the V2 installer/source helpers."""
import sys

import pytest

from v3v2 import install, sources


def test_voice_id_helpers_are_available_without_registry():
    decoded = '00R00000000000'
    encoded = install.encode_id(decoded)
    assert len(encoded) == 16
    assert install.decode_id(encoded) == decoded


def test_installed_voice_discovery_is_empty_without_registry(monkeypatch):
    if sys.platform == 'win32':
        pytest.skip('This test specifically covers a non-Windows host')
    assert sources.installed() == []
    assert sources.installed_ids() == {}
    assert install.registered_ids() == set()


def test_registry_only_install_operation_fails_with_clear_error(monkeypatch):
    if sys.platform == 'win32':
        pytest.skip('This test specifically covers a non-Windows host')
    with pytest.raises(install.InstallError, match='not installed'):
        install.voice_dir()
