"""Modal dialog windows used throughout the Kivy application.

Requirement: the main window is the only non-modal top-level; every other window is a
modal :class:`~kivy.uix.popup.Popup`.  Because Kivy is event driven, dialogs never
"block" — each one takes an ``on_done`` callback invoked with its result when the user
closes it (``None`` means cancelled).
"""

from __future__ import annotations

import base64
import io
import os
from pathlib import Path
from typing import Callable, Optional

from PIL import Image as PILImage, ImageDraw

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image as KivyImage
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput

from ..images import image_to_texture, load_image
from .colors import rgba
from .widgets import FlatButton, font_bold_path, font_file, make_label, note_label

OnDone = Callable[[Optional[dict]], None]

# Generated 9-patch (white rounded rectangle) used as the Popup background.
_PANEL_RADIUS = 16
_PANEL_PATCH = 64          # pixel size of the generated source image
_PANEL_SOURCE_CACHE = {}


def _popup_panel_source() -> str:
    """A white rounded-corner 9-patch (as a data URI) for Popup backgrounds.

    Kivy's :class:`~kivy.uix.modalview.ModalView.background_color` only
    *multiplies* its background texture, and the default texture is a dark grey
    — so no amount of tinting can produce a real theme-coloured panel (in light
    mode the dialog body came out dark grey with unreadable dark text).
    Substituting an opaque white rounded rectangle lets ``background_color``
    tint it to exactly the theme's ``bg`` colour.
    """
    cached = _PANEL_SOURCE_CACHE.get((_PANEL_PATCH, _PANEL_RADIUS))
    if cached is not None:
        return cached
    img = PILImage.new("RGBA", (_PANEL_PATCH, _PANEL_PATCH), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle(
        [0, 0, _PANEL_PATCH - 1, _PANEL_PATCH - 1], radius=_PANEL_RADIUS,
        fill=(255, 255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    source = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    _PANEL_SOURCE_CACHE[(_PANEL_PATCH, _PANEL_RADIUS)] = source
    return source


def _mk_popup(width: int, height: int, *, theme, title: str = "") -> Popup:
    popup = Popup(
        title=title,
        title_size=14,
        title_font=font_bold_path(),
        size_hint=(None, None),
        size=(width, height),
        auto_dismiss=False,
        background=_popup_panel_source(),
        border=[_PANEL_RADIUS, _PANEL_RADIUS, _PANEL_RADIUS, _PANEL_RADIUS],
        background_color=rgba(theme.bg),
        # The dialogs draw their own headers; Kivy's always-present (empty) title
        # separator would otherwise float as a stray line under the top margin.
        separator_color=(0, 0, 0, 0),
    )
    return popup


def _button_row(theme) -> tuple[BoxLayout, FlatButton, FlatButton]:
    """Bottom-right aligned OK/Cancel row (OK first); returns (row, ok, cancel)."""
    row = BoxLayout(orientation="horizontal", size_hint=(1.0, None), height=40,
                    spacing=8)
    row.add_widget(BoxLayout())  # spacer
    ok = FlatButton(text="OK", size=(110, 36),
                    bg_color=rgba(theme.accent), fg_color=rgba(theme.accent_fg))
    cancel = FlatButton(text="Cancel", size=(110, 36),
                        bg_color=rgba(theme.panel_2), fg_color=rgba(theme.fg))
    row.add_widget(ok)
    row.add_widget(cancel)
    return row, ok, cancel


def _theme_file_chooser(chooser: FileChooserListView, theme) -> None:
    """Paint a :class:`FileChooserListView`'s file/folder text in the theme colour.

    Kivy renders file-chooser rows from its internal ``FileListEntry`` template,
    whose text Labels default to white. That is unreadable on the light theme
    (white text on near-white rows) even though it looks fine on dark.  Repaint
    each entry's labels to ``theme.fg`` as it is added (and once the list has
    settled) so the text follows the current theme.
    """
    text_color = rgba(theme.fg)

    def _iter_widgets(widget):
        stack = [widget]
        while stack:
            w = stack.pop()
            yield w
            stack.extend(w.children)

    def _paint(widget) -> None:
        if widget is None:
            return
        try:
            for w in _iter_widgets(widget):
                if isinstance(w, Label):
                    w.color = text_color
        except Exception:  # pragma: no cover - cosmetic
            pass

    def _paint_node(_chooser, node, _parent=None) -> None:
        _paint(node)

    # Top-level entries arrive via on_entry_added; folders expanded inline add
    # their children via on_subentry_to_entry. Both need repainting.
    chooser.bind(on_entry_added=_paint_node,
                 on_subentry_to_entry=_paint_node)
    # Entries stream in over several frames; repaint once they have settled in
    # case any were created before the bind above took effect.
    Clock.schedule_once(lambda _dt: _paint(chooser), 0.25)


def _field_input(theme, text: str = "", password: bool = False) -> TextInput:
    return TextInput(
        text=text, multiline=False, password=password,
        font_name=font_file(), font_size=13,
        background_color=rgba(theme.input_bg), foreground_color=rgba(theme.fg),
        hint_text_color=rgba(theme.muted), cursor_color=rgba(theme.accent),
        padding=(10, 8),
    )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class SettingsDialog:
    """Edit connection, model and book settings. Result dict mirrors the form."""

    def __init__(self, theme, initial: dict, on_done: OnDone):
        self._on_done = on_done
        self.settings = dict(initial)
        self.popup = _mk_popup(580, 470, theme=theme)
        body = BoxLayout(orientation="vertical", padding=(20, 16), spacing=12)
        body.add_widget(make_label("Settings", font_size=17, bold=True,
                                   color=rgba(theme.fg)))
        hint = note_label("Connection, model and books-folder configuration for this session.",
                          color=rgba(theme.muted))
        body.add_widget(hint)

        # Form rows: fixed height so the text fields, spinner and labels line up
        # and nothing gets squashed to a fraction of its intended size.
        grid = GridLayout(cols=2, spacing=(16, 10), size_hint=(1.0, None),
                          row_default_height=40, row_force_default=True)
        grid.bind(minimum_height=grid.setter("height"))
        self._fields: dict[str, TextInput] = {}

        def _form_label(text: str) -> Label:
            lbl = make_label(text, color=rgba(theme.fg), font_size=13,
                             auto_size=False)
            lbl.size_hint = (1.0, 1.0)
            lbl.halign = "right"
            lbl.valign = "middle"
            lbl.bind(size=lambda l, *_a: setattr(l, "text_size", (l.width, l.height)))
            return lbl

        def add_row(key: str, label: str, value: str) -> None:
            grid.add_widget(_form_label(label))
            inp = _field_input(theme, value)
            inp.size_hint = (1.0, 1.0)
            self._fields[key] = inp
            grid.add_widget(inp)

        add_row("server_address", "Server address",
                str(self.settings.get("server_address", "")))
        add_row("book_root", "Books folder", str(self.settings.get("book_root", "")))
        add_row("model", "Model", str(self.settings.get("model", "")))
        add_row("connection_timeout", "Timeout (s)",
                str(self.settings.get("connection_timeout", "")))

        # theme selector (same height as the text-field rows above)
        grid.add_widget(_form_label("Theme"))
        self._theme_spinner = Spinner(
            text=str(self.settings.get("theme", "light")),
            values=("light", "dark"), size_hint=(1.0, 1.0),
            font_name=font_file(), font_size=13,
            background_color=rgba(theme.input_bg), color=rgba(theme.fg),
            background_normal="",
        )
        grid.add_widget(self._theme_spinner)
        body.add_widget(grid)

        row, ok, cancel = _button_row(theme)
        ok.bind(on_release=lambda *_a: self._confirm())
        cancel.bind(on_release=lambda *_a: self._close(None))
        body.add_widget(row)
        self.popup.content = body

    # -- api ----------------------------------------------------------------
    def open(self) -> None:
        self.popup.open()

    def _confirm(self) -> None:
        for key, inp in self._fields.items():
            self.settings[key] = inp.text.strip()
        self.settings["theme"] = self._theme_spinner.text
        self._close(self.settings)

    def _close(self, result: Optional[dict]) -> None:
        self.popup.dismiss()
        if self._on_done is not None:
            self._on_done(result)


# ---------------------------------------------------------------------------
# Confirm / info
# ---------------------------------------------------------------------------

class ConfirmDialog:
    def __init__(self, theme, title: str, message: str, on_done: Callable[[bool], None],
                 danger: bool = False):
        self._on_done = on_done
        popup = _mk_popup(460, 220, theme=theme)
        body = BoxLayout(orientation="vertical", padding=(18, 16), spacing=8)
        body.add_widget(make_label(title, font_size=16, bold=True, color=rgba(theme.fg)))
        msg = make_label(message, color=rgba(theme.fg), font_size=13, auto_size=False)
        msg.text_size = (400, None)
        msg.bind(texture_size=lambda _l, ts: setattr(msg, "height", ts[1]))
        body.add_widget(msg)
        row, ok, cancel = _button_row(theme)
        ok.bg_color = rgba("#dc2626") if danger else rgba(theme.accent)
        ok.bind(on_release=lambda *_a: self._done(True))
        cancel.bind(on_release=lambda *_a: self._done(False))
        body.add_widget(row)
        popup.content = body
        self.popup = popup

    def open(self) -> None:
        self.popup.open()

    def _done(self, value: bool) -> None:
        self.popup.dismiss()
        if self._on_done is not None:
            self._on_done(value)


class InfoDialog:
    def __init__(self, theme, title: str, message: str, on_done: Optional[Callable[[], None]] = None):
        self._on_done = on_done
        popup = _mk_popup(480, 240, theme=theme)
        body = BoxLayout(orientation="vertical", padding=(18, 16), spacing=8)
        body.add_widget(make_label(title, font_size=16, bold=True, color=rgba(theme.fg)))
        msg = make_label(message, color=rgba(theme.fg), font_size=13, auto_size=False)
        msg.text_size = (420, None)
        msg.bind(texture_size=lambda _l, ts: setattr(msg, "height", ts[1]))
        body.add_widget(msg)
        # Info dialogs need a single confirmation action (no Cancel).
        row = BoxLayout(orientation="horizontal", size_hint=(1.0, None), height=40)
        row.add_widget(BoxLayout())  # spacer
        ok = FlatButton(text="OK", size=(110, 36),
                        bg_color=rgba(theme.accent), fg_color=rgba(theme.accent_fg))
        ok.bind(on_release=lambda *_a: self._close())
        row.add_widget(ok)
        body.add_widget(row)
        popup.content = body
        self.popup = popup

    def open(self) -> None:
        self.popup.open()

    def _close(self) -> None:
        self.popup.dismiss()
        if self._on_done is not None:
            self._on_done()


# ---------------------------------------------------------------------------
# File chooser (attach an image / pick a folder / pick an export path)
# ---------------------------------------------------------------------------

class FilePickerDialog:
    """Modal file/folder chooser built on Kivy's :class:`FileChooserListView`."""

    def __init__(self, theme, title: str, on_done: Callable[[Optional[str]], None],
                 *, directory: bool = False, filters=None, start_dir: Optional[str] = None,
                 ok_text: str = "Choose"):
        self._on_done = on_done
        popup = _mk_popup(720, 480, theme=theme)
        body = BoxLayout(orientation="vertical", padding=(12, 10), spacing=6)
        head = BoxLayout(orientation="horizontal", size_hint=(1.0, None), height=30)
        head.add_widget(make_label(title, font_size=15, bold=True, color=rgba(theme.fg)))
        body.add_widget(head)

        # A folder picker must list folders only — plain files cannot be a
        # books root, so filter them out (Kivy would otherwise show them).
        if directory:
            filters = [lambda _dirname, filename: os.path.isdir(filename)]
            filter_dirs = True
        else:
            filters = filters if filters is not None else []
            filter_dirs = False

        chooser = FileChooserListView(
            dirselect=directory, filters=filters, filter_dirs=filter_dirs,
            path=str(start_dir or Path.home()), font_name=font_file(),
        )
        _theme_file_chooser(chooser, theme)
        body.add_widget(chooser)

        row = BoxLayout(orientation="horizontal", size_hint=(1.0, None), height=40, spacing=8)
        self._path_lbl = make_label("", color=rgba(theme.muted), font_size=12,
                                    auto_size=False)
        self._path_lbl.size_hint = (1.0, 1.0)
        self._path_lbl.halign = "left"
        self._path_lbl.valign = "middle"
        self._path_lbl.shorten = True
        self._path_lbl.bind(size=lambda l, *_a: setattr(l, "text_size", (l.width, l.height)))
        row.add_widget(self._path_lbl)
        ok = FlatButton(text=ok_text, size=(110, 34),
                        bg_color=rgba(theme.accent), fg_color=rgba(theme.accent_fg))
        ok.disabled = True
        cancel = FlatButton(text="Cancel", size=(100, 34),
                            bg_color=rgba(theme.panel_2), fg_color=rgba(theme.fg))
        row.add_widget(ok)
        row.add_widget(cancel)
        body.add_widget(row)

        def _update(_chooser=None, selection=None, *_a) -> None:
            if selection:
                self._path_lbl.text = str(selection[0])
            ok.disabled = not bool(selection)

        chooser.bind(selection=_update)

        def _choose(_btn) -> None:
            if chooser.selection:
                self._close(str(chooser.selection[0]))

        ok.bind(on_release=_choose)
        cancel.bind(on_release=lambda *_a: self._close(None))
        popup.content = body
        self.popup = popup
        # initialise the ok-button state
        _update(selection=list(chooser.selection))

    def open(self) -> None:
        self.popup.open()

    def _close(self, path: Optional[str]) -> None:
        self.popup.dismiss()
        if self._on_done is not None:
            self._on_done(path)


# ---------------------------------------------------------------------------
# Image preview
# ---------------------------------------------------------------------------

class ImagePreviewDialog:
    def __init__(self, theme, path: str | Path, on_done: Optional[Callable[[], None]] = None):
        self._on_done = on_done
        popup = _mk_popup(760, 600, theme=theme)
        body = BoxLayout(orientation="vertical", padding=(12, 10), spacing=8)
        head = BoxLayout(orientation="horizontal", size_hint=(1.0, None), height=28)
        head.add_widget(make_label(str(Path(path).name), font_size=14, bold=True,
                                   color=rgba(theme.fg)))
        body.add_widget(head)

        try:
            pil = load_image(path)
        except Exception as exc:
            body.add_widget(make_label(f"Could not load image: {exc}",
                                       color=rgba(theme.error_fg)))
        else:
            box_w, box_h = 730, 500
            ratio = min(box_w / pil.width, box_h / pil.height)
            if ratio < 1.0:
                pil = pil.resize((max(1, int(pil.width * ratio)),
                                  max(1, int(pil.height * ratio))), PILImage.LANCZOS)
            img = KivyImage(texture=image_to_texture(pil), size_hint=(1.0, 1.0))
            body.add_widget(img)

        close = FlatButton(text="Close", size=(120, 36), bg_color=rgba(theme.accent),
                           fg_color=rgba(theme.accent_fg))
        close.bind(on_release=lambda *_a: self._close())
        bar = BoxLayout(orientation="horizontal", size_hint=(1.0, None), height=40)
        bar.add_widget(BoxLayout())
        bar.add_widget(close)
        body.add_widget(bar)
        popup.content = body
        self.popup = popup

    def open(self) -> None:
        self.popup.open()

    def _close(self) -> None:
        self.popup.dismiss()
        if self._on_done is not None:
            self._on_done()
