"""The Books tab: a read-only, scrollable rendering of every book's pages.

The user can *browse* books and pages but can never write them here — only the model
(authoring through tool calls handled by the client) creates/edits/removes content.
Each page's Markdown is rendered with :mod:`app.markdown_render` into a crisp raster
image, uploaded as a texture and shown in a Kivy ``ScrollView``.  Pages are re-rendered
when the panel is resized so they always fit the available width.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from kivy.clock import Clock
from kivy.graphics import Color, Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image as KivyImage
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner

from .. import markdown_render
from ..images import image_to_texture
from .colors import rgba
from .dialogs import FilePickerDialog, InfoDialog
from .widgets import (FlatButton, font_file, make_label, note_label)

PAGE_TEXT_WIDTH = 760     # markdown text column width used for rendering
CAPTION_H = 22


class _PageEntry(BoxLayout):
    """One rendered page: a caption centred above a centred page image."""

    def __init__(self, order: int, title: str, texture, img_w: int, img_h: int,
                 *, theme, **kwargs):
        super().__init__(orientation="vertical", size_hint_y=None,
                         spacing=4, **kwargs)
        self.theme = theme
        self.size_hint_x = 1.0
        self.height = CAPTION_H + 4 + img_h

        self._caption = Label(
            text=f"{order}. {title}", font_name=font_file(), font_size=12,
            color=rgba(theme.muted), halign="center", valign="middle",
            size_hint=(1.0, None), height=CAPTION_H, shorten=False)
        self._caption.bind(size=lambda lbl, *_a: setattr(lbl, "text_size",
                                                         (lbl.width, lbl.height)))
        self.add_widget(self._caption)

        self._image = KivyImage(texture=texture, size_hint=(None, None),
                                size=(img_w, img_h))
        row = BoxLayout(orientation="horizontal", size_hint=(1.0, None),
                        height=img_h)
        row.add_widget(BoxLayout())      # equal left spacer
        row.add_widget(self._image)
        row.add_widget(BoxLayout())      # equal right spacer
        self.add_widget(row)


class BooksPanel(BoxLayout):
    """Toolbar + scrollable page renderer for the selected book."""

    def __init__(self, theme, book_store, *, on_book_selected=None,
                 on_change_root=None, **kwargs):
        super().__init__(orientation="vertical", **kwargs)
        self.theme = theme
        self._store = book_store
        self._on_book_selected = on_book_selected
        self._on_change_root = on_change_root
        self.zoom = 1.0
        self._refit_scheduled = False

        with self.canvas.before:
            Color(*rgba(theme.panel))
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=lambda *_a: setattr(self._bg, "pos", self.pos),
                  size=lambda *_a: setattr(self._bg, "size", self.size))

        self._build_toolbar()
        self._build_reader()

    # -- toolbar -----------------------------------------------------------
    def _build_toolbar(self) -> None:
        bar = BoxLayout(orientation="horizontal", spacing=6,
                        size_hint=(1.0, None), height=46, padding=(8, 6))
        bar.add_widget(make_label("Books", bold=True, font_size=15,
                                  color=rgba(self.theme.fg), size_hint=(None, 1.0)))
        note = note_label("read-only - the assistant authors pages", size_hint=(None, 1.0))
        bar.add_widget(note)

        self._root_btn = FlatButton(text="Folder...", size=(92, 34),
                                    bg_color=rgba(self.theme.panel_2),
                                    fg_color=rgba(self.theme.fg),
                                    on_release=lambda *_a: self._pick_folder())
        bar.add_widget(self._root_btn)

        self._book_spinner = Spinner(
            text="no books", values=(), size_hint=(None, 1.0), width=220,
            font_name=font_file(), font_size=13, color=rgba(self.theme.fg),
            background_color=rgba(self.theme.input_bg), background_normal="",
        )
        self._book_spinner.bind(text=lambda _sp, value: self._on_book_text(value))
        bar.add_widget(self._book_spinner)

        refresh = FlatButton(text="Refresh", size=(90, 34),
                             bg_color=rgba(self.theme.panel_2),
                             fg_color=rgba(self.theme.fg),
                             on_release=lambda *_a: self.refresh())
        bar.add_widget(refresh)

        bar.add_widget(BoxLayout())  # spacer

        minus = FlatButton(text="-", size=(34, 34),
                           bg_color=rgba(self.theme.panel_2),
                           fg_color=rgba(self.theme.fg),
                           on_release=lambda *_a: self._set_zoom(self.zoom - 0.1))
        self._zoom_lbl = make_label("100%", font_size=12, color=rgba(self.theme.muted),
                                    size_hint=(None, 1.0), width=46, halign="center")
        plus = FlatButton(text="+", size=(34, 34),
                          bg_color=rgba(self.theme.panel_2),
                          fg_color=rgba(self.theme.fg),
                          on_release=lambda *_a: self._set_zoom(self.zoom + 0.1))
        export = FlatButton(text="Export .md", size=(104, 34),
                            bg_color=rgba(self.theme.panel_2),
                            fg_color=rgba(self.theme.fg),
                            on_release=lambda *_a: self._export())
        bar.add_widget(minus)
        bar.add_widget(self._zoom_lbl)
        bar.add_widget(plus)
        bar.add_widget(export)
        self.add_widget(bar)

    def _set_zoom(self, value: float) -> None:
        self.zoom = max(0.4, min(2.5, value))
        self._zoom_lbl.text = f"{int(self.zoom * 100)}%"
        self.refresh()

    # -- reader ------------------------------------------------------------
    def _build_reader(self) -> None:
        self._scroller = ScrollView(do_scroll_x=False, do_scroll_y=True,
                                    bar_width=10,
                                    bar_color=rgba(self.theme.scrollbar, 0.6),
                                    scroll_type=["bars", "content"])
        self._pages_box = BoxLayout(orientation="vertical",
                                    size_hint_x=1.0, padding=(14, 12))
        self._pages_box.bind(minimum_height=self._pages_box.setter("height"))
        self._scroller.bind(size=lambda *_a: self._request_refit())
        self._scroller.add_widget(self._pages_box)
        self.add_widget(self._scroller)

    def _request_refit(self) -> None:
        """Debounced re-render when the reader is resized (responsive fit)."""
        if self._refit_scheduled:
            return
        self._refit_scheduled = True
        Clock.schedule_once(self._do_refit, 0.08)

    def _do_refit(self, _dt=None) -> None:
        self._refit_scheduled = False
        if self._scroller.width > 80 and self.current_book_name():
            self._show_current()

    # -- data --------------------------------------------------------------
    def set_theme(self, theme) -> None:
        self.theme = theme
        self.refresh()

    def set_store(self, store) -> None:
        self._store = store
        self.refresh()

    def refresh(self) -> None:
        try:
            books = self._store.list_books()
        except Exception:
            books = []
        names = [b.name for b in books]
        self._book_spinner.values = names
        current = self._book_spinner.text
        if not current or current not in names:
            self._book_spinner.text = names[0] if names else "no books"
        self._show_current()

    def select_book(self, name: str) -> None:
        if name and name in self._book_spinner.values:
            self._book_spinner.text = name
        self._show_current()

    def current_book_name(self) -> Optional[str]:
        name = self._book_spinner.text
        return name if name and name != "no books" else None

    def _on_book_text(self, value: str) -> None:
        if value and value != "no books":
            if self._on_book_selected is not None:
                self._on_book_selected(value)
        self._show_current()

    # -- rendering ---------------------------------------------------------
    def _show_current(self) -> None:
        self._pages_box.clear_widgets()
        name = self.current_book_name()
        if name is None:
            self._place_hint("No books yet. Ask the assistant to create one, for example: "
                             '"Create a book called The Wandering Star with 4 pages."')
            return
        book = self._store.get_book(name)
        if book is None:
            self._place_hint(f"No book named '{name}' was found on disk.")
            return
        pages = self._store.list_pages(name)
        if not pages:
            self._place_hint(f"Book '{name}' has no pages yet - ask the assistant to write "
                             "some on the Conversation tab.")
            return

        for page in pages:
            content = self._store.read_page(name, page.name) or ""
            try:
                img = markdown_render.render_markdown(
                    content, text_width=PAGE_TEXT_WIDTH,
                    image_dir=self._store.pages_dir(name))
            except Exception:
                img = markdown_render.render_markdown("# (page could not be rendered)")

            # display size: zoom, but never wider than the reader (responsive)
            avail = max(220.0, self._scroller.width - 44)
            scale = min(self.zoom, avail / max(1.0, float(img.width)))
            if scale != 1.0:
                img = img.resize((max(1, int(img.width * scale)),
                                  max(1, int(img.height * scale))))
            texture = image_to_texture(img)
            entry = _PageEntry(page.order, page.name, texture,
                               img.width, img.height, theme=self.theme)
            self._pages_box.add_widget(entry)

    def _place_hint(self, text: str) -> None:
        hint = make_label(text, font_size=14, color=rgba(self.theme.muted),
                          auto_size=False, valign="top", halign="left")
        hint.text_size = (max(200.0, self._pages_box.width - 60), None)
        hint.bind(texture_size=lambda _l, ts: setattr(hint, "height", ts[1] + 60))
        hint.padding = (20, 30)
        self._pages_box.add_widget(hint)

    # -- folder / export ---------------------------------------------------
    def _pick_folder(self) -> None:
        dlg = FilePickerDialog(self.theme, "Choose the books folder",
                               lambda path: self._apply_root(path), directory=True)
        dlg.open()

    def _apply_root(self, path: Optional[str]) -> None:
        if path and self._on_change_root is not None:
            self._on_change_root(path)

    def _export(self) -> None:
        name = self.current_book_name()
        if name is None:
            InfoDialog(self.theme, "Export", "Pick a book first.").open()
            return
        dlg = FilePickerDialog(
            self.theme, "Export book as one Markdown file",
            lambda chosen: self._do_export(name, chosen),
            directory=False, filters=["*.md"], start_dir=str(self._store.root),
            ok_text="Save here",
        )
        dlg.open()

    def _do_export(self, book_name: str, target: Optional[str]) -> None:
        if not target:
            return
        if not target.lower().endswith(".md"):
            target += ".md"
        parts = []
        for page in self._store.list_pages(book_name):
            content = self._store.read_page(book_name, page.name) or ""
            parts.append(content)
        Path(target).write_text("\n\n".join(parts), encoding="utf-8")
        InfoDialog(self.theme, "Export complete",
                   f"Saved {len(parts)} page(s) to:\n{target}").open()
