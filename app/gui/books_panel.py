"""The Books tab: a read-only, scrollable rendering of every book's pages.

The user can *browse* books and pages but can never write them here — only the model
(authoring through tool calls handled by the client) creates/edits/removes content.
Each page's Markdown is rendered with :mod:`app.markdown_render` into a crisp raster
image, uploaded as a texture and shown in a Kivy ``ScrollView``. Pages are re-rendered
when the panel is resized so they always fit the available width.
"""

from __future__ import annotations

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

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
from .dialogs import BookInstructionsDialog, ConfirmDialog, FilePickerDialog, GoToPageDialog
from .widgets import FlatButton, ResponsiveSpinner, font_file, make_label, note_label

PAGE_TEXT_WIDTH = 760     # markdown text column width used for rendering
CAPTION_H = 22


@dataclass
class _RenderedPageItem:
    chapter_order: int
    chapter_name: str
    page_index: int       # 0-indexed page within chapter
    page_count: int       # total pages in chapter
    global_index: int     # 0-indexed page within book
    image: Any            # PIL Image


class _PageEntry(BoxLayout):
    """One rendered page: an optional caption above a centred page image."""

    def __init__(self, order: int, title: str, texture, img_w: int, img_h: int,
                 *, theme, show_caption: bool = False, **kwargs):
        super().__init__(orientation="vertical", size_hint=(1.0, None),
                         spacing=4, **kwargs)
        self.theme = theme
        cap_h = CAPTION_H if show_caption else 0
        self.height = cap_h + (4 if show_caption else 0) + img_h

        if show_caption:
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
                 on_change_root=None, on_book_deleted=None,
                 on_instruction_updated=None, **kwargs):
        super().__init__(orientation="vertical", **kwargs)
        self.theme = theme
        self._store = book_store
        self._on_book_selected = on_book_selected
        self._on_change_root = on_change_root
        self._on_book_deleted: Optional[Callable[[str], None]] = on_book_deleted
        self._on_instruction_updated: Optional[Callable[[str, str], None]] = on_instruction_updated
        self.zoom = 1.0
        self._refit_scheduled = False

        self.current_page_idx: int = 0
        self.single_page_mode: bool = True
        self._current_book: Optional[str] = None
        self._cached_book_name: Optional[str] = None
        self._cached_pages: list[_RenderedPageItem] = []
        self._chapter_spinner_updating: bool = False

        with self.canvas.before:
            self._bg_color = Color(*rgba(theme.panel))
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=lambda *_a: setattr(self._bg, "pos", self.pos),
                  size=lambda *_a: setattr(self._bg, "size", self.size))

        self._build_toolbar()
        self._build_reader()

    # -- toolbar -----------------------------------------------------------
    def _build_toolbar(self) -> None:
        self._toolbar = BoxLayout(orientation="vertical", spacing=4,
                                  size_hint=(1.0, None), height=46, padding=(8, 6))

        self._row1 = BoxLayout(orientation="horizontal", spacing=6,
                               size_hint=(1.0, 1.0))
        self._row2 = BoxLayout(orientation="horizontal", spacing=6,
                               size_hint=(1.0, 1.0))

        # --- Book controls group ---
        self._book_controls = BoxLayout(orientation="horizontal", spacing=6,
                                        size_hint=(None, 1.0))
        self._book_controls.bind(minimum_width=self._book_controls.setter("width"))

        self._book_controls.add_widget(make_label("Books", bold=True, font_size=15,
                                                  color=rgba(self.theme.fg), size_hint=(None, 1.0),
                                                  width=52))
        note = note_label("read-only", size_hint=(None, 1.0), width=62)
        self._book_controls.add_widget(note)

        refresh = FlatButton(text="Refresh", size=(76, 34),
                             bg_color=rgba(self.theme.panel_2),
                             fg_color=rgba(self.theme.fg),
                             on_release=lambda *_a: self.refresh())
        self._book_controls.add_widget(refresh)

        self._instruction_btn = FlatButton(
            text="Instructions", size=(106, 34),
            bg_color=rgba(self.theme.panel_2),
            fg_color=rgba(self.theme.fg),
            on_release=lambda *_a: self.open_book_instructions(),
        )
        self._book_controls.add_widget(self._instruction_btn)

        self._delete_btn = FlatButton(
            text="Delete Book", size=(110, 34),
            bg_color=rgba(self.theme.panel_2),
            fg_color=rgba("#dc2626"),
            on_release=lambda *_a: self.prompt_delete_book(),
        )
        self._book_controls.add_widget(self._delete_btn)

        # View mode toggle: Single page vs All pages
        self._mode_btn = FlatButton(
            text="Single Page", size=(106, 34),
            bg_color=rgba(self.theme.accent if self.single_page_mode else self.theme.panel_2),
            fg_color=rgba(self.theme.accent_fg if self.single_page_mode else self.theme.fg),
            on_release=lambda *_a: self.toggle_view_mode(),
        )
        self._book_controls.add_widget(self._mode_btn)

        ch_lbl = make_label("Ch:", color=rgba(self.theme.muted), font_size=12,
                            size_hint=(None, 1.0), width=26)
        self._book_controls.add_widget(ch_lbl)

        self._chapter_spinner = ResponsiveSpinner(
            text="No chapters", values=(), size_hint=(None, None), size=(150, 34),
            font_name=font_file(), font_size=12, color=rgba(self.theme.fg),
            background_color=rgba(self.theme.panel_2), background_normal="",
        )
        self._chapter_spinner.bind(text=lambda _sp, val: self._on_chapter_selected(val))
        self._book_controls.add_widget(self._chapter_spinner)

        # --- Page reading controls group ---
        self._page_controls = BoxLayout(orientation="horizontal", spacing=6,
                                        size_hint=(None, 1.0))
        self._page_controls.bind(minimum_width=self._page_controls.setter("width"))

        self._prev_btn = FlatButton(
            text="< Prev", size=(72, 34),
            bg_color=rgba(self.theme.panel_2),
            fg_color=rgba(self.theme.fg),
            on_release=lambda *_a: self.prev_page(),
        )
        self._page_controls.add_widget(self._prev_btn)

        self._page_lbl = make_label("0 pages", font_size=12, color=rgba(self.theme.fg),
                                    size_hint=(None, 1.0), width=105, halign="center")
        self._page_controls.add_widget(self._page_lbl)

        self._next_btn = FlatButton(
            text="Next >", size=(72, 34),
            bg_color=rgba(self.theme.panel_2),
            fg_color=rgba(self.theme.fg),
            on_release=lambda *_a: self.next_page(),
        )
        self._page_controls.add_widget(self._next_btn)

        self._goto_btn = FlatButton(
            text="Go to page", size=(102, 34),
            bg_color=rgba(self.theme.panel_2),
            fg_color=rgba(self.theme.fg),
            on_release=lambda *_a: self.open_go_to_page(),
        )
        self._page_controls.add_widget(self._goto_btn)

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
        self._page_controls.add_widget(minus)
        self._page_controls.add_widget(self._zoom_lbl)
        self._page_controls.add_widget(plus)

        self._spacer_row1 = BoxLayout()
        self._spacer_row2 = BoxLayout()

        self._is_wide_toolbar = True
        self._row1.add_widget(self._book_controls)
        self._row1.add_widget(self._spacer_row1)
        self._row1.add_widget(self._page_controls)
        self._toolbar.add_widget(self._row1)

        self.add_widget(self._toolbar)
        self.bind(width=lambda *_a: self._update_toolbar_layout())

    def _calc_group_width(self, group: BoxLayout) -> float:
        if not group.children:
            return 0.0
        return sum(c.width for c in group.children) + max(0, len(group.children) - 1) * group.spacing

    def _update_toolbar_layout(self) -> None:
        g1_w = self._calc_group_width(self._book_controls) or 720
        g2_w = self._calc_group_width(self._page_controls) or 480
        needed = g1_w + g2_w + 30
        should_be_wide = self.width >= needed

        if should_be_wide and not self._is_wide_toolbar:
            self._is_wide_toolbar = True
            if self._page_controls.parent:
                self._page_controls.parent.remove_widget(self._page_controls)
            if self._spacer_row1.parent:
                self._spacer_row1.parent.remove_widget(self._spacer_row1)
            if self._book_controls.parent:
                self._book_controls.parent.remove_widget(self._book_controls)
            if self._row2.parent:
                self._toolbar.remove_widget(self._row2)

            self._row1.clear_widgets()
            self._row1.add_widget(self._book_controls)
            self._row1.add_widget(self._spacer_row1)
            self._row1.add_widget(self._page_controls)
            self._toolbar.height = 46
            self._toolbar.spacing = 0

        elif not should_be_wide and self._is_wide_toolbar:
            self._is_wide_toolbar = False
            if self._page_controls.parent:
                self._page_controls.parent.remove_widget(self._page_controls)
            if self._spacer_row1.parent:
                self._spacer_row1.parent.remove_widget(self._spacer_row1)
            if self._book_controls.parent:
                self._book_controls.parent.remove_widget(self._book_controls)

            self._row1.clear_widgets()
            self._row1.add_widget(self._book_controls)
            self._row1.add_widget(self._spacer_row1)

            self._row2.clear_widgets()
            self._row2.add_widget(self._spacer_row2)
            self._row2.add_widget(self._page_controls)

            if self._row2.parent is None:
                self._toolbar.add_widget(self._row2)
            self._toolbar.height = 80
            self._toolbar.spacing = 4

    def _set_zoom(self, value: float) -> None:
        self.zoom = max(0.4, min(2.5, value))
        self._zoom_lbl.text = f"{int(self.zoom * 100)}%"
        self.refresh()

    def toggle_view_mode(self) -> None:
        self.single_page_mode = not self.single_page_mode
        self._mode_btn.text = "Single Page" if self.single_page_mode else "All Pages"
        self._mode_btn.bg_color = rgba(self.theme.accent if self.single_page_mode else self.theme.panel_2)
        self._mode_btn.fg_color = rgba(self.theme.accent_fg if self.single_page_mode else self.theme.fg)
        self._show_current()

    def prev_page(self) -> None:
        if self.current_page_idx > 0:
            self.current_page_idx -= 1
            self._show_current()

    def next_page(self) -> None:
        pages = self._get_rendered_pages()
        if self.current_page_idx < len(pages) - 1:
            self.current_page_idx += 1
            self._show_current()

    def prev_chapter(self) -> None:
        pages = self._get_rendered_pages()
        if not pages or self.current_page_idx < 0 or self.current_page_idx >= len(pages):
            return
        cur_order = pages[self.current_page_idx].chapter_order
        prev_pages = [p for p in pages if p.chapter_order < cur_order]
        if prev_pages:
            target_order = prev_pages[-1].chapter_order
            first_p = next(p for p in prev_pages if p.chapter_order == target_order)
            self.current_page_idx = first_p.global_index
            self._show_current()

    def next_chapter(self) -> None:
        pages = self._get_rendered_pages()
        if not pages or self.current_page_idx < 0 or self.current_page_idx >= len(pages):
            return
        cur_order = pages[self.current_page_idx].chapter_order
        next_pages = [p for p in pages if p.chapter_order > cur_order]
        if next_pages:
            self.current_page_idx = next_pages[0].global_index
            self._show_current()

    def go_to_page(self, idx: int) -> None:
        pages = self._get_rendered_pages()
        if not pages:
            self.current_page_idx = 0
        else:
            self.current_page_idx = max(0, min(idx, len(pages) - 1))
        self._show_current()

    def open_go_to_page(self) -> None:
        pages = self._get_rendered_pages()
        if not pages:
            return
        GoToPageDialog(
            self.theme,
            current_page=self.current_page_idx + 1,
            total_pages=len(pages),
            on_done=lambda p: self.go_to_page(p - 1) if p is not None else None,
        ).open()

    def _on_chapter_selected(self, value: str) -> None:
        if self._chapter_spinner_updating:
            return
        if not value or value == "No chapters":
            return
        pages = self._get_rendered_pages()
        for p in pages:
            if f"{p.chapter_order}. {p.chapter_name}" == value:
                self.current_page_idx = p.global_index
                self._show_current()
                break

    def _update_chapter_spinner(self, chapters: list) -> None:
        if not hasattr(self, "_chapter_spinner"):
            return
        if not chapters:
            self._chapter_spinner_updating = True
            self._chapter_spinner.values = ()
            self._chapter_spinner.text = "No chapters"
            self._chapter_spinner.disabled = True
            self._chapter_spinner_updating = False
            return

        values = [f"{ch.chapter_number}. {ch.name}" for ch in chapters]
        self._chapter_spinner.values = values
        self._chapter_spinner.disabled = False

        pages = self._get_rendered_pages()
        self._chapter_spinner_updating = True
        if pages and 0 <= self.current_page_idx < len(pages):
            cur_p = pages[self.current_page_idx]
            match_txt = f"{cur_p.chapter_order}. {cur_p.chapter_name}"
            if match_txt in values:
                self._chapter_spinner.text = match_txt
            elif values:
                self._chapter_spinner.text = values[0]
        elif values:
            self._chapter_spinner.text = values[0]
        self._chapter_spinner_updating = False

    def open_book_instructions(self) -> None:
        name = self.current_book_name()
        if not name:
            return
        book = self._store.get_book(name)
        current_inst = book.custom_instruction if book else ""

        def _on_done(new_inst: Optional[str]) -> None:
            if new_inst is not None:
                self._store.update_book_instruction(name, new_inst)
                if self._on_instruction_updated is not None:
                    self._on_instruction_updated(name, new_inst)

        BookInstructionsDialog(self.theme, name, current_inst, on_done=_on_done).open()

    def prompt_delete_book(self) -> None:
        name = self.current_book_name()
        if not name:
            return

        def _on_confirm(confirmed: bool) -> None:
            if confirmed:
                self.delete_current_book()

        ConfirmDialog(
            self.theme,
            title="Delete Book",
            message=f"Are you sure you want to permanently delete '{name}' and all its chapters?",
            on_done=_on_confirm,
            danger=True,
            ok_text="Delete",
        ).open()

    def delete_current_book(self) -> None:
        name = self.current_book_name()
        if not name:
            return
        try:
            self._store.delete_book(name)
        except Exception:
            return
        self._invalidate_cache()
        if self._on_book_deleted is not None:
            self._on_book_deleted(name)
        else:
            self.select_book(None)

    def _invalidate_cache(self) -> None:
        self._cached_book_name = None
        self._cached_pages = []

    def _get_rendered_pages(self) -> list[_RenderedPageItem]:
        name = self.current_book_name()
        if not name:
            self._cached_book_name = None
            self._cached_pages = []
            return []
        if self._cached_book_name == name and self._cached_pages:
            return self._cached_pages

        try:
            chapters = self._store.list_chapters(name)
        except Exception:
            chapters = []

        pages_list: list[_RenderedPageItem] = []
        global_idx = 0
        for ch in chapters:
            content = self._store.read_chapter(name, ch.name) or ""
            try:
                imgs = markdown_render.render_markdown_pages(
                    content, text_width=PAGE_TEXT_WIDTH,
                    image_dir=self._store.chapters_dir(name))
            except Exception:
                imgs = markdown_render.render_markdown_pages("# (page could not be rendered)")
            if not imgs:
                imgs = markdown_render.render_markdown_pages(f"# {ch.name}\n\n*(Empty chapter)*")
            for p_idx, img in enumerate(imgs):
                pages_list.append(_RenderedPageItem(
                    chapter_order=ch.chapter_number,
                    chapter_name=ch.name,
                    page_index=p_idx,
                    page_count=len(imgs),
                    global_index=global_idx,
                    image=img,
                ))
                global_idx += 1

        self._cached_book_name = name
        self._cached_pages = pages_list
        return pages_list

    def _get_current_pages(self) -> list:
        return self._get_rendered_pages()

    # -- reader ------------------------------------------------------------
    def _build_reader(self) -> None:
        self._scroller = ScrollView(
            do_scroll_x=False,
            do_scroll_y=True,
            bar_width=10,
            bar_color=rgba(self.theme.accent, 0.85),
            bar_inactive_color=rgba(self.theme.muted, 0.4),
            scroll_type=["bars", "content"],
            scroll_wheel_distance=45,
        )
        self._pages_box = BoxLayout(
            orientation="vertical",
            size_hint=(1.0, None),
            spacing=8,
            padding=(14, 8, 14, 20),
        )
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
        self._bg_color.rgba = rgba(theme.panel)
        self._scroller.bar_color = rgba(theme.accent, 0.85)
        self._scroller.bar_inactive_color = rgba(theme.muted, 0.4)
        if hasattr(self, "_chapter_spinner"):
            self._chapter_spinner.color = rgba(theme.fg)
            self._chapter_spinner.background_color = rgba(theme.panel_2)
        if hasattr(self, "_instruction_btn"):
            self._instruction_btn.bg_color = rgba(theme.panel_2)
            self._instruction_btn.fg_color = rgba(theme.fg)
        if hasattr(self, "_delete_btn"):
            self._delete_btn.bg_color = rgba(theme.panel_2)
        self.refresh()

    def set_store(self, store) -> None:
        self._store = store
        self._invalidate_cache()
        self.refresh()

    def refresh(self) -> None:
        self._invalidate_cache()
        if not self._current_book:
            try:
                books = self._store.list_books()
                if books:
                    self._current_book = books[0].name
            except Exception:
                pass
        self._show_current()

    def select_book(self, name: str) -> None:
        if name and name != "no books":
            if self._current_book != name:
                self._invalidate_cache()
            self._current_book = name
        else:
            self._invalidate_cache()
            self._current_book = None
        self.current_page_idx = 0
        self._show_current()

    def current_book_name(self) -> Optional[str]:
        return self._current_book

    # -- rendering ---------------------------------------------------------
    def _update_pagination(self, total: int) -> None:
        if total <= 0:
            self._page_lbl.text = "0 pages"
            self._prev_btn.disabled = True
            self._next_btn.disabled = True
            self._goto_btn.disabled = True
        else:
            self._page_lbl.text = f"Page {self.current_page_idx + 1} of {total}"
            self._prev_btn.disabled = (self.current_page_idx <= 0)
            self._next_btn.disabled = (self.current_page_idx >= total - 1)
            self._goto_btn.disabled = (total <= 1)

    def _show_current(self) -> None:
        self._pages_box.clear_widgets()
        name = self.current_book_name()
        has_book = bool(name)
        if hasattr(self, "_instruction_btn"):
            self._instruction_btn.disabled = not has_book
        if hasattr(self, "_delete_btn"):
            self._delete_btn.disabled = not has_book
        if name is None:
            self._update_pagination(0)
            self._update_chapter_spinner([])
            self._place_hint("No books yet. Ask the assistant to create one, for example: "
                             '"Create a book called The Wandering Star with 4 chapters."')
            return
        book = self._store.get_book(name)
        if book is None:
            self._update_pagination(0)
            self._update_chapter_spinner([])
            self._place_hint(f"No book named '{name}' was found on disk.")
            return
        chapters = self._store.list_chapters(name)
        if not chapters:
            self._update_pagination(0)
            self._update_chapter_spinner([])
            self._place_hint(f"Book '{name}' has no chapters yet - ask the assistant to write "
                             "some on the Conversation tab.")
            return

        rendered_pages = self._get_rendered_pages()
        if not rendered_pages:
            self._update_pagination(0)
            self._update_chapter_spinner(chapters)
            return

        self.current_page_idx = max(0, min(self.current_page_idx, len(rendered_pages) - 1))
        self._update_pagination(len(rendered_pages))
        self._update_chapter_spinner(chapters)

        pages_to_show = [rendered_pages[self.current_page_idx]] if self.single_page_mode else rendered_pages
        show_caption = not self.single_page_mode

        avail = max(220.0, self._scroller.width - 44)
        for p in pages_to_show:
            img = p.image
            scale = min(self.zoom, avail / max(1.0, float(img.width)))
            if scale != 1.0:
                img_disp = img.resize((max(1, int(img.width * scale)),
                                       max(1, int(img.height * scale))))
            else:
                img_disp = img
            texture = image_to_texture(img_disp)

            title = p.chapter_name
            if p.page_count > 1:
                title += f" (Page {p.page_index + 1}/{p.page_count})"
            entry = _PageEntry(p.chapter_order, title, texture,
                               img_disp.width, img_disp.height, theme=self.theme,
                               show_caption=show_caption)
            self._pages_box.add_widget(entry)

        Clock.schedule_once(lambda _dt: setattr(self._scroller, "scroll_y", 1.0), 0.02)

    def _place_hint(self, text: str) -> None:
        hint = make_label(text, font_size=14, color=rgba(self.theme.muted),
                          auto_size=False, valign="top", halign="left")
        hint.text_size = (max(200.0, self._pages_box.width - 60), None)
        hint.bind(texture_size=lambda _l, ts: setattr(hint, "height", ts[1] + 60))
        hint.padding = (20, 30)
        self._pages_box.add_widget(hint)

    # -- folder ------------------------------------------------------------
    def _pick_folder(self) -> None:
        dlg = FilePickerDialog(self.theme, "Choose the books folder",
                               lambda path: self._apply_root(path), directory=True)
        dlg.open()

    def _apply_root(self, path: Optional[str]) -> None:
        if path and self._on_change_root is not None:
            self._on_change_root(path)

