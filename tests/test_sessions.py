"""Tests for chat sessions: creation, switching, deletion, and message isolation."""

from __future__ import annotations

import pytest

from app.config import Config
from app.gui.session_list import ChatSession, SessionList
from app.gui.application import BookWriterApp


def test_chat_session_defaults():
    session = ChatSession()
    assert session.id
    assert session.title == "Session"
    assert session.display_messages == []
    assert session.client_messages == []


def test_app_initializes_with_one_session(tmp_path):
    cfg = Config(book_root=str(tmp_path / "books"))
    app = BookWriterApp(cfg)
    assert len(app.sessions) == 1
    first = app.sessions[0]
    assert app.current_session_id == first.id
    assert app.active_session.id == first.id
    assert app.client.messages is first.client_messages


def test_app_create_and_select_sessions(tmp_path):
    cfg = Config(book_root=str(tmp_path / "books"))
    app = BookWriterApp(cfg)

    s1 = app.active_session
    # Append message to session 1
    s1.display_messages.append({"kind": "user", "text": "Hello in session 1"})
    s1.client_messages.append({"role": "user", "content": "Hello in session 1"})

    # Create new session
    app.create_new_session()
    assert len(app.sessions) == 2
    s2 = app.active_session
    assert s2.id != s1.id
    assert s2.title == "Session 2"
    assert s2.display_messages == []
    assert s2.client_messages == []
    assert app.client.messages is s2.client_messages

    # Append message to session 2
    s2.display_messages.append({"kind": "user", "text": "Hello in session 2"})
    s2.client_messages.append({"role": "user", "content": "Hello in session 2"})

    # Switch back to session 1
    app.select_session(s1.id)
    assert app.active_session.id == s1.id
    assert app.client.messages is s1.client_messages
    assert app.client.messages[0]["content"] == "Hello in session 1"

    # Switch to session 2
    app.select_session(s2.id)
    assert app.active_session.id == s2.id
    assert app.client.messages is s2.client_messages
    assert app.client.messages[0]["content"] == "Hello in session 2"


def test_app_delete_session(tmp_path):
    cfg = Config(book_root=str(tmp_path / "books"))
    app = BookWriterApp(cfg)

    app.create_new_session()
    app.create_new_session()
    assert len(app.sessions) == 3

    s1, s2, s3 = app.sessions
    # Currently active is s3
    assert app.current_session_id == s3.id

    # Delete inactive s2
    app.delete_session(s2.id)
    assert len(app.sessions) == 2
    assert [s.id for s in app.sessions] == [s1.id, s3.id]
    assert app.current_session_id == s3.id

    # Delete active s3
    app.delete_session(s3.id)
    assert len(app.sessions) == 1
    assert app.current_session_id == s1.id

    # Deleting the only remaining session resets it to a fresh session
    old_id = s1.id
    app.delete_session(old_id)
    assert len(app.sessions) == 1
    assert app.sessions[0].id != old_id
    assert app.current_session_id == app.sessions[0].id


def test_gui_compose_visibility_and_sessions_panel(tmp_path):
    cfg = Config(book_root=str(tmp_path / "books"))
    app = BookWriterApp(cfg)
    root = app._build_gui()

    # Conversation tab is active initially
    assert app._active_tab == "chat"
    assert app._panels["chat"] is app.chat_panel
    assert app.compose.parent is app.chat_panel
    assert app.chat_panel.parent is app._content

    # Compose bar is inside the chat panel
    assert app.compose in app.chat_panel.children

    # Session list is in the chat panel
    assert app.session_list.parent is not None

    # Switch to books tab: compose bar is hidden
    app.show_tab("books")
    assert app._active_tab == "books"
    assert app.books.parent is app._content
    assert app.chat_panel.parent is None
    assert app.compose.parent is not app._content

    # Switch to logs tab: compose bar is hidden
    app.show_tab("logs")
    assert app._active_tab == "logs"
    assert app.logs_panel.parent is app._content
    assert app.chat_panel.parent is None
    assert app.compose.parent is not app._content

    # Switch back to chat tab: compose bar is visible again
    app.show_tab("chat")
    assert app._active_tab == "chat"
    assert app.chat_panel.parent is app._content
    assert app.compose.parent is app.chat_panel


def test_session_list_external_buttons(tmp_path):
    cfg = Config(book_root=str(tmp_path / "books"))
    app = BookWriterApp(cfg)
    app._build_gui()

    # The session list container has 1 item initially
    assert len(app.session_list._container.children) == 1

    # Click the external + New button
    app.session_list._new_btn.dispatch("on_release")
    assert len(app.sessions) == 2
    assert len(app.session_list._container.children) == 2

    # Click the external Remove button -> opens confirmation dialog
    app.session_list._remove_btn.dispatch("on_release")
    assert hasattr(app, "_active_dialog")
    assert app._active_dialog is not None
    # If user cancels, session is not removed
    app._active_dialog._done(False)
    assert len(app.sessions) == 2
    assert len(app.session_list._container.children) == 2

    # Click Remove button again and confirm
    app.session_list._remove_btn.dispatch("on_release")
    assert app._active_dialog is not None
    app._active_dialog._done(True)
    assert len(app.sessions) == 1
    assert len(app.session_list._container.children) == 1


def test_session_number_reuse_and_single_session_reset(tmp_path):
    cfg = Config(book_root=str(tmp_path / "books"))
    app = BookWriterApp(cfg)

    # Initial session is Session 1
    assert app.sessions[0].title == "Session 1"
    assert app.sessions[0].number == 1

    # Create Session 2 and Session 3
    app.create_new_session()
    assert app.sessions[-1].title == "Session 2"
    assert app.sessions[-1].number == 2

    app.create_new_session()
    assert app.sessions[-1].title == "Session 3"
    assert app.sessions[-1].number == 3

    # Delete Session 2
    s2 = next(s for s in app.sessions if s.number == 2)
    app.delete_session(s2.id)
    assert [s.number for s in app.sessions] == [1, 3]

    # Creating a new session reuses number 2
    app.create_new_session()
    assert any(s.number == 2 for s in app.sessions)
    assert [s.title for s in app.sessions] == ["Session 1", "Session 2", "Session 3"]

    # Delete Session 1 and Session 3, leaving only Session 2
    s1 = next(s for s in app.sessions if s.number == 1)
    s3 = next(s for s in app.sessions if s.number == 3)
    app.delete_session(s1.id)
    app.delete_session(s3.id)
    assert len(app.sessions) == 1
    assert app.sessions[0].number == 2

    # Deleting the only remaining session resets to Session 1, NOT incrementing
    app.delete_session(app.sessions[0].id)
    assert len(app.sessions) == 1
    assert app.sessions[0].title == "Session 1"
    assert app.sessions[0].number == 1

    # Deleting the single session again still resets to Session 1
    app.delete_session(app.sessions[0].id)
    assert len(app.sessions) == 1
    assert app.sessions[0].title == "Session 1"
    assert app.sessions[0].number == 1



