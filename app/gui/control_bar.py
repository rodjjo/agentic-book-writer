"""The top control bar: server address, connection, model, theme, settings.

Two stacked rows of controls (no scrollbar, nothing to drag/pan):

* **Row 1** — brand, the server-address field (flexible width → absorbs window resizes)
  and the Connect/Disconnect button.
* **Row 2** — the model selector, theme + settings buttons and the connection status
  (right aligned).

The bar keeps the fixed dark slate surface (independent of the theme) like a title bar,
so the status stays legible in both light and dark mode.  Everything uses layout hints,
so the bar (and the rest of the window) reflows when the window is resized.
"""

from __future__ import annotations

from kivy.graphics import Color, Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image as KivyImage
from kivy.uix.label import Label
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput

from .colors import rgba
from .widgets import (FlatButton, ResponsiveSpinner, app_icon_path, font_file, icon_path)

SLATE = "#1f2937"          # bar background
SLATE_INPUT = "#111827"    # dark field background
SLATE_HINT = "#9ca3af"     # muted label text


class _BarField(TextInput):
    """Dark text input used inside the control bar (flexible width)."""

    def __init__(self, hint: str, password: bool = False, **kwargs):
        kwargs.setdefault("size_hint_min_x", 160.0)
        kwargs.setdefault("font_size", 13)
        super().__init__(
            hint_text=hint,
            multiline=False,
            password=password,
            size_hint=(1.0, 1.0),
            font_name=font_file(),
            background_color=rgba(SLATE_INPUT),
            foreground_color=(1, 1, 1, 1),
            hint_text_color=rgba(SLATE_HINT),
            cursor_color=rgba("#ffffff"),
            padding=(10, 8),
            **kwargs,
        )


class ControlBar(BoxLayout):
    """Top bar with connection and book configuration controls (three rows)."""

    def __init__(self, app, **kwargs):
        super().__init__(orientation="vertical", spacing=4, padding=(8, 5),
                         size_hint=(1.0, None), height=126)
        self._app = app
        self._connected = False

        with self.canvas.before:
            Color(*rgba(SLATE, 1.0))
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=lambda *_a: setattr(self._bg, "pos", self.pos),
                  size=lambda *_a: setattr(self._bg, "size", self.size))

        # -- row 1: brand + server address + connect ------------------------
        row1 = BoxLayout(orientation="horizontal", spacing=8,
                         size_hint=(1.0, None), height=36)
        logo = KivyImage(source=app_icon_path(), size_hint=(None, None),
                         size=(32, 32))
        logo.pos_hint = {"center_y": 0.5}
        row1.add_widget(logo)
        title = Label(text="Book Writer", font_name=font_file(bold=True),
                      font_size=15, color=(1, 1, 1, 1),
                      size_hint=(None, 1.0), width=140)
        row1.add_widget(title)

        self._server = _BarField("server - e.g. unix:/tmp/book_writer.sock or http://localhost:8000")
        row1.add_widget(self._server)

        self._connect_btn = FlatButton(
            text="Connect", icon=icon_path("connect", 48), size=(124, 36),
            bg_color=rgba("#2563eb"), fg_color=(1, 1, 1, 1),
            on_release=lambda *_a: self._app.toggle_connection())
        row1.add_widget(self._connect_btn)
        self.add_widget(row1)

        # -- row 2: API key (aligned directly below server address) ---------
        row2 = BoxLayout(orientation="horizontal", spacing=8,
                         size_hint=(1.0, None), height=36)
        # Left label matching width of logo (32) + spacing (8) + title (140) = 180
        api_lbl = Label(text="API Key:", font_name=font_file(bold=True),
                        font_size=13, color=(1, 1, 1, 1),
                        size_hint=(None, 1.0), width=180,
                        halign="right", valign="middle")
        api_lbl.bind(size=lambda lbl, *_a: setattr(lbl, "text_size", (lbl.width, lbl.height)))
        row2.add_widget(api_lbl)

        self._api_key = _BarField("API key (optional, in-memory) - e.g. sk-...", password=True)
        self._api_key.bind(text=lambda _inp, val: self._app.on_api_key_changed(val))
        row2.add_widget(self._api_key)

        self._toggle_key_btn = FlatButton(
            text="Show", size=(124, 36),
            bg_color=rgba("#374151"), fg_color=(1, 1, 1, 1),
            on_release=lambda *_a: self._toggle_key_visibility())
        row2.add_widget(self._toggle_key_btn)
        self.add_widget(row2)

        # -- row 3: model + theme/settings + status -------------------------
        row3 = BoxLayout(orientation="horizontal", spacing=8,
                         size_hint=(1.0, None), height=36)
        self._model = ResponsiveSpinner(
            text="model", values=(), size_hint=(None, 1.0), width=260,
            font_name=font_file(), font_size=13, color=(1, 1, 1, 1),
            background_color=rgba(SLATE_INPUT), background_normal="",
        )
        self._model.bind(text=lambda _sp, value: self._on_model_text(value))
        row3.add_widget(self._model)

        theme_btn = FlatButton(text="Theme", size=(92, 34),
                               bg_color=rgba("#374151"), fg_color=(1, 1, 1, 1),
                               on_release=lambda *_a: self._app.toggle_theme())
        row3.add_widget(theme_btn)
        settings_btn = FlatButton(text="Settings", size=(112, 34),
                                  bg_color=rgba("#374151"), fg_color=(1, 1, 1, 1),
                                  on_release=lambda *_a: self._app.open_settings())
        row3.add_widget(settings_btn)

        row3.add_widget(BoxLayout())  # flexible spacer

        self._status = Label(text="disconnected", font_name=font_file(bold=True),
                             font_size=12, color=rgba("#f87171"),
                             size_hint=(None, 1.0), width=190,
                             halign="right", valign="middle")
        self._status.bind(size=lambda lbl, *_a: setattr(lbl, "text_size",
                                                        (lbl.width, lbl.height)))
        row3.add_widget(self._status)
        self.add_widget(row3)

        self.set_server_address(app.cfg.server_address)

    # -- events ------------------------------------------------------------
    def _toggle_key_visibility(self) -> None:
        self._api_key.password = not self._api_key.password
        self._toggle_key_btn.text = "Hide" if not self._api_key.password else "Show"

    def _on_model_text(self, value: str) -> None:
        if value and value != "model":
            self._app.on_model_selected(value)

    # -- updates -----------------------------------------------------------
    def get_api_key(self) -> str:
        return self._api_key.text.strip()

    def set_api_key(self, key: str) -> None:
        self._api_key.text = key

    def set_server_address(self, addr: str) -> None:
        self._server.text = addr

    def get_server_address(self) -> str:
        return self._server.text.strip()

    def update_models(self, models: list[str]) -> None:
        if models:
            self._model.values = list(models)
            if self._model.text == "model" or self._model.text not in models:
                self._model.text = models[0]
        else:
            self._model.values = []
            self._model.text = "model"

    def set_model(self, model: str) -> None:
        if model:
            self._model.text = model

    def get_model(self) -> str:
        return self._model.text

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        if connected:
            self._connect_btn.set_label("Disconnect")
            self._connect_btn.bg_color = rgba("#059669")
            self._status.text = "connected"
            self._status.color = rgba("#34d399")
        else:
            self._connect_btn.set_label("Connect")
            self._connect_btn.bg_color = rgba("#2563eb")
            self._status.text = "disconnected"
            self._status.color = rgba("#f87171")

    def set_status(self, text: str) -> None:
        self._status.text = text

    def set_busy(self, busy: bool) -> None:
        self._connect_btn.disabled = busy
