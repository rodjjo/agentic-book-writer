"""BookStore on-disk behaviour tests."""

from __future__ import annotations

import pytest

from app.books_store import Book, BookStore, BookStoreError, Page, slugify


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Hello World", "hello-world"),
     ("  Café & Tea!! ", "caf-tea"),
     ("---", "untitled"),
     ("a" * 200, "a" * 80),
     ("", "untitled")],
)
def test_slugify(text, expected):
    assert slugify(text) == expected


def test_create_and_list(books_root):
    store = BookStore(books_root)
    assert store.list_books() == []
    book = store.create_book("My First Book", author="A. Author", description="d")
    assert book.name == "My First Book"
    assert store.get_book("my first book") is not None  # case-insensitive by slug
    names = [b.name for b in store.list_books()]
    assert names == ["My First Book"]
    # folder layout
    assert (books_root / "my-first-book" / "book.json").exists()
    assert (books_root / "my-first-book" / "pages").is_dir()


def test_duplicate_name_rejected(books_root):
    store = BookStore(books_root)
    store.create_book("Dup")
    with pytest.raises(BookStoreError):
        store.create_book("dup")


def test_empty_name_rejected(books_root):
    store = BookStore(books_root)
    with pytest.raises(BookStoreError):
        store.create_book("  ")


def test_write_read_page(books_root):
    store = BookStore(books_root)
    store.create_book("Manual")
    store.write_page("Manual", "Introduction", "# Hello\n\nBody text.")
    page = store.write_page("Manual", "Chapter Two", "Second page.")
    assert page.order == 2

    content = store.read_page("Manual", "introduction")
    assert content == "# Hello\n\nBody text.\n"
    pages = store.list_pages("Manual")
    assert [p.name for p in pages] == ["Introduction", "Chapter Two"]
    # files are plain markdown next to book.json
    md_files = sorted(p.name for p in (books_root / "manual" / "pages").glob("*.md"))
    assert len(md_files) == 2


def test_overwrite_page_keeps_order(books_root):
    store = BookStore(books_root)
    store.create_book("B")
    store.write_page("B", "One", "old")
    store.write_page("B", "One", "new content")
    assert store.read_page("B", "One") == "new content\n"
    assert len(store.list_pages("B")) == 1


def test_read_missing(books_root):
    store = BookStore(books_root)
    store.create_book("B")
    assert store.read_page("B", "nope") is None
    with pytest.raises(BookStoreError):
        store.read_page("Ghost", "x")


def test_delete_page_renumbers(books_root):
    store = BookStore(books_root)
    store.create_book("B")
    for i, name in enumerate(["One", "Two", "Three"], start=1):
        store.write_page("B", name, f"page {i}")
    assert store.delete_page("B", "two") is True
    pages = store.list_pages("B")
    assert [p.name for p in pages] == ["One", "Three"]
    assert [p.order for p in pages] == [1, 2]
    assert store.delete_page("B", "two") is False
    # the file disappeared
    assert not (books_root / "b" / "pages" / "002_three.md").exists()


def test_search(books_root):
    store = BookStore(books_root)
    store.create_book("B")
    store.write_page("B", "Alpha", "# Alpha\n\nThe quick brown fox jumps.")
    store.write_page("B", "Beta", "beta line about foxes.")
    hits = store.search("B", "FOX")
    assert len(hits) == 2
    pages = {h["page"] for h in hits}
    assert pages == {"Alpha", "Beta"}
    assert hits[0]["line"] >= 1
    assert store.search("B", "zzz") == []
    assert store.search("B", "") == []


def test_remove_book(books_root):
    store = BookStore(books_root)
    store.create_book("Gone")
    store.write_page("Gone", "p", "content")
    assert store.remove_book("gone") is True
    assert store.get_book("Gone") is None
    assert not (books_root / "gone").exists()
    assert store.remove_book("gone") is False
