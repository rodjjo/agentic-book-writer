"""The Book Writer Kivy application: window shell + controller logic.

This is the Kivy replacement of the original Tkinter ``Application``.  It owns the
configuration, the client, the book store and the whole widget tree (control bar on top,
Conversation / Books / Logs tabs in the middle, instruction compose bar at the bottom)
and drives the network work from background threads while every UI mutation is marshalled
through a thread-safe queue drained on Kivy's main thread via ``Clock``.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import uuid
from typing import Optional

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.spinner import Spinner

from .. import logs as app_logs
from ..books_store import BookStore
from ..client import Client
from ..config import Config, Theme as ConfigTheme
from ..theme import THEMES
from ..tools import ToolContext
from .books_panel import BooksPanel
from .chat_log import ChatLog
from .colors import rgba
from .compose_bar import ComposeBar
from .control_bar import ControlBar
from .dialogs import BookInstructionsDialog, ConfirmDialog, ImagePreviewDialog, SettingsDialog
from .log_view import LogView
from .session_list import ChatSession, SessionList
from .widgets import FlatButton, ResponsiveSpinner, app_icon_path, font_file, make_label, note_label

log = app_logs.get_logger("gui")

TAB_ORDER = ["chat", "books", "logs"]
TAB_LABELS = {"chat": "Conversation", "books": "Books", "logs": "Logs"}


class BookWriterApp(App):
    """Kivy application: controller for the whole Book Writer GUI."""

    def __init__(self, config: Config, autoconnect: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.cfg = config
        self.title = "Book Writer"
        self.autoconnect = autoconnect
        self.theme = THEMES[config.theme.value]

        # persistent state shared across theme rebuilds
        self.book_store = BookStore(config.book_root)
        self.tool_ctx = ToolContext(self.book_store, on_event=self._on_book_change)
        self.client = Client(config, tool_context=self.tool_ctx)
        self.client.set_model(config.model)
        self.current_book: Optional[str] = None

        # sessions management
        initial_session = ChatSession(
            id=str(uuid.uuid4()),
            title="Session 1",
            number=1,
            display_messages=[],
            client_messages=[],
        )
        self.sessions: list[ChatSession] = [initial_session]
        self.current_session_id: str = initial_session.id
        self._chat_messages: list[dict] = initial_session.display_messages
        self.client.messages = initial_session.client_messages

        self._connected = False
        self._busy = False
        self._active_tab = "chat"
        self._ui_queue: queue.Queue = queue.Queue()
        self._books_refresh_scheduled = False
        self.api_key: str = ""

    def on_api_key_changed(self, key: str) -> None:
        self.api_key = key.strip()
        if hasattr(self, "client"):
            self.client.set_api_key(self.api_key)

    def _get_next_session_number(self, exclude_id: Optional[str] = None) -> int:
        """Find the lowest positive integer n >= 1 that is not currently used by any session."""
        used: set[int] = set()
        for s in self.sessions:
            if exclude_id and s.id == exclude_id:
                continue
            if hasattr(s, "number") and s.number > 0:
                used.add(s.number)
            m = re.match(r"^Session\s+(\d+)$", s.title.strip())
            if m:
                used.add(int(m.group(1)))
        n = 1
        while n in used:
            n += 1
        return n

    @property
    def active_session(self) -> ChatSession:
        for s in self.sessions:
            if s.id == self.current_session_id:
                return s
        if not self.sessions:
            n = self._get_next_session_number()
            s = ChatSession(id=str(uuid.uuid4()), title=f"Session {n}", number=n)
            self.sessions.append(s)
            self.current_session_id = s.id
            return s
        self.current_session_id = self.sessions[0].id
        return self.sessions[0]

    def create_new_session(self) -> None:
        if self._busy:
            if hasattr(self, "control_bar"):
                self.control_bar.set_status("please wait for assistant to finish turn")
            return
        n = self._get_next_session_number()
        new_session = ChatSession(
            id=str(uuid.uuid4()),
            title=f"Session {n}",
            number=n,
            display_messages=[],
            client_messages=[],
        )
        self.sessions.append(new_session)
        self.sessions.sort(key=lambda s: getattr(s, "number", 9999))
        self.select_session(new_session.id)
        if hasattr(self, "compose"):
            self.compose.focus_input()
        app_logs.get_logger("chat").info("created new chat session: %s", new_session.title)

    def select_session(self, session_id: str) -> None:
        if self._busy:
            if hasattr(self, "control_bar"):
                self.control_bar.set_status("please wait for assistant to finish turn")
            return
        self.current_session_id = session_id
        session = self.active_session
        self._chat_messages = session.display_messages
        if hasattr(self, "chat"):
            self.chat.messages = session.display_messages
            self.chat._schedule_render()
        self.client.messages = session.client_messages
        if hasattr(self, "session_list"):
            self.session_list.set_sessions(self.sessions, self.current_session_id)
        app_logs.get_logger("chat").info("switched to chat session: %s", session.title)

    def prompt_delete_session(self, session_id: str) -> None:
        """Prompt user for confirmation before removing a session."""
        if self._busy:
            if hasattr(self, "control_bar"):
                self.control_bar.set_status("please wait for assistant to finish turn")
            return
        session = next((s for s in self.sessions if s.id == session_id), None)
        if not session:
            return

        def _on_confirm(confirmed: bool) -> None:
            if confirmed:
                self.delete_session(session_id)

        self._active_dialog = ConfirmDialog(
            self.theme,
            title="Remove Session",
            message=f"Are you sure you want to remove '{session.title}'?",
            on_done=_on_confirm,
            danger=True,
            ok_text="Remove",
        )
        self._active_dialog.open()

    def delete_session(self, session_id: str) -> None:
        if self._busy:
            if hasattr(self, "control_bar"):
                self.control_bar.set_status("please wait for assistant to finish turn")
            return
        idx = next((i for i, s in enumerate(self.sessions) if s.id == session_id), -1)
        if idx == -1:
            return

        deleted_session = self.sessions[idx]
        if len(self.sessions) <= 1:
            fresh = ChatSession(
                id=str(uuid.uuid4()),
                title="Session 1",
                number=1,
                display_messages=[],
                client_messages=[],
            )
            self.sessions = [fresh]
            self.current_session_id = fresh.id
        else:
            is_active = session_id == self.current_session_id
            self.sessions.pop(idx)
            if is_active:
                new_idx = min(idx, len(self.sessions) - 1)
                self.current_session_id = self.sessions[new_idx].id

        session = self.active_session
        self._chat_messages = session.display_messages
        if hasattr(self, "chat"):
            self.chat.messages = session.display_messages
            self.chat._schedule_render()
        self.client.messages = session.client_messages
        if hasattr(self, "session_list"):
            self.session_list.set_sessions(self.sessions, self.current_session_id)
        app_logs.get_logger("chat").info("removed chat session: %s", deleted_session.title)

    # ------------------------------------------------------------------
    # Build / teardown
    # ------------------------------------------------------------------
    def build(self):
        app_logs.install()
        app_logs.get_logger("boot").info("Book Writer starting (kivy)...")
        root = self._build_gui()
        Clock.schedule_interval(lambda _dt: self._drain_ui(), 1 / 30.0)
        Clock.schedule_once(lambda _dt: self._on_first_frame(), 0.5)
        return root

    def on_start(self):
        try:
            from kivy.core.window import Window

            Window.title = "Book Writer"
            Window.minimum_width, Window.minimum_height = 880, 560
            Window.set_icon(app_icon_path())
        except Exception as exc:  # pragma: no cover - cosmetic
            app_logs.get_logger("boot").debug("window decoration failed: %s", exc)

    def _build_gui(self) -> BoxLayout:
        """(Re)build the whole widget tree with the current theme."""
        theme = self.theme
        root = BoxLayout(orientation="vertical", padding=(0, 0, 0, 0),
                         spacing=0)

        # -- top control bar --------------------------------------------
        self.control_bar = ControlBar(self)
        root.add_widget(self.control_bar)

        # -- tabs -------------------------------------------------------
        self._tab_buttons: dict = {}
        tab_bar = BoxLayout(orientation="horizontal", spacing=6,
                            size_hint=(1.0, None), height=38, padding=(10, 4))
        for key in TAB_ORDER:
            btn = FlatButton(text=TAB_LABELS[key], size_hint=(None, None),
                             size=(110 if key == "chat" else 85, 30),
                             padding=(12, 4),
                             on_release=lambda *_a, k=key: self.show_tab(k))
            btn.key = key
            tab_bar.add_widget(btn)
            self._tab_buttons[key] = btn

        # Spacer between tab buttons and book selector
        tab_bar.add_widget(BoxLayout())

        # Folder button placed before the book selection control
        self.folder_btn = FlatButton(
            text="Folder...", size=(84, 30),
            bg_color=rgba(theme.panel_2), fg_color=rgba(theme.fg),
            on_release=lambda *_a: self.books._pick_folder() if hasattr(self, "books") else None,
        )
        tab_bar.add_widget(self.folder_btn)

        # Book selector positioned at the right of the window
        book_lbl = make_label("Book:", color=rgba(theme.muted), font_size=13,
                              size_hint=(None, 1.0))
        tab_bar.add_widget(book_lbl)

        self.book_spinner = ResponsiveSpinner(
            text="no books", values=(), size_hint=(None, None), size=(180, 30),
            font_name=font_file(), font_size=13, color=rgba(theme.fg),
            background_color=rgba(theme.input_bg), background_normal="",
        )
        self.book_spinner.bind(text=lambda _sp, value: self._on_book_spinner_text(value))
        tab_bar.add_widget(self.book_spinner)

        self.instruction_btn = FlatButton(
            text="Instructions", size=(106, 30),
            bg_color=rgba(theme.panel_2), fg_color=rgba(theme.fg),
            on_release=lambda *_a: self.open_book_instructions(),
        )
        self.instruction_btn.disabled = True
        tab_bar.add_widget(self.instruction_btn)

        self.delete_book_btn = FlatButton(
            text="Delete", size=(74, 30),
            bg_color=rgba(theme.panel_2), fg_color=rgba("#dc2626"),
            on_release=lambda *_a: self.prompt_delete_current_book(),
        )
        self.delete_book_btn.disabled = True
        tab_bar.add_widget(self.delete_book_btn)

        def _update_tab_bar_responsiveness(*_a):
            avail = tab_bar.width
            if avail > 1050:
                self.book_spinner.width = min(280, max(180, int(avail * 0.2)))
            else:
                self.book_spinner.width = 170

        tab_bar.bind(width=_update_tab_bar_responsiveness)

        root.add_widget(tab_bar)
        self._style_tabs()

        # -- conversation panel -----------------------------------------
        self.chat = ChatLog(theme, messages=self.active_session.display_messages)
        self.session_list = SessionList(
            theme,
            on_new_session=self.create_new_session,
            on_select_session=self.select_session,
            on_delete_session=self.prompt_delete_session,
        )
        self.session_list.set_sessions(self.sessions, self.current_session_id)

        chat_body = BoxLayout(orientation="horizontal", spacing=0, size_hint=(1.0, 1.0))
        chat_body.add_widget(self.session_list)
        chat_body.add_widget(self.chat)

        self.compose = ComposeBar(self, theme)

        self.chat_panel = BoxLayout(orientation="vertical", spacing=0, size_hint=(1.0, 1.0))
        self.chat_panel.add_widget(chat_body)
        self.chat_panel.add_widget(self.compose)

        # -- books panel ------------------------------------------------
        self.books = BooksPanel(
            theme, self.book_store,
            on_book_selected=self.on_book_selected,
            on_change_root=self.set_book_root,
            on_book_deleted=self.delete_book,
            on_instruction_updated=self.save_book_instruction)

        # -- logs panel -------------------------------------------------
        self.logs_panel = BoxLayout(orientation="vertical")
        head = BoxLayout(orientation="horizontal", spacing=8,
                         size_hint=(1.0, None), height=38, padding=(10, 4))
        head.add_widget(make_label("Logs", font_size=15, bold=True,
                                   color=rgba(theme.fg), size_hint=(None, 1.0)))
        self._logs_count = note_label("", color=rgba(theme.muted), size_hint=(None, 1.0))
        head.add_widget(self._logs_count)
        head.add_widget(BoxLayout())
        clear_logs = FlatButton(text="Clear", size=(70, 30),
                                bg_color=rgba(theme.panel_2), fg_color=rgba(theme.fg),
                                on_release=lambda *_a: self.log_view.clear())
        head.add_widget(clear_logs)
        self.logs_panel.add_widget(head)
        self.log_view = LogView(theme)
        self.logs_panel.add_widget(self.log_view)

        self._panels = {
            "chat": self.chat_panel,
            "books": self.books,
            "logs": self.logs_panel,
        }
        self._content = BoxLayout()
        root.add_widget(self._content)

        self.show_tab(self._active_tab, force=True)

        # refresh books/logs so the very first paint is not empty
        self.refresh_book_selector()
        self.books.refresh()
        self._refresh_logs()
        return root

    def _on_first_frame(self) -> None:
        self.compose.focus_input()
        if self.autoconnect:
            app_logs.get_logger("boot").info("autoconnect: yes")
            Clock.schedule_once(lambda _dt: self.toggle_connection(), 0.8)

    def _style_tabs(self) -> None:
        for key, btn in self._tab_buttons.items():
            active = key == self._active_tab
            btn.bg_color = rgba(self.theme.accent if active else self.theme.panel_2)
            btn.fg_color = rgba(self.theme.accent_fg if active else self.theme.fg)
        if hasattr(self, "folder_btn"):
            self.folder_btn.bg_color = rgba(self.theme.panel_2)
            self.folder_btn.fg_color = rgba(self.theme.fg)
        if hasattr(self, "book_spinner"):
            self.book_spinner.color = rgba(self.theme.fg)
            self.book_spinner.background_color = rgba(self.theme.input_bg)
        if hasattr(self, "instruction_btn"):
            self.instruction_btn.bg_color = rgba(self.theme.panel_2)
            self.instruction_btn.fg_color = rgba(self.theme.fg)
        if hasattr(self, "delete_book_btn"):
            self.delete_book_btn.bg_color = rgba(self.theme.panel_2)

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------
    def show_tab(self, name: str, force: bool = False) -> None:
        if name not in self._panels:
            return
        if name == self._active_tab and not force:
            return
        self._active_tab = name
        self._content.clear_widgets()
        self._content.add_widget(self._panels[name])
        self._style_tabs()
        if name == "chat":
            if hasattr(self, "compose"):
                Clock.schedule_once(lambda _dt: self.compose.focus_input(), 0.1)
        elif name == "books":
            self.books.refresh()
        elif name == "logs":
            self._refresh_logs()

    # ------------------------------------------------------------------
    # Theme / settings
    # ------------------------------------------------------------------
    def toggle_theme(self) -> None:
        if self._busy:
            return
        new_name = "dark" if self.theme.name == "light" else "light"
        self.cfg.theme = ConfigTheme(new_name)
        self.theme = THEMES[new_name]
        self._rebuild_ui()
        app_logs.get_logger("settings").info("theme set to %s", new_name)

    def _rebuild_ui(self) -> None:
        """Rebuild the widget tree (theme switch) preserving conversation/state."""
        text = self.compose.get_text()
        image = self.compose.image_path()
        api_key = self.control_bar.get_api_key() if hasattr(self, "control_bar") else self.api_key
        tab = self._active_tab
        root = self.root
        root.clear_widgets()
        new_root = self._build_gui()
        root.add_widget(new_root)
        if hasattr(self, "control_bar") and api_key:
            self.control_bar.set_api_key(api_key)
        self.compose.set_text(text)
        if image:
            self.compose.set_image(image)
        self.show_tab(tab, force=True)
        Clock.schedule_once(lambda _dt: self.compose.focus_input(), 0.2)

    def open_settings(self) -> None:
        if self._busy:
            return
        settings = {
            "server_address": self.control_bar.get_server_address() or self.cfg.server_address,
            "book_root": str(self.book_store.root),
            "model": self.cfg.model,
            "connection_timeout": str(self.cfg.connection_timeout),
            "theme": self.theme.name,
            "system_prompt": self.cfg.system_prompt,
        }
        SettingsDialog(self.theme, settings, on_done=self._apply_settings).open()

    def _apply_settings(self, settings: Optional[dict]) -> None:
        if not settings:
            return
        changed_theme = False
        try:
            self.cfg.update_server_address(settings.get("server_address")
                                              or self.cfg.server_address)
            self._recreate_client()
        except Exception as exc:
            # errors go to the Logs tab, never the conversation
            app_logs.get_logger("settings").error("invalid server address: %s", exc)
            return
        if settings.get("book_root") and settings["book_root"] != str(self.book_store.root):
            self.set_book_root(settings["book_root"])
        if settings.get("model"):
            self.cfg.model = settings["model"]
            self.client.set_model(self.cfg.model)
            if hasattr(self, "control_bar"):
                self.control_bar.set_model(self.cfg.model)
        try:
            self.cfg.connection_timeout = float(settings.get("connection_timeout")
                                                   or self.cfg.connection_timeout)
        except ValueError:
            pass
        if "system_prompt" in settings:
            self.cfg.system_prompt = settings["system_prompt"]
            self.client.config.system_prompt = self.cfg.system_prompt

        book_instructions = settings.get("book_instructions", {})
        for book_name, instruction in book_instructions.items():
            try:
                self.book_store.update_book_instruction(book_name, instruction)
            except Exception as exc:
                app_logs.get_logger("settings").error("failed to update instruction for '%s': %s", book_name, exc)

        book = self.book_store.get_book(self.current_book) if self.current_book else None
        self.client.set_current_book(
            self.current_book,
            book_id=book.id if book else None,
            custom_instruction=book.custom_instruction if book else None,
        )

        theme_name = settings.get("theme", self.theme.name)
        if theme_name in THEMES and THEMES[theme_name] is not self.theme:
            self.cfg.theme = ConfigTheme(theme_name)
            self.theme = THEMES[theme_name]
            changed_theme = True
        if hasattr(self, "control_bar"):
            self.control_bar.set_server_address(self.cfg.server_address)
        if changed_theme:
            self._rebuild_ui()
        else:
            self._refresh_logs()
        app_logs.get_logger("settings").info("settings updated")

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def toggle_connection(self) -> None:
        if self._connected:
            self._connected = False
            self.control_bar.set_connected(False)
            app_logs.get_logger("conn").info("disconnected")
            return
        if self._busy:
            return
        if hasattr(self, "control_bar"):
            self.api_key = self.control_bar.get_api_key()
            self.client.set_api_key(self.api_key)
        self._busy = True
        self.control_bar.set_busy(True)
        self.control_bar.set_status("Connecting...")
        try:
            address = self.control_bar.get_server_address()
            if address and address != self.cfg.server_address:
                self.cfg.update_server_address(address)
                self._recreate_client()
        except Exception as exc:
            self._busy = False
            self.control_bar.set_busy(False)
            self.control_bar.set_status("invalid address")
            # errors go to the Logs tab, never the conversation
            app_logs.get_logger("conn").error("invalid server address: %s", exc)
            return
        threading.Thread(target=self._connect_worker, daemon=True).start()

    def _connect_worker(self) -> None:
        try:
            models = self.client.ping()
            self._ui_queue.put(("connect_done", (True, models)))
        except Exception as exc:
            self._ui_queue.put(("connect_done", (False, str(exc))))

    def _recreate_client(self) -> None:
        """Rebuild client/transport after a server-address change."""
        self.tool_ctx = ToolContext(self.book_store, on_event=self._on_book_change)
        self.client = Client(self.cfg, tool_context=self.tool_ctx, api_key=self.api_key)
        self.client.set_model(self.cfg.model)
        self.client.messages = self.active_session.client_messages
        book = self.book_store.get_book(self.current_book) if self.current_book else None
        self.client.set_current_book(
            self.current_book,
            book_id=book.id if book else None,
            custom_instruction=book.custom_instruction if book else None,
        )

    # ------------------------------------------------------------------
    # Books
    # ------------------------------------------------------------------
    def _on_book_spinner_text(self, value: str) -> None:
        if value and value != "no books":
            self.on_book_selected(value)

    def refresh_book_selector(self) -> None:
        try:
            books = self.book_store.list_books()
        except Exception:
            books = []
        names = [b.name for b in books]
        if hasattr(self, "book_spinner"):
            self.book_spinner.values = names
            if not names:
                self.book_spinner.text = "no books"
                self.current_book = None
            elif self.current_book in names:
                self.book_spinner.text = self.current_book
            else:
                self.book_spinner.text = names[0]
                self.on_book_selected(names[0])
        has_book = bool(self.current_book and self.current_book != "no books")
        if hasattr(self, "instruction_btn"):
            self.instruction_btn.disabled = not has_book
        if hasattr(self, "delete_book_btn"):
            self.delete_book_btn.disabled = not has_book

    def set_book_root(self, path: str) -> None:
        if not path:
            return
        self.cfg.book_root = path
        self.book_store = BookStore(path)
        self.tool_ctx = ToolContext(self.book_store, on_event=self._on_book_change)
        self.client = Client(self.cfg, tool_context=self.tool_ctx)
        self.client.set_model(self.cfg.model)
        self.client.messages = self.active_session.client_messages
        self.refresh_book_selector()
        book = self.book_store.get_book(self.current_book) if self.current_book else None
        self.client.set_current_book(
            self.current_book,
            book_id=book.id if book else None,
            custom_instruction=book.custom_instruction if book else None,
        )
        self.books.set_store(self.book_store)
        self.books.select_book(self.current_book or "")
        app_logs.get_logger("books").info("books folder -> %s", path)

    def on_book_selected(self, name: Optional[str]) -> None:
        name = name or None
        self.current_book = name
        if hasattr(self, "book_spinner"):
            target_text = name or "no books"
            if self.book_spinner.text != target_text:
                self.book_spinner.text = target_text
        has_book = bool(self.current_book and self.current_book != "no books")
        if hasattr(self, "instruction_btn"):
            self.instruction_btn.disabled = not has_book
        if hasattr(self, "delete_book_btn"):
            self.delete_book_btn.disabled = not has_book
        book = self.book_store.get_book(name) if name else None
        self.client.set_current_book(
            self.current_book,
            book_id=book.id if book else None,
            custom_instruction=book.custom_instruction if book else None,
        )
        if hasattr(self, "books"):
            self.books.select_book(self.current_book or "")
        if self.current_book:
            app_logs.get_logger("books").info("working in book '%s' (ID: %s)", self.current_book, book.id if book else "unknown")
        else:
            app_logs.get_logger("books").info("no book selected")

    def open_book_instructions(self) -> None:
        if not self.current_book or self.current_book == "no books":
            return
        book = self.book_store.get_book(self.current_book)
        current_inst = book.custom_instruction if book else ""

        def _on_done(new_inst: Optional[str]) -> None:
            if new_inst is not None:
                self.save_book_instruction(self.current_book, new_inst)

        self._active_dialog = BookInstructionsDialog(
            self.theme,
            self.current_book,
            current_inst,
            on_done=_on_done,
        )
        self._active_dialog.open()

    def save_book_instruction(self, book_name: str, instruction: str) -> None:
        try:
            self.book_store.update_book_instruction(book_name, instruction)
            book = self.book_store.get_book(book_name)
            if self.current_book == book_name:
                self.client.set_current_book(
                    self.current_book,
                    book_id=book.id if book else None,
                    custom_instruction=instruction,
                )
            app_logs.get_logger("books").info("updated instruction for '%s'", book_name)
        except Exception as exc:
            app_logs.get_logger("books").error("failed to update instruction for '%s': %s", book_name, exc)

    def prompt_delete_current_book(self) -> None:
        if not self.current_book or self.current_book == "no books":
            return
        book_name = self.current_book

        def _on_confirm(confirmed: bool) -> None:
            if confirmed:
                self.delete_book(book_name)

        self._active_dialog = ConfirmDialog(
            self.theme,
            title="Delete Book",
            message=f"Are you sure you want to permanently delete '{book_name}' and all its chapters?",
            on_done=_on_confirm,
            danger=True,
            ok_text="Delete",
        )
        self._active_dialog.open()

    def delete_book(self, name: str) -> None:
        try:
            self.book_store.delete_book(name)
            app_logs.get_logger("books").info("deleted book '%s'", name)
        except Exception as exc:
            app_logs.get_logger("books").error("failed to delete book '%s': %s", name, exc)
            return
        if self.current_book == name:
            self.current_book = None
        self.refresh_book_selector()
        self.books.refresh()

    def _on_book_change(self, data: dict) -> None:
        # Called from the tool worker thread → bounce to the UI thread.
        try:
            self._ui_queue.put(("book_changed", dict(data)))
        except Exception:  # pragma: no cover
            pass

    def request_books_refresh(self) -> None:
        if self._books_refresh_scheduled:
            return
        self._books_refresh_scheduled = True
        Clock.schedule_once(self._do_books_refresh, 0.05)

    def _do_books_refresh(self, _dt) -> None:
        self._books_refresh_scheduled = False
        try:
            self.refresh_book_selector()
            self.books.refresh()
            self.books.select_book(self.current_book or "")
        except Exception as exc:
            app_logs.get_logger("books").warning("refresh failed: %s", exc)

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------
    def _refresh_logs(self) -> None:
        if hasattr(self, "log_view"):
            self.log_view.refresh()
            if hasattr(self, "_logs_count"):
                self._logs_count.text = f"{len(self.log_view.entries)} entries"

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------
    def send_now(self) -> None:
        if self._busy:
            return
        text = self.compose.get_text()
        image = self.compose.image_path()
        if not text and not image:
            self.control_bar.set_status("type an instruction or attach an image first")
            return

        session = self.active_session
        if session.title.startswith("Session ") and text:
            first_line = text.strip().splitlines()[0].strip()
            if first_line:
                snippet = first_line[:14] + ("…" if len(first_line) > 14 else "")
                session.title = snippet
                if hasattr(self, "session_list"):
                    self.session_list.set_sessions(self.sessions, self.current_session_id)

        display = text
        if image:
            display = (text + "  ") if text else ""
            display += f"[attached image: {image.split('/')[-1]}]"
        self.chat.add_user(display or "(image attached)")
        self.compose.clear_text()
        self.compose.clear_image()

        self._busy = True
        if hasattr(self, "control_bar"):
            self.api_key = self.control_bar.get_api_key()
            self.client.set_api_key(self.api_key)
        self.compose.set_busy(True)
        self.control_bar.set_status("working...")
        app_logs.get_logger("send").info("sending instruction to %s (model %s)",
                                         self.cfg.server_address, self.client.model)
        threading.Thread(target=self._send_worker, args=(text, image), daemon=True).start()

    def _send_worker(self, text: str, image: Optional[str]) -> None:
        try:
            self.client.send(text, image, on_event=lambda e: self._ui_queue.put(("event", e)))
        except Exception as exc:
            self._ui_queue.put(("event", {"kind": "error", "message": str(exc)}))
        finally:
            self._ui_queue.put(("send_done", None))

    # ------------------------------------------------------------------
    # UI queue
    # ------------------------------------------------------------------
    def _drain_ui(self, *_args) -> None:
        try:
            while True:
                item = self._ui_queue.get_nowait()
                kind = item[0]
                if kind == "event":
                    self._handle_event(item[1])
                elif kind == "book_changed":
                    self.request_books_refresh()
                elif kind == "connect_done":
                    self._on_connect_done(*item[1])
                elif kind == "send_done":
                    self._release()
        except queue.Empty:
            pass
        if app_logs.take_dirty():
            self._refresh_logs()

    def _handle_event(self, event: dict) -> None:
        kind = event.get("kind")
        if kind == "assistant":
            self.chat.add_assistant(event.get("content") or "")
        elif kind == "assistant_delta":
            # streamed tokens: render them into the live bubble immediately
            self.chat.stream_append(event.get("content") or "")
        elif kind == "tool_call":
            # Tool-calling reports belong on the Logs tab, not the conversation.
            name = event.get("name", "?")
            args = event.get("args", {})
            try:
                rendered = json.dumps(args, ensure_ascii=False)[:900]
            except (TypeError, ValueError):
                rendered = str(args)[:900]
            app_logs.get_logger("agent").info("calling tool %s %s", name, rendered)
        elif kind == "tool_result":
            # Tool results are reported on the Logs tab; only the book panel needs
            # to know they ran so it can refresh.
            name = event.get("name", "?")
            app_logs.log_tool(name, event.get("result", {}))
            self.request_books_refresh()
        elif kind == "error":
            # Connection issues / failures go to the Logs tab, never the conversation.
            app_logs.get_logger("agent").error(
                "error: %s", event.get("message", "Unknown error"))
        elif kind == "loop_start":
            pass

    def _on_connect_done(self, ok: bool, data) -> None:
        self._busy = False
        self.control_bar.set_busy(False)
        if ok:
            models = data or []
            self._connected = True
            self.control_bar.set_connected(True)
            self.control_bar.update_models(models or [self.cfg.model])
            self.cfg.model = self.control_bar.get_model() or self.cfg.model
            self.client.set_model(self.cfg.model)
            self.control_bar.set_status("connected")
            app_logs.get_logger("conn").info("connected - models: %s",
                                             ", ".join(models) if models else "none")
        else:
            self._connected = False
            self.control_bar.set_connected(False)
            self.control_bar.set_status("disconnected")
            # connection issues are recorded for the Logs tab, not the conversation
            app_logs.get_logger("conn").error("connect failed: %s", data)

    def _release(self) -> None:
        self._busy = False
        self.compose.set_busy(False)
        self.control_bar.set_busy(False)
        self.control_bar.set_status("connected" if self._connected else "disconnected")
        self.request_books_refresh()
        self.chat.scroll_to_bottom()
        app_logs.get_logger("send").info("turn finished")

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    def on_model_selected(self, model: str) -> None:
        if model and model != "model":
            self.cfg.model = model
            self.client.set_model(model)
            app_logs.get_logger("conn").info("model selected: %s", model)

    def show_image(self, path: str) -> None:
        ImagePreviewDialog(self.theme, path).open()

    def snap(self, path: str) -> None:
        """Save a screenshot (dev/test helper)."""
        from kivy.core.window import Window
        Window.screenshot(path)
