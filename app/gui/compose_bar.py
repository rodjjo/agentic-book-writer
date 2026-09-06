"""The bottom instruction area: multi-line input, image attachment, preview and send.

Enter sends the instruction, Shift+Enter inserts a newline.  An attached image is shown
as a small preview with the file name, a remove (✕) button, and clicking the preview
opens a larger modal view.
"""

from __future__ import annotations

from pathlib import Path

from kivy.graphics import Color, Rectangle
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image as KivyImage
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput

from ..images import image_to_texture, load_image, make_thumbnail
from .colors import rgba
from .dialogs import FilePickerDialog
from .widgets import FlatButton, font_file, icon_path, make_label, note_label

IMAGE_FILTERS = ["*.png", "*.jpg", "*.jpeg", "*.gif", "*.bmp", "*.webp"]
THUMB = (72, 54)


class InstructionInput(TextInput):
    """Multiline input where Enter (without Shift) means “send”."""

    def __init__(self, send_callback=None, **kwargs):
        super().__init__(**kwargs)
        self.send_callback = send_callback

    def keyboard_on_key_down(self, window, keycode, text, modifiers):
        key, _scancode = keycode
        if key in (13, 271) and "shift" not in modifiers:
            if self.send_callback is not None:
                self.send_callback()
            return True
        return super().keyboard_on_key_down(window, keycode, text, modifiers)


class ClickableThumb(ButtonBehavior, BoxLayout):
    """Small clickable image preview (opens the image at full size)."""

    def __init__(self, image_path: str, theme, on_click=None, **kwargs):
        super().__init__(orientation="horizontal", spacing=6, padding=(4, 2), **kwargs)
        self.size_hint = (None, None)
        self.size = (THUMB[0] + 26, THUMB[1] + 8)
        self._path = image_path
        self._on_click = on_click
        try:
            pil = load_image(image_path)
            thumb = make_thumbnail(pil, THUMB)
            self._tex = image_to_texture(thumb)
        except Exception:
            self._tex = None
        with self.canvas.before:
            Color(*rgba("#000000", 0.08))
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=lambda *_a: setattr(self._bg, "pos", self.pos),
                  size=lambda *_a: setattr(self._bg, "size", self.size))
        img = KivyImage(texture=self._tex, size_hint=(None, None),
                        size=THUMB if self._tex else (THUMB[0], 1))
        self.add_widget(img)
        self.bind(on_release=lambda *_a: self._on_click() if self._on_click else None)

    def set_colors(self, *_a):
        pass


class ComposeBar(BoxLayout):
    """Bottom bar: instruction TextInput + attach/preview toolbar + send."""

    def __init__(self, app, theme, **kwargs):
        super().__init__(orientation="vertical", spacing=4, size_hint=(1.0, None),
                         height=156, **kwargs)
        self._app = app
        self.theme = theme
        self._image_path: str | None = None
        self._thumb: ClickableThumb | None = None

        with self.canvas.before:
            self._bar_color = Color(*rgba(theme.panel))
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=lambda *_a: setattr(self._bg, "pos", self.pos),
                  size=lambda *_a: setattr(self._bg, "size", self.size))

        # -- row 1: input + send column ------------------------------------
        row1 = BoxLayout(orientation="horizontal", spacing=8, size_hint=(1.0, None),
                         height=74)
        self._input = InstructionInput(
            send_callback=lambda: self._app.send_now(),
            multiline=True,
            size_hint=(1.0, 1.0),
            font_name=font_file(), font_size=14,
            background_color=rgba(theme.input_bg), foreground_color=rgba(theme.fg),
            hint_text_color=rgba(theme.muted), cursor_color=rgba(theme.accent),
            hint_text="Describe what the assistant should write or change... (Enter sends)",
            padding=(10, 8),
        )
        row1.add_widget(self._input)

        right = BoxLayout(orientation="vertical", spacing=2, size_hint=(None, 1.0),
                          width=112)
        self._send_btn = FlatButton(text="Send", icon=icon_path("send", 48),
                                    size=(108, 44), bg_color=rgba(theme.accent),
                                    fg_color=rgba(theme.accent_fg),
                                    on_release=lambda *_a: self._app.send_now())
        right.add_widget(self._send_btn)
        self._state_lbl = note_label("", color=rgba(theme.accent), font_size=11,
                                     halign="center", size_hint=(1.0, None),
                                     auto_size=False)
        self._state_lbl.height = 16
        right.add_widget(self._state_lbl)
        row1.add_widget(right)
        self.add_widget(row1)

        # -- row 2: attach / preview / hint --------------------------------
        row2 = BoxLayout(orientation="horizontal", spacing=8, size_hint=(1.0, None),
                         height=52)
        attach = FlatButton(text="Attach image", icon=icon_path("upload", 48),
                            size=(150, 40), bg_color=rgba(theme.panel_2),
                            fg_color=rgba(theme.fg),
                            on_release=lambda *_a: self._pick_image())
        row2.add_widget(attach)

        self._preview_box = BoxLayout(orientation="horizontal", spacing=6,
                                      size_hint=(1.0, None), height=52)
        row2.add_widget(self._preview_box)

        hint = note_label("Enter to send | Shift+Enter for newline", color=rgba(theme.muted),
                          size_hint=(None, 1.0))
        row2.add_widget(hint)
        self.add_widget(row2)

        self._apply_theme(theme)

    # -- styling -----------------------------------------------------------
    def _apply_theme(self, theme) -> None:
        self.theme = theme
        if getattr(self, "_bar_color", None) is not None:
            self._bar_color.rgba = rgba(theme.panel)
        self._input.background_color = rgba(theme.input_bg)
        self._input.foreground_color = rgba(theme.fg)
        self._input.cursor_color = rgba(theme.accent)
        self._input.hint_text_color = rgba(theme.muted)
        self._send_btn.bg_color = rgba(theme.accent)
        self._send_btn.fg_color = rgba(theme.accent_fg)

    # -- image handling ----------------------------------------------------
    def _pick_image(self) -> None:
        dlg = FilePickerDialog(self.theme, "Attach an image",
                               lambda p: self._set_image(p),
                               filters=IMAGE_FILTERS)
        dlg.open()

    def _set_image(self, path: str | None) -> None:
        if path:
            self.set_image(path)

    def set_image(self, path: str) -> None:
        self._image_path = path
        self._rebuild_preview()

    def clear_image(self) -> None:
        self._image_path = None
        self._rebuild_preview()

    def _rebuild_preview(self) -> None:
        self._preview_box.clear_widgets()
        self._thumb = None
        if not self._image_path:
            return
        name = Path(self._image_path).name
        self._thumb = ClickableThumb(
            self._image_path, self.theme,
            on_click=lambda: self._app.show_image(self._image_path))
        self._preview_box.add_widget(self._thumb)
        label = make_label(name, color=rgba(self.theme.fg), font_size=11)
        label.text_size = (180, None)
        label.size = (190, 24)
        label.shorten = True
        self._preview_box.add_widget(label)
        remove = FlatButton(icon=icon_path("close", 48), size=(34, 34),
                            bg_color=rgba(self.theme.error_bubble),
                            fg_color=rgba(self.theme.error_fg),
                            on_release=lambda *_a: self.clear_image())
        self._preview_box.add_widget(remove)

    # -- busy / state ------------------------------------------------------
    def set_busy(self, busy: bool) -> None:
        self._send_btn.disabled = busy
        self._state_lbl.text = "Working..." if busy else ""
        self._input.readonly = busy

    def set_state(self, text: str) -> None:
        self._state_lbl.text = text

    # -- accessors ---------------------------------------------------------
    def get_text(self) -> str:
        return self._input.text.strip()

    def set_text(self, text: str) -> None:
        self._input.text = text or ""

    def clear_text(self) -> None:
        self._input.text = ""

    def focus_input(self) -> None:
        self._input.focus = True

    def image_path(self) -> str | None:
        return self._image_path

    def has_image(self) -> bool:
        return self._image_path is not None

    def has_content(self) -> bool:
        return bool(self.get_text().strip()) or self._image_path is not None
