"""Tests for BooksPanel: single-page mode, pagination, Go to Page, and top-bar selector."""

from __future__ import annotations

from app.books_store import BookStore
from app.config import Config
from app.gui.application import BookWriterApp
from app.gui.books_panel import BooksPanel, _PageEntry
from app.gui.dialogs import GoToPageDialog
from app.theme import THEMES


def test_books_panel_pagination_and_single_page(tmp_path):
    store = BookStore(str(tmp_path / "books"))
    store.create_book("Story Book")
    store.write_page("Story Book", "Page 1", "Page 1 content")
    store.write_page("Story Book", "Page 2", "Page 2 content")
    store.write_page("Story Book", "Page 3", "Page 3 content")

    theme = THEMES["light"]
    panel = BooksPanel(theme, store)
    panel.select_book("Story Book")

    # Single-page mode by default
    assert panel.single_page_mode is True
    assert panel.current_page_idx == 0
    assert panel._page_lbl.text == "Page 1 of 3"
    assert panel._prev_btn.disabled is True
    assert panel._next_btn.disabled is False

    # In single-page mode, exactly 1 page entry is rendered
    page_entries = [c for c in panel._pages_box.children if isinstance(c, _PageEntry)]
    assert len(page_entries) == 1

    # Next page
    panel.next_page()
    assert panel.current_page_idx == 1
    assert panel._page_lbl.text == "Page 2 of 3"
    assert panel._prev_btn.disabled is False
    assert panel._next_btn.disabled is False

    # Next page -> Page 3 (last page)
    panel.next_page()
    assert panel.current_page_idx == 2
    assert panel._page_lbl.text == "Page 3 of 3"
    assert panel._prev_btn.disabled is False
    assert panel._next_btn.disabled is True

    # Previous page -> Page 2
    panel.prev_page()
    assert panel.current_page_idx == 1
    assert panel._page_lbl.text == "Page 2 of 3"

    # Go to page
    panel.go_to_page(0)
    assert panel.current_page_idx == 0
    assert panel._page_lbl.text == "Page 1 of 3"

    # Toggle to all-pages mode
    panel.toggle_view_mode()
    assert panel.single_page_mode is False
    assert panel._mode_btn.text == "All Pages"
    page_entries_all = [c for c in panel._pages_box.children if isinstance(c, _PageEntry)]
    assert len(page_entries_all) == 3

    # Toggle back to single-page mode
    panel.toggle_view_mode()
    assert panel.single_page_mode is True
    assert panel._mode_btn.text == "Single Page"
    page_entries_single = [c for c in panel._pages_box.children if isinstance(c, _PageEntry)]
    assert len(page_entries_single) == 1


def test_goto_page_dialog():
    theme = THEMES["light"]
    submitted = []

    def on_done(val):
        submitted.append(val)

    dlg = GoToPageDialog(theme, current_page=2, total_pages=5, on_done=on_done)
    assert dlg.total_pages == 5

    # Valid submission
    dlg.input.text = "4"
    dlg._submit()
    assert submitted == [4]

    # Out of range submission returns None
    submitted.clear()
    dlg = GoToPageDialog(theme, current_page=2, total_pages=5, on_done=on_done)
    dlg.input.text = "10"
    dlg._submit()
    assert submitted == [None]


def test_top_tab_bar_book_selector_and_folder_btn(tmp_path):
    store_dir = tmp_path / "books"
    store = BookStore(str(store_dir))
    store.create_book("Book Alpha")
    store.create_book("Book Beta")

    cfg = Config(book_root=str(store_dir))
    app = BookWriterApp(cfg)
    app._build_gui()

    # Folder button and book spinner are in the tab bar
    assert hasattr(app, "folder_btn")
    assert hasattr(app, "book_spinner")
    assert "Book Alpha" in app.book_spinner.values
    assert "Book Beta" in app.book_spinner.values

    # Select Book Beta via spinner
    app.book_spinner.text = "Book Beta"
    assert app.current_book == "Book Beta"
    assert app.books.current_book_name() == "Book Beta"

    # Verify BooksPanel scroller configuration
    assert app.books._pages_box.size_hint_y is None
    assert app.books._scroller.do_scroll_y is True
    assert app.books._scroller.scroll_wheel_distance >= 40
    assert app.books._scroller.bar_width > 0


def test_books_panel_chapter_navigation_and_pagebreaks(tmp_path):
    store = BookStore(str(tmp_path / "books"))
    store.create_book("Epic Novel")
    # Chapter 1 has an explicit pagebreak -> 2 pages
    store.write_chapter("Epic Novel", "Prologue", "Page 1 of Prologue\n\n<!-- pagebreak -->\n\nPage 2 of Prologue")
    # Chapter 2 has 1 page
    store.write_chapter("Epic Novel", "The Beginning", "Chapter 2 content here")

    theme = THEMES["light"]
    panel = BooksPanel(theme, store)
    panel.select_book("Epic Novel")

    # Chapter 1 (2 pages) + Chapter 2 (1 page) = 3 pages total
    pages = panel._get_rendered_pages()
    assert len(pages) == 3
    assert panel._page_lbl.text == "Page 1 of 3"
    assert panel._chapter_spinner.text == "1. Prologue"
    assert panel._chapter_spinner.values == ["1. Prologue", "2. The Beginning"]

    # Advance within chapter 1 to page 2
    panel.next_page()
    assert panel.current_page_idx == 1
    assert panel._page_lbl.text == "Page 2 of 3"
    assert panel._chapter_spinner.text == "1. Prologue"

    # Advance to page 3 (which is chapter 2)
    panel.next_page()
    assert panel.current_page_idx == 2
    assert panel._page_lbl.text == "Page 3 of 3"
    assert panel._chapter_spinner.text == "2. The Beginning"

    # Go back to chapter 1 using prev_chapter
    panel.prev_chapter()
    assert panel.current_page_idx == 0
    assert panel._chapter_spinner.text == "1. Prologue"

    # Go forward to chapter 2 using next_chapter
    panel.next_chapter()
    assert panel.current_page_idx == 2
    assert panel._chapter_spinner.text == "2. The Beginning"

    # Direct selection via chapter spinner
    panel._on_chapter_selected("1. Prologue")
    assert panel.current_page_idx == 0


def test_books_panel_and_app_delete_book(tmp_path):
    store = BookStore(str(tmp_path / "books"))
    store.create_book("To Delete")
    store.write_chapter("To Delete", "Intro", "Some content")
    store.create_book("Keep This")

    cfg = Config(book_root=str(tmp_path / "books"))
    app = BookWriterApp(cfg)
    app._build_gui()

    assert "To Delete" in app.book_spinner.values
    assert "Keep This" in app.book_spinner.values

    # Select and delete "To Delete"
    app.select_book = app.on_book_selected
    app.on_book_selected("To Delete")
    assert app.current_book == "To Delete"

    app.delete_book("To Delete")
    assert "To Delete" not in [b.name for b in store.list_books()]
    assert "To Delete" not in app.book_spinner.values
    assert app.current_book == "Keep This"

    # Delete the remaining book via BooksPanel
    panel = app.books
    assert panel.current_book_name() == "Keep This"
    panel.delete_current_book()
    assert len(store.list_books()) == 0


def test_books_panel_toolbar_responsive_layout(tmp_path):
    store = BookStore(str(tmp_path / "books"))
    theme = THEMES["light"]
    panel = BooksPanel(theme, store)

    # Wide layout test
    panel.width = 1400
    panel._update_toolbar_layout()
    assert panel._is_wide_toolbar is True
    assert panel._toolbar.height == 46
    assert panel._book_controls in panel._row1.children
    assert panel._page_controls in panel._row1.children

    # Narrow layout test (below threshold)
    panel.width = 900
    panel._update_toolbar_layout()
    assert panel._is_wide_toolbar is False
    assert panel._toolbar.height == 80
    assert panel._book_controls in panel._row1.children
    assert panel._page_controls in panel._row2.children


def test_flat_button_does_not_summarize_label():
    from app.gui.widgets import FlatButton
    btn = FlatButton(text="Instructions", size=(92, 30))
    # Button width must automatically expand to accommodate the full text
    assert btn.width >= 100
    assert btn._label is not None
    assert btn._label.shorten is False

    del_btn = FlatButton(text="Delete Book", size=(92, 34))
    assert del_btn.width >= 108
    assert del_btn._label.shorten is False

    mode_btn = FlatButton(text="Single Page", size=(96, 34))
    assert mode_btn.width >= 100
    mode_btn.text = "All Pages"
    assert mode_btn.width >= 90


def test_tab_bar_responsiveness_and_unsummarized_buttons(tmp_path):
    cfg = Config(book_root=str(tmp_path / "books"))
    app = BookWriterApp(cfg)
    app._build_gui()

    assert app.instruction_btn.width >= 100
    assert app.instruction_btn._label.shorten is False
    assert app.delete_book_btn.width >= 70
    assert app.delete_book_btn._label.shorten is False


def test_responsive_spinner_left_aligned_and_dropdown_expansion(tmp_path):
    cfg = Config(book_root=str(tmp_path / "books"))
    app = BookWriterApp(cfg)
    app._build_gui()

    # Book spinner properties
    assert app.book_spinner.halign == "left"
    assert app.book_spinner.shorten is True
    assert app.book_spinner.shorten_from == "right"

    # Populate with books of varying length
    short_title = "Short"
    long_title = "The Incredibly Magnificent Chronological History of Modern Automation and Robotics"
    app.book_spinner.values = (short_title, long_title)
    app.book_spinner.text = long_title

    # Main button text box is bounded and left-aligned
    assert app.book_spinner.text_size[0] == app.book_spinner.width - 20
    assert app.book_spinner.halign == "left"

    # Dropdown expands to fit the largest title
    dp_w = app.book_spinner._calc_dropdown_width()
    assert dp_w > app.book_spinner.width
    assert dp_w >= 450  # comfortably fits the long title

    # Check that dropdown items are left-aligned
    app.book_spinner._update_dropdown()
    dp = app.book_spinner._dropdown
    options = [c for c in dp.container.children]
    assert len(options) == 2
    for opt in options:
        assert opt.halign == "left"
        assert opt.shorten_from == "right"
        assert opt.text_size[0] > 0

    # Test chapter spinner in BooksPanel
    panel = app.books
    assert panel._chapter_spinner.halign == "left"
    assert panel._chapter_spinner.shorten_from == "right"

    ch1 = "1. Introduction"
    ch2 = "2. A Very Long and Detailed Treatise on Advanced Autonomous Systems in 2026"
    panel._chapter_spinner.values = (ch1, ch2)
    panel._chapter_spinner.text = ch2

    ch_dp_w = panel._chapter_spinner._calc_dropdown_width()
    assert ch_dp_w > panel._chapter_spinner.width
    assert ch_dp_w >= 400

    panel._chapter_spinner._update_dropdown()
    ch_options = [c for c in panel._chapter_spinner._dropdown.container.children]
    assert len(ch_options) == 2
    for ch_opt in ch_options:
        assert ch_opt.halign == "left"
        assert ch_opt.shorten_from == "right"




