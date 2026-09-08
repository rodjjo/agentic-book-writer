"""Configuration parsing / defaults tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import (Config, ConfigError, Theme, Transport,
                        CONFIG_DIR_NAME, CONFIG_FILE_NAME, default_config,
                        default_config_path, load_config, parse_server_address,
                        save_config)


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


def test_config_system_prompt():
    cfg = Config()
    assert cfg.system_prompt == ""
    cfg_with_prompt = Config(system_prompt="You are a poet.")
    assert cfg_with_prompt.system_prompt == "You are a poet."
    overridden = cfg.with_overrides(system_prompt="New prompt")
    assert overridden.system_prompt == "New prompt"


def test_cli_system_prompt():
    from app.__main__ import build_config, build_parser

    parser = build_parser()
    args = parser.parse_args(["--system-prompt", "You are an author."])
    cfg = build_config(args)
    assert cfg.system_prompt == "You are an author."


# ---------------------------------------------------------------------------
# Settings persistence (~/.agentic-book-writer/config.json)
# ---------------------------------------------------------------------------

def test_default_config_path_is_under_dot_dir_in_home():
    assert default_config_path() == Path.home() / CONFIG_DIR_NAME / CONFIG_FILE_NAME
    assert default_config_path().parent.name == CONFIG_DIR_NAME
    assert default_config_path().name == CONFIG_FILE_NAME


def test_config_roundtrip_via_dict(tmp_path):
    cfg = Config(server_address="http://10.0.0.1:8080", model="gpt-test",
                 book_root=str(tmp_path / "books"), theme=Theme.DARK,
                 connection_timeout=12.5, max_chat_turns=4, stream=False,
                 system_prompt="Be concise.")
    data = cfg.to_dict()
    assert data["theme"] == "dark"
    assert data["connection_timeout"] == 12.5
    assert data["stream"] is False

    restored = Config.from_dict(data)
    assert restored == cfg
    assert restored.theme is Theme.DARK


def test_config_from_dict_unknown_keys_ignored_and_defaults_filled():
    cfg = Config.from_dict({"version": 1, "server_address": "unix:/tmp/x.sock",
                            "theme": "dark", "some_future_key": 123})
    assert cfg.server_address == "unix:/tmp/x.sock"
    assert cfg.theme is Theme.DARK
    # keys that were not provided fall back to the dataclass defaults
    assert cfg.connection_timeout == 30.0
    assert cfg.max_chat_turns == 8
    assert cfg.stream is True


@pytest.mark.parametrize("bad_theme", ["neon", "darkk", 3, None])
def test_config_from_dict_rejects_bad_theme(bad_theme):
    with pytest.raises(ConfigError):
        Config.from_dict({"theme": bad_theme})


def test_config_from_dict_rejects_unusable_timeout():
    with pytest.raises(ConfigError):
        Config.from_dict({"connection_timeout": "soon"})


def test_save_and_load_config_roundtrip(tmp_path):
    target = tmp_path / "config.json"
    cfg = Config(server_address="unix:/tmp/book_writer.sock", model="agent",
                 book_root=str(tmp_path / "books"), theme=Theme.DARK,
                 connection_timeout=60.0, system_prompt="Write slowly.")

    written = save_config(cfg, target)
    assert written == target
    assert target.exists()

    raw = json.loads(target.read_text())
    assert raw["version"] == 1
    assert raw["theme"] == "dark"

    loaded = load_config(target)
    assert loaded is not None
    assert loaded == cfg


def test_load_config_missing_file_returns_none(tmp_path):
    assert load_config(tmp_path / "nope.json") is None


def test_load_config_corrupt_json_raises(tmp_path):
    target = tmp_path / "config.json"
    target.write_text("{ not json")
    with pytest.raises(ConfigError):
        load_config(target)


def test_load_config_non_object_raises(tmp_path):
    target = tmp_path / "config.json"
    target.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(ConfigError):
        load_config(target)


# ---------------------------------------------------------------------------
# Startup: persisted settings layered under explicit CLI flags
# ---------------------------------------------------------------------------

def test_startup_config_loads_saved_settings(tmp_path):
    from app.__main__ import build_parser, startup_config

    settings = tmp_path / "config.json"
    save_config(Config(server_address="unix:/tmp/saved.sock", model="saved-model",
                       theme=Theme.DARK, system_prompt="Saved prompt"), settings)

    args = build_parser().parse_args([])
    cfg = startup_config(args, config_path=settings)
    assert cfg.server_address == "unix:/tmp/saved.sock"
    assert cfg.model == "saved-model"
    assert cfg.theme is Theme.DARK
    assert cfg.system_prompt == "Saved prompt"


def test_startup_config_cli_overrides_saved(tmp_path):
    from app.__main__ import build_parser, startup_config

    settings = tmp_path / "config.json"
    save_config(Config(server_address="unix:/tmp/saved.sock", model="saved-model",
                       theme=Theme.LIGHT, system_prompt="Saved prompt"), settings)

    args = build_parser().parse_args(
        ["--server", "http://127.0.0.1:9000", "--theme", "dark"])
    cfg = startup_config(args, config_path=settings)
    # CLI flags win for the fields they name...
    assert cfg.server_address == "http://127.0.0.1:9000"
    assert cfg.theme is Theme.DARK
    # ...and untouched fields keep their persisted values.
    assert cfg.model == "saved-model"
    assert cfg.system_prompt == "Saved prompt"


def test_startup_config_no_file_uses_defaults(tmp_path):
    from app.__main__ import build_parser, startup_config

    args = build_parser().parse_args(["--server", "unix:/tmp/cli.sock"])
    cfg = startup_config(args, config_path=tmp_path / "missing.json")
    assert cfg.server_address == "unix:/tmp/cli.sock"
    assert cfg.model  # default model
    assert cfg.book_root  # default book root


def test_startup_config_corrupt_file_warns_and_falls_back(tmp_path, capsys):
    from app.__main__ import build_parser, startup_config

    settings = tmp_path / "config.json"
    settings.write_text("{ corrupt")
    args = build_parser().parse_args(["--server", "unix:/tmp/cli.sock"])
    cfg = startup_config(args, config_path=settings)
    # app still starts with defaults + CLI flags; the problem is reported
    assert cfg.server_address == "unix:/tmp/cli.sock"
    err = capsys.readouterr().err
    assert "warning" in err
    assert str(settings) in err

