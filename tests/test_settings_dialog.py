"""Tests for SettingsDialog: system prompt, per-book custom instructions, and apply_settings."""

from __future__ import annotations

from app.books_store import BookStore
from app.config import Config, load_config
from app.gui.application import BookWriterApp
from app.gui.dialogs import BookInstructionsDialog, SettingsDialog
from app.theme import THEMES


def test_settings_dialog_global_prompt():
    theme = THEMES["light"]
    initial = {
        "server_address": "http://127.0.0.1:8000",
        "book_root": "/tmp/books",
        "model": "test-model",
        "connection_timeout": "30",
        "theme": "light",
        "system_prompt": "Global prompt text",
    }

    result = None

    def on_done(res):
        nonlocal result
        result = res

    dlg = SettingsDialog(theme, initial, on_done=on_done)
    assert dlg._system_prompt_input.text == "Global prompt text"

    # Modify system prompt and confirm
    dlg._system_prompt_input.text = "Updated global prompt"
    dlg._confirm()

    assert result is not None
    assert result["system_prompt"] == "Updated global prompt"


def test_book_instructions_dialog():
    theme = THEMES["light"]
    saved_instruction = None

    def on_done(val):
        nonlocal saved_instruction
        saved_instruction = val

    dlg = BookInstructionsDialog(theme, "The Silver Kite", "Tone: Whimsical fantasy", on_done=on_done)
    assert dlg.book_name == "The Silver Kite"
    assert dlg._input.text == "Tone: Whimsical fantasy"

    dlg._input.text = "Tone: Dark epic fantasy with dragons."
    dlg._save()

    assert saved_instruction == "Tone: Dark epic fantasy with dragons."


def test_application_book_instructions_button_and_dialog(tmp_path):
    root = tmp_path / "books"
    store = BookStore(str(root))
    store.create_book("Aetheria", custom_instruction="Initial prompt for Aetheria")

    cfg = Config(book_root=str(root))
    app = BookWriterApp(cfg)
    app._build_gui()

    # When no book is selected, instruction button is disabled
    app.current_book = None
    app.refresh_book_selector()
    assert app.current_book == "Aetheria"
    assert app.instruction_btn.disabled is False

    # Save new instruction via application helper
    app.save_book_instruction("Aetheria", "New magical lore instruction.")
    reloaded = store.get_book("Aetheria")
    assert reloaded.custom_instruction == "New magical lore instruction."
    assert app.client.current_book_instruction == "New magical lore instruction."


def test_application_apply_settings(tmp_path):
    root = tmp_path / "books"
    store = BookStore(str(root))
    store.create_book("SciFi Book", custom_instruction="Hard sci-fi only.")
    store.create_book("Fantasy Book", custom_instruction="High fantasy.")

    cfg = Config(book_root=str(root), system_prompt="Original prompt")
    app = BookWriterApp(cfg, settings_path=str(tmp_path / "config.json"))
    app.current_book = "SciFi Book"
    app.client.set_current_book("SciFi Book")

    # Verify initial context
    ctx = app.client.context_messages[0]["content"]
    assert "Original prompt" in ctx
    assert "Hard sci-fi only." in ctx

    # Apply new settings
    new_settings = {
        "server_address": cfg.server_address,
        "book_root": str(root),
        "model": cfg.model,
        "connection_timeout": "30",
        "theme": "light",
        "system_prompt": "Revised universal prompt",
        "book_instructions": {
            "SciFi Book": "Cyberpunk and neon themes.",
            "Fantasy Book": "Dark grimdark fantasy.",
        },
    }

    app._apply_settings(new_settings)

    assert app.cfg.system_prompt == "Revised universal prompt"
    # Applying settings writes them to the settings file immediately.
    persisted = load_config(tmp_path / "config.json")
    assert persisted is not None
    assert persisted.system_prompt == "Revised universal prompt"
    assert persisted.theme.value == "light"
    # Check persistence in store
    reloaded_scifi = store.get_book("SciFi Book")
    assert reloaded_scifi.custom_instruction == "Cyberpunk and neon themes."
    reloaded_fantasy = store.get_book("Fantasy Book")
    assert reloaded_fantasy.custom_instruction == "Dark grimdark fantasy."

    # Check that client context is updated
    updated_ctx = app.client.context_messages[0]["content"]
    assert "Revised universal prompt" in updated_ctx
    assert "Cyberpunk and neon themes." in updated_ctx
    assert "Original prompt" not in updated_ctx
    assert "Hard sci-fi only." not in updated_ctx


def test_application_saves_settings_on_model_change(tmp_path):
    cfg = Config(book_root=str(tmp_path / "books"))
    app = BookWriterApp(cfg, settings_path=str(tmp_path / "config.json"))
    assert load_config(tmp_path / "config.json") is None  # nothing saved yet

    app.on_model_selected("better-model")
    assert app.cfg.model == "better-model"
    persisted = load_config(tmp_path / "config.json")
    assert persisted is not None
    assert persisted.model == "better-model"
    assert persisted.book_root == str(tmp_path / "books")


def test_application_re_selecting_current_model_does_not_rewrite_settings(tmp_path):
    cfg = Config(book_root=str(tmp_path / "books"), model="book-writer-agent")
    app = BookWriterApp(cfg, settings_path=str(tmp_path / "config.json"))

    # The spinner initialising to the current (persisted) model must not touch disk.
    app.on_model_selected(cfg.model)
    assert load_config(tmp_path / "config.json") is None


def test_application_theme_toggle_persists(tmp_path, monkeypatch):
    from app.config import Theme as ConfigTheme
    from app.theme import THEMES

    cfg = Config(book_root=str(tmp_path / "books"), theme=ConfigTheme.LIGHT)
    app = BookWriterApp(cfg, settings_path=str(tmp_path / "config.json"))
    # Headless tests never run the Kivy event loop, so a real theme change would
    # try to rebuild a window that does not exist; stub the rebuild out.
    monkeypatch.setattr(app, "_rebuild_ui", lambda: None)

    app.toggle_theme()

    assert app.cfg.theme is ConfigTheme.DARK
    assert app.theme is THEMES["dark"]
    persisted = load_config(tmp_path / "config.json")
    assert persisted is not None
    assert persisted.theme is ConfigTheme.DARK


def test_application_book_root_change_persists(tmp_path):
    cfg = Config(book_root=str(tmp_path / "first"))
    app = BookWriterApp(cfg, settings_path=str(tmp_path / "config.json"))
    app._build_gui()

    second = tmp_path / "second"
    new_settings = {
        "server_address": cfg.server_address,
        "book_root": str(second),
        "model": cfg.model,
        "connection_timeout": "30",
        "theme": "light",
        "system_prompt": "",
    }
    app._apply_settings(new_settings)

    assert str(app.book_store.root) == str(second)
    persisted = load_config(tmp_path / "config.json")
    assert persisted is not None
    assert persisted.book_root == str(second)


def test_application_uses_settings_file_under_dot_dir_by_default(tmp_path, monkeypatch):
    import app.config as config_module

    fake_home = tmp_path / "home"
    monkeypatch.setattr(config_module.Path, "home",
                        staticmethod(lambda: fake_home))
    cfg = Config(book_root=str(tmp_path / "books"))
    app = BookWriterApp(cfg)
    assert app.settings_path == fake_home / ".agentic-book-writer" / "config.json"


def test_control_bar_api_key(tmp_path):
    from app.gui.control_bar import ControlBar

    root = tmp_path / "books"
    cfg = Config(book_root=str(root))
    app = BookWriterApp(cfg)
    bar = ControlBar(app)

    assert bar.get_api_key() == ""
    assert bar._api_key.password is True

    # Set key
    bar.set_api_key("sk-test-key-12345")
    assert bar.get_api_key() == "sk-test-key-12345"

    # Toggle visibility
    bar._toggle_key_visibility()
    assert bar._api_key.password is False
    assert bar._toggle_key_btn.text == "Hide"

    bar._toggle_key_visibility()
    assert bar._api_key.password is True
    assert bar._toggle_key_btn.text == "Show"

    # Text change syncs to app
    bar._api_key.text = "sk-new-key"
    assert app.api_key == "sk-new-key"
    assert app.client.api_key == "sk-new-key"

