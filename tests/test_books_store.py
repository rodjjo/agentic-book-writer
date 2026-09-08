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
    assert (books_root / "my-first-book" / "chapters").is_dir()


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
    md_files = sorted(p.name for p in (books_root / "manual" / "chapters").glob("*.md"))
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


def test_book_uuid4_in_json_and_lookup(books_root):
    import json
    import uuid

    store = BookStore(books_root)
    book = store.create_book("Identifier Test", author="Tester")
    
    # Must be valid UUIDv4
    parsed_uuid = uuid.UUID(book.id, version=4)
    assert str(parsed_uuid) == book.id

    # On disk book.json has "id"
    json_path = books_root / "identifier-test" / "book.json"
    assert json_path.exists()
    raw_data = json.loads(json_path.read_text(encoding="utf-8"))
    assert raw_data.get("id") == book.id

    # Lookup by uuid
    by_uuid = store.get_book(book.id)
    assert by_uuid is not None
    assert by_uuid.name == "Identifier Test"

    by_id = store.get_book_by_id(book.id)
    assert by_id is not None
    assert by_id.id == book.id


def test_legacy_book_json_without_id_generates_uuid4(books_root):
    import json
    import uuid

    folder = books_root / "legacy-book"
    folder.mkdir(parents=True)
    legacy_json = {
        "name": "Legacy Book",
        "folder": "legacy-book",
        "author": "Old Author",
        "pages": [],
    }
    (folder / "book.json").write_text(json.dumps(legacy_json), encoding="utf-8")

    store = BookStore(books_root)
    book = store.get_book("Legacy Book")
    assert book is not None
    assert book.id
    # Valid UUIDv4 generated
    parsed = uuid.UUID(book.id, version=4)
    assert str(parsed) == book.id


def test_page_template_uuid_and_renumbering(books_root):
    store = BookStore(books_root)
    book = store.create_book("Page Template Book")
    b_id = book.id

    p1 = store.write_page("Page Template Book", "First", "First page content")
    p2 = store.write_page("Page Template Book", "Second", "Second page content")
    p3 = store.write_page("Page Template Book", "Third", "Third page content")

    expected_p1 = f"{b_id}_0001.md"
    expected_p2 = f"{b_id}_0002.md"
    expected_p3 = f"{b_id}_0003.md"

    assert p1.file == expected_p1
    assert p2.file == expected_p2
    assert p3.file == expected_p3

    chapters_dir = books_root / "page-template-book" / "chapters"
    assert (chapters_dir / expected_p1).exists()
    assert (chapters_dir / expected_p2).exists()
    assert (chapters_dir / expected_p3).exists()

    # Read page by filename as well as display name
    assert store.read_page("Page Template Book", "First") == "First page content\n"
    assert store.read_page("Page Template Book", expected_p1) == "First page content\n"

    # Delete page 2: page 3 renumbered to 2 and renamed to _0002.md
    assert store.delete_page("Page Template Book", "Second") is True
    pages = store.list_pages("Page Template Book")
    assert [p.name for p in pages] == ["First", "Third"]
    assert [p.order for p in pages] == [1, 2]
    assert pages[1].file == f"{b_id}_0002.md"

    assert (chapters_dir / expected_p1).exists()
    assert (chapters_dir / f"{b_id}_0002.md").exists()
    assert not (chapters_dir / expected_p3).exists()


def test_book_custom_instruction(books_root):
    store = BookStore(books_root)
    book = store.create_book("Instruction Book", custom_instruction="Write in noir style.")
    assert book.custom_instruction == "Write in noir style."

    # Load from disk
    loaded = store.get_book("Instruction Book")
    assert loaded is not None
    assert loaded.custom_instruction == "Write in noir style."

    # Update instruction
    store.update_book_instruction("Instruction Book", "Write in cyberpunk style.")
    reloaded = store.get_book("Instruction Book")
    assert reloaded.custom_instruction == "Write cyberpunk style." or reloaded.custom_instruction == "Write in cyberpunk style."
    assert reloaded.custom_instruction == "Write in cyberpunk style."


def test_chapter_crud_operations(books_root):
    store = BookStore(books_root)
    book = store.create_book("Novel")
    ch1 = store.write_chapter("Novel", 1, "# Chapter One\n\nIt began on a rainy Tuesday.", title="The Beginning")
    assert ch1.chapter_number == 1
    assert ch1.name == "The Beginning"

    ch2 = store.write_chapter("Novel", 2, "# Chapter Two\n\nThe storm cleared.", title="The Aftermath")
    assert ch2.chapter_number == 2

    # list chapters
    chapters = store.list_chapters("Novel")
    assert len(chapters) == 2
    assert [c.name for c in chapters] == ["The Beginning", "The Aftermath"]
    assert [c.chapter_number for c in chapters] == [1, 2]

    # read chapter by number and title
    content1 = store.read_chapter("Novel", 1)
    assert "rainy Tuesday" in content1
    content2 = store.read_chapter("Novel", "The Aftermath")
    assert "storm cleared" in content2

    # delete chapter and renumber
    assert store.delete_chapter("Novel", 1) is True
    remaining = store.list_chapters("Novel")
    assert len(remaining) == 1
    assert remaining[0].chapter_number == 1
    assert remaining[0].name == "The Aftermath"



