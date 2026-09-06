"""Configuration parsing / defaults tests."""

from __future__ import annotations

import pytest

from app.config import (Config, ConfigError, Theme, Transport,
                        default_config, parse_server_address)


def test_default_config_sane():
    cfg = default_config()
    assert cfg.server_address.startswith("unix:")
    assert cfg.model
    assert cfg.book_root


def test_parse_http_address():
    spec = parse_server_address("http://127.0.0.1:8000")
    assert spec.transport is Transport.HTTP
    assert spec.host == "127.0.0.1:8000"


def test_parse_bare_host_port_defaults_to_http():
    spec = parse_server_address("localhost:1234")
    assert spec.transport is Transport.HTTP


def test_parse_unix_address():
    spec = parse_server_address("unix:/tmp/book_writer.sock")
    assert spec.transport is Transport.UNIX
    assert spec.socket_path == "/tmp/book_writer.sock"

    spec2 = parse_server_address("unix:///var/run/x.sock")
    assert spec2.transport is Transport.UNIX
    assert spec2.socket_path == "/var/run/x.sock"


@pytest.mark.parametrize("bad", ["", "   ", "ftp://x", "not an address at all!"])
def test_parse_rejects_bad_addresses(bad):
    with pytest.raises(ConfigError):
        parse_server_address(bad)


def test_config_update_server_address_revalidates():
    cfg = Config()
    cfg.update_server_address("http://10.0.0.1:9999")
    assert cfg.server_spec.transport is Transport.HTTP
    with pytest.raises(ConfigError):
        cfg.update_server_address("garbage value")


def test_config_theme_values():
    assert Theme.LIGHT.value == "light"
    assert Theme.DARK.value == "dark"
