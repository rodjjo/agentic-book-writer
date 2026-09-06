"""Shared Kivy building blocks: fonts, icon paths, flat buttons and small helpers."""

from __future__ import annotations

import os
from pathlib import Path

from kivy.graphics import Color, RoundedRectangle
from kivy.properties import ListProperty, NumericProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image as KivyImage
from kivy.uix.label import Label

from .colors import darken, rgba

# -- fonts -------------------------------------------------------------------

_DEJAVU_DIR = Path("/usr/share/fonts/truetype/dejavu")


def font_file(bold: bool = False, italic: bool = False) -> str:
    """Absolute path to the UI font (DejaVu); falls back to Kivy's bundled font."""
    style = ""
    if bold and italic:
        style = "-BoldOblique"
    elif bold:
        style = "-Bold"
    elif italic:
        style = "-Oblique"
    path = _DEJAVU_DIR / f"DejaVuSans{style}.ttf"
    if path.exists():
        return str(path)
    return "Roboto"


def font_path() -> str:
    return font_file()


def font_bold_path() -> str:
    return font_file(bold=True)


def icon_path(name: str, size: int = 48) -> str:
    """Resolve an icon shipped inside the package at ``app/icons/<name>-<size>.png``."""
    here = Path(__file__).resolve().parent.parent / "icons"
    return str(here / f"{name}-{size}.png")


def app_icon_path() -> str:
    """Path to the brand icon used for the window/logo (``app/icons/book-writer.png``)."""
    here = Path(__file__).resolve().parent.parent / "icons"
    path = here / "book-writer.png"
    if path.exists():
        return str(path)
    return icon_path("book-icon", 48)


def icon_exists(name: str, size: int = 48) -> bool:
    return os.path.exists(icon_path(name, size))


# -- labels ------------------------------------------------------------------

def make_label(text: str = "", *, color=(0.1, 0.1, 0.12, 1), font_size=13,
               bold=False, italic=False, halign="left", valign="middle",
               auto_size=True, **kw) -> Label:
    """A themed :class:`~kivy.uix.label.Label`.

    ``auto_size`` labels bind their size to the texture (natural single-line size);
    non-auto labels default to filling their parent's width. Callers can still pass an
    explicit ``size_hint``/``text_size``.
    """
    kw.setdefault("size_hint", (None, None) if auto_size else (1.0, None))
    label = Label(
        text=text,
        font_name=font_file(bold=bold, italic=italic),
        font_size=font_size,
        color=color,
        halign=halign,
        valign=valign,
        **kw,
    )
    if auto_size:
        label.bind(texture_size=label.setter("size"))
    return label


def note_label(text: str, *, color=(0.54, 0.54, 0.58, 1), font_size=11, **kw) -> Label:
    """Small muted caption label (auto-sized)."""
    return make_label(text, color=color, font_size=font_size, **kw)


# -- buttons -----------------------------------------------------------------

class FlatButton(ButtonBehavior, BoxLayout):
    """Flat, themeable push button: rounded background + optional icon + text.

    Example::

        FlatButton(text="Send", icon=icon_path("send"), size=(110, 42),
                   bg_color=rgba("#2563eb"), fg_color=(1, 1, 1, 1),
                   on_release=lambda *_a: app.send_now())
    """

    bg_color = ListProperty([0.13, 0.39, 0.93, 1.0])
    fg_color = ListProperty([1.0, 1.0, 1.0, 1.0])
    radius = NumericProperty(7)

    def __init__(self, text: str = "", icon: str | None = None, *,
                 on_release=None, size=None, bg_color=None, fg_color=None,
                 icon_size=(16, 16), spacing=6, padding=(12, 4), font_size=13,
                 **kwargs):
        kwargs.setdefault("orientation", "horizontal")
        # Buttons carry their own height; default to vertically centred inside
        # horizontal rows (BoxLayout aligns fixed-height children to the bottom
        # otherwise). pos_hint is a no-op in vertical BoxLayouts / plain parents.
        kwargs.setdefault("pos_hint", {"center_y": 0.5})
        super().__init__(**kwargs)
        self.size_hint = (None, None)
        if size is not None:
            self.size = size
        self.spacing = spacing
        self.padding = padding
        if bg_color is not None:
            self.bg_color = bg_color
        if fg_color is not None:
            self.fg_color = fg_color

        if icon and os.path.exists(icon):
            img = KivyImage(source=icon, size_hint=(None, None), size=icon_size)
            self.add_widget(img)
        self._label = None
        if text:
            self._label = Label(text=text, font_name=font_file(),
                                font_size=font_size, color=self.fg_color,
                                size_hint=(1.0, 1.0), halign="center",
                                valign="middle", shorten=True)
            self._label.bind(size=lambda lbl, *_a: self._bound_text(lbl))
            self._bound_text(self._label)
            self.add_widget(self._label)

        if on_release is not None:
            self.bind(on_release=on_release)
        self.bind(fg_color=self._schedule_paint, bg_color=self._schedule_paint,
                  state=self._schedule_paint, disabled=self._schedule_paint)

        with self.canvas.before:
            self._bg = Color(*self._current_bg())
            self._rect = RoundedRectangle(pos=self.pos, size=self.size,
                                          radius=[self.radius, self.radius,
                                                  self.radius, self.radius])
        self.bind(pos=self._redraw, size=self._redraw)

    # -- internal -----------------------------------------------------------
    @staticmethod
    def _bound_text(label) -> None:
        label.text_size = (label.width, None)
        label.halign = "center"

    def _current_bg(self) -> list:
        bg = list(self.bg_color)
        if self.disabled:
            bg[3] *= 0.35
        elif self.state == "down":
            bg = list(darken(tuple(bg), 0.18))
        return bg

    def _schedule_paint(self, *_args) -> None:
        if getattr(self, "_bg", None) is not None:
            self._bg.rgba = self._current_bg()
            if self._label is not None:
                self._label.color = self.fg_color if not self.disabled else (1, 1, 1, 0.6)

    def _redraw(self, *_args) -> None:
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._rect.radius = [self.radius, self.radius, self.radius, self.radius]
        self._schedule_paint()

    def on_touch_down(self, touch):
        if self.disabled:
            return False
        return super().on_touch_down(touch)

    # -- public -------------------------------------------------------------
    def set_label(self, text: str) -> None:
        if self._label is not None:
            self._label.text = text

    def set_colors(self, bg, fg) -> None:
        self.bg_color = bg
        self.fg_color = fg
