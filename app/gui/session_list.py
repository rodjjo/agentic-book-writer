"""Chat session data model and session list sidebar widget for the Conversation tab."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from kivy.graphics import Color, Line, Rectangle, RoundedRectangle
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from .colors import rgba
from .widgets import FlatButton, font_file, make_label


@dataclass
class ChatSession:
    """Represents an independent chat conversation session."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = "Session"
    number: int = 1
    display_messages: list[dict] = field(default_factory=list)
    client_messages: list[dict] = field(default_factory=list)


class SessionItemWidget(ButtonBehavior, BoxLayout):
    """A clean, non-overlapping single session row in the sessions list."""

    def __init__(
        self,
        session: ChatSession,
        is_active: bool,
        theme,
        on_select: Callable[[str], None],
        **kwargs,
    ):
        kwargs.setdefault("pos_hint", {"x": 0})
        super().__init__(
            orientation="horizontal",
            size_hint=(1.0, None),
            height=36,
            padding=(10, 0),
            spacing=0,
            **kwargs,
        )
        self.session = session
        self.is_active = is_active
        self.theme = theme
        self.on_select = on_select

        bg_color = theme.accent if is_active else theme.panel_2
        fg_color = theme.accent_fg if is_active else theme.fg

        with self.canvas.before:
            self._color = Color(*rgba(bg_color))
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[6])

        self.bind(pos=self._update_rect, size=self._update_rect)

        self._label = Label(
            text=session.title,
            font_name=font_file(bold=is_active),
            font_size=13,
            color=rgba(fg_color),
            halign="left",
            valign="middle",
            size_hint=(1.0, 1.0),
            shorten=True,
            shorten_from="right",
        )
        self._label.bind(size=self._update_text_bounds)
        self.add_widget(self._label)

        self.bind(on_release=lambda *_a: self.on_select(self.session.id))

    def _update_rect(self, *_args):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def _update_text_bounds(self, *_args):
        self._label.text_size = (max(10, self._label.width - 8), self._label.height)


class SessionList(BoxLayout):
    """Sidebar widget displaying action buttons and the list of chat sessions."""

    def __init__(
        self,
        theme,
        *,
        on_new_session: Optional[Callable[[], None]] = None,
        on_select_session: Optional[Callable[[str], None]] = None,
        on_delete_session: Optional[Callable[[str], None]] = None,
        **kwargs,
    ):
        super().__init__(
            orientation="vertical",
            size_hint=(None, 1.0),
            width=210,
            spacing=0,
            padding=(0, 0),
            **kwargs,
        )
        self.theme = theme
        self._on_new_session = on_new_session
        self._on_select_session = on_select_session
        self._on_delete_session = on_delete_session

        self._sessions: list[ChatSession] = []
        self._active_id: str = ""

        with self.canvas.before:
            self._bg_color = Color(*rgba(theme.panel))
            self._bg = Rectangle(pos=self.pos, size=self.size)
            self._border_color = Color(*rgba(theme.border if hasattr(theme, "border") else theme.panel_2))
            self._border = Line(points=[self.right, self.y, self.right, self.top], width=1)

        self.bind(pos=self._update_canvas, size=self._update_canvas)

        self._build_header_and_controls()
        self._build_list()

    def _update_canvas(self, *_args):
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._border.points = [self.right, self.y, self.right, self.top]

    def _build_header_and_controls(self):
        controls_box = BoxLayout(
            orientation="vertical",
            size_hint=(1.0, None),
            spacing=6,
            padding=(10, 8, 10, 8),
        )
        controls_box.bind(minimum_height=controls_box.setter("height"))

        # Title
        lbl = make_label(
            "Sessions",
            bold=True,
            font_size=15,
            color=rgba(self.theme.fg),
            size_hint=(1.0, None),
            height=24,
            auto_size=False,
        )
        controls_box.add_widget(lbl)

        # Action buttons outside the session list
        buttons_row = BoxLayout(
            orientation="horizontal",
            size_hint=(1.0, None),
            height=32,
            spacing=6,
        )

        self._new_btn = FlatButton(
            text="+ New",
            size=(92, 32),
            bg_color=rgba(self.theme.accent),
            fg_color=rgba(self.theme.accent_fg),
            on_release=lambda *_a: self._on_new_click(),
        )
        buttons_row.add_widget(self._new_btn)

        self._remove_btn = FlatButton(
            text="Remove",
            size=(92, 32),
            bg_color=rgba(self.theme.panel_2),
            fg_color=rgba(self.theme.fg),
            on_release=lambda *_a: self._on_remove_click(),
        )
        buttons_row.add_widget(self._remove_btn)

        controls_box.add_widget(buttons_row)
        self.add_widget(controls_box)

    def _build_list(self):
        self._scroll = ScrollView(
            do_scroll_x=False,
            do_scroll_y=True,
            bar_width=4,
            size_hint=(1.0, 1.0),
        )
        self._container = BoxLayout(
            orientation="vertical",
            size_hint=(1.0, None),
            spacing=5,
            padding=(10, 4, 10, 10),
        )
        self._container.bind(minimum_height=self._container.setter("height"))
        self._scroll.add_widget(self._container)
        self.add_widget(self._scroll)

    def _on_new_click(self):
        if self._on_new_session:
            self._on_new_session()

    def _on_remove_click(self):
        if self._on_delete_session and self._active_id:
            self._on_delete_session(self._active_id)

    def set_sessions(self, sessions: list[ChatSession], active_id: str):
        self._sessions = list(sessions)
        self._active_id = active_id
        self._container.clear_widgets()

        for s in self._sessions:
            is_active = (s.id == self._active_id)
            item = SessionItemWidget(
                session=s,
                is_active=is_active,
                theme=self.theme,
                on_select=self._select,
            )
            self._container.add_widget(item)

    def _select(self, session_id: str):
        if self._on_select_session:
            self._on_select_session(session_id)

    def set_theme(self, theme):
        self.theme = theme
        self._bg_color.rgba = rgba(theme.panel)
        self._border_color.rgba = rgba(theme.border if hasattr(theme, "border") else theme.panel_2)
        self.set_sessions(self._sessions, self._active_id)
