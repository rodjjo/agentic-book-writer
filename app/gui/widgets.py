"""Shared Kivy building blocks: fonts, icon paths, flat buttons and small helpers."""

from __future__ import annotations

import os
from pathlib import Path

from kivy.graphics import Color, RoundedRectangle
from kivy.properties import ListProperty, NumericProperty, StringProperty


def measure_text_width(text: str, font_name: str | None = None, font_size: int = 13) -> int:
    """Measure the pixel width of text accurately without requiring an active OpenGL window."""
    if not text:
        return 0
    try:
        from kivy.core.text import Label as CoreLabel
        fn = font_name or font_file()
        c = CoreLabel(font_name=fn, font_size=font_size)
        w, _ = c.get_extents(text)
        return int(w)
    except Exception:
        return int(len(text) * font_size * 0.65)


# -- fonts -------------------------------------------------------------------
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.dropdown import DropDown
from kivy.uix.image import Image as KivyImage
from kivy.uix.label import Label
from kivy.uix.spinner import Spinner, SpinnerOption

from .colors import darken, rgba

# -- fonts -------------------------------------------------------------------

_BUNDLED_FONT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "non_py", "fonts")
)
_DEJAVU_DIR = Path(_BUNDLED_FONT_DIR) if os.path.exists(_BUNDLED_FONT_DIR) else Path("/usr/share/fonts/truetype/dejavu")


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

    text = StringProperty("")
    bg_color = ListProperty([0.13, 0.39, 0.93, 1.0])
    fg_color = ListProperty([1.0, 1.0, 1.0, 1.0])
    radius = NumericProperty(7)

    def __init__(self, text: str = "", icon: str | None = None, *,
                 on_release=None, size=None, bg_color=None, fg_color=None,
                 icon_size=(16, 16), spacing=6, padding=(12, 4), font_size=13,
                 auto_width: bool = True, shorten: bool = False,
                 **kwargs):
        kwargs.setdefault("orientation", "horizontal")
        # Buttons carry their own height; default to vertically centred inside
        # horizontal rows (BoxLayout aligns fixed-height children to the bottom
        # otherwise). pos_hint is a no-op in vertical BoxLayouts / plain parents.
        kwargs.setdefault("pos_hint", {"center_y": 0.5})
        super().__init__(**kwargs)
        self.spacing = spacing
        self.padding = padding
        self._icon_path = icon
        self._icon_size = icon_size
        self._font_size = font_size
        self._auto_width = auto_width
        self._shorten = shorten
        self._min_width = size[0] if size is not None else 0
        self._fixed_height = size[1] if size is not None else 32
        self._has_icon = bool(icon and os.path.exists(icon))

        self.size_hint = kwargs.pop("size_hint", (None, None))
        if bg_color is not None:
            self.bg_color = bg_color
        if fg_color is not None:
            self.fg_color = fg_color

        if self._has_icon:
            img = KivyImage(source=icon, size_hint=(None, None), size=icon_size)
            self.add_widget(img)

        self._label = None
        if text:
            self._label = Label(text=text, font_name=font_file(),
                                font_size=font_size, color=self.fg_color,
                                size_hint=(1.0, 1.0), halign="center",
                                valign="middle", shorten=shorten)
            self._label.bind(size=lambda lbl, *_a: self._bound_text(lbl))
            self._bound_text(self._label)
            self.add_widget(self._label)

        self.text = text
        self._update_dimensions(text)

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

    def on_text(self, _instance, value: str) -> None:
        if self._label is not None:
            self._label.text = value
        elif value:
            self._label = Label(text=value, font_name=font_file(),
                                font_size=self._font_size, color=self.fg_color,
                                size_hint=(1.0, 1.0), halign="center",
                                valign="middle", shorten=self._shorten)
            self._label.bind(size=lambda lbl, *_a: self._bound_text(lbl))
            self._bound_text(self._label)
            self.add_widget(self._label)
        if getattr(self, "_auto_width", True):
            self._update_dimensions(value)

    def _calc_content_width(self, text: str) -> float:
        pad_h = (self.padding[0] + self.padding[2]) if len(self.padding) >= 4 else (self.padding[0] * 2 if self.padding else 24)
        icon_w = (self._icon_size[0] + self.spacing) if getattr(self, "_has_icon", False) else 0
        text_w = measure_text_width(text, font_size=getattr(self, "_font_size", 13)) if text else 0
        return float(pad_h + icon_w + text_w + (6 if text else 0))

    def _update_dimensions(self, text: str) -> None:
        if self.size_hint[0] is None:
            content_w = self._calc_content_width(text)
            final_w = max(getattr(self, "_min_width", 0), content_w) if getattr(self, "_auto_width", True) else (getattr(self, "_min_width", 0) or content_w)
            self.width = final_w
        if self.size_hint[1] is None:
            self.height = getattr(self, "_fixed_height", 32)

    # -- internal -----------------------------------------------------------
    @staticmethod
    def _bound_text(label) -> None:
        label.text_size = (label.width, label.height)
        label.halign = "center"
        label.valign = "middle"

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
        self.text = text

    def set_colors(self, bg, fg) -> None:
        self.bg_color = bg
        self.fg_color = fg


# -- responsive spinner ------------------------------------------------------

class ResponsiveDropDown(DropDown):
    """Dropdown list that does not force-clamp its width to the parent spinner."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.auto_width = False


class LeftAlignedSpinnerOption(SpinnerOption):
    """Dropdown option with left-aligned text, themed colors, and proper padding."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.font_name = font_file()
        self.halign = "left"
        self.valign = "middle"
        self.shorten = True
        self.shorten_from = "right"
        self.padding = (12, 4)
        self.bind(size=self._update_text_bounds)
        self.bind(text=self._update_text_bounds)
        self._update_text_bounds()

    def _update_text_bounds(self, *_a):
        self.text_size = (max(10, self.width - 24), self.height)


class ResponsiveSpinner(Spinner):
    """Spinner with left-aligned button text (showing start of name) and an adaptive floating dropdown.

    - Main button: Left-aligned text so the start of book/chapter title is always visible,
      gracefully cropping on the right with ellipsis if needed.
    - Floating dropdown: Options are left-aligned and the dropdown width automatically expands
      to fit the largest item in `values` while respecting the window boundaries.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("dropdown_cls", ResponsiveDropDown)
        kwargs.setdefault("option_cls", LeftAlignedSpinnerOption)
        super().__init__(**kwargs)
        self.halign = "left"
        self.valign = "middle"
        self.shorten = True
        self.shorten_from = "right"
        self.padding = (10, 4)
        self.bind(size=self._update_text_bounds)
        self.bind(text=self._update_text_bounds)
        self._update_text_bounds()

    def _update_text_bounds(self, *_a):
        self.text_size = (max(10, self.width - 20), self.height)

    def _calc_dropdown_width(self) -> float:
        font_name = self.font_name or font_file()
        font_size = self.font_size or 13
        max_text_w = max((measure_text_width(str(v), font_name=font_name, font_size=font_size) for v in self.values), default=0)
        needed_w = max(self.width, max_text_w + 48)
        win = self.get_parent_window()
        if win:
            max_allowed = max(self.width, win.width - 24)
            return float(min(needed_w, max_allowed))
        return float(needed_w)

    def _update_dropdown(self, *largs):
        dp = self._dropdown
        cls = self.option_cls
        values = self.values
        text_autoupdate = self.text_autoupdate
        if isinstance(cls, str):
            from kivy.factory import Factory
            cls = Factory.get(cls)
        dp.clear_widgets()
        for value in values:
            item = cls(text=value)
            item.font_name = self.font_name
            item.font_size = self.font_size
            item.color = self.color
            item.background_color = self.background_color
            item.background_normal = self.background_normal
            item.height = self.height if self.sync_height else item.height
            item.bind(on_release=lambda option: dp.select(option.text))
            dp.add_widget(item)
        if text_autoupdate:
            if values:
                if not self.text or self.text not in values:
                    self.text = values[0]
            else:
                self.text = ""
        if self._dropdown:
            self._dropdown.auto_width = False
            self._dropdown.width = self._calc_dropdown_width()

    def _toggle_dropdown(self, *largs):
        if self.values and self._dropdown:
            self._dropdown.auto_width = False
            self._dropdown.width = self._calc_dropdown_width()
        super()._toggle_dropdown(*largs)

