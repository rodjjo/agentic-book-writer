"""On-disk management of books and their pages.

Layout on disk::

    <book_root>/
        <slug(book-name)>/
            book.json        # metadata + ordered list of pages
            pages/
                001_the-beginning.md
                002_a_new_day.md
                ...

Each ``*.md`` file holds *pure* Markdown — all page metadata lives in ``book.json`` so the
pages render cleanly and can be edited with any editor.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str, max_len: int = 80) -> str:
    """Turn arbitrary text into a safe, filesystem-friendly slug."""
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    value = value[:max_len].strip("-")
    return value or "untitled"


@dataclass
class Page:
    """A single page (one Markdown file) inside a book."""

    name: str
    file: str           # filename relative to the book folder
    order: int
    created_at: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Page":
        return cls(
            name=str(data.get("name", "Untitled")),
            file=str(data.get("file", "page.md")),
            order=int(data.get("order", 0)),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


@dataclass
class Book:
    """Metadata describing a book and the pages it contains."""

    name: str
    folder: str
    author: str = ""
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    pages: list[Page] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Book":
        return cls(
            name=str(data.get("name", "Untitled")),
            folder=str(data.get("folder", "")),
            author=str(data.get("author", "")),
            description=str(data.get("description", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            pages=[Page.from_dict(p) for p in data.get("pages", [])],
        )


class BookStoreError(Exception):
    """Raised for user-facing book/store problems (missing book, duplicate name, ...)."""


class BookStore:
    """Creates, reads and deletes books and their pages under ``root``."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- paths ------------------------------------------------------------
    def book_dir(self, book_name: str) -> Path:
        return self.root / slugify(book_name)

    def pages_dir(self, book_name: str) -> Path:
        return self.book_dir(book_name) / "pages"

    def _book_json_path(self, book_name: str) -> Path:
        return self.book_dir(book_name) / "book.json"

    def _read_json(self, book_name: str) -> Optional[dict]:
        path = self._book_json_path(book_name)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise BookStoreError(f"{book_name}: corrupt book.json ({exc})") from exc

    def _write_json(self, book_name: str, data: dict) -> None:
        path = self._book_json_path(book_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_book(self, book_name: str) -> Optional[Book]:
        data = self._read_json(book_name)
        return Book.from_dict(data) if data is not None else None

    # -- CRUD -------------------------------------------------------------
    def create_book(
        self,
        name: str,
        author: str = "",
        description: str = "",
    ) -> Book:
        """Create a new (empty) book. Raises if a book with that name exists."""
        name = (name or "").strip()
        if not name:
            raise BookStoreError("book name cannot be empty")
        if self.get_book(name) is not None:
            raise BookStoreError(f"a book named '{name}' already exists")
        now = _now_iso()
        book = Book(
            name=name,
            folder=slugify(name),
            author=author,
            description=description,
            created_at=now,
            updated_at=now,
            pages=[],
        )
        self.pages_dir(name).mkdir(parents=True, exist_ok=True)
        self._write_json(name, book.as_dict())
        return book

    def _load_book(self, book_name: str) -> Book:
        book = self.get_book(book_name)
        if book is None:
            raise BookStoreError(f"no book named '{book_name}'")
        return book

    def list_books(self) -> list[Book]:
        books: list[Book] = []
        if not self.root.exists():
            return books
        for meta_dir in sorted(self.root.iterdir()):
            if not meta_dir.is_dir():
                continue
            data_path = meta_dir / "book.json"
            if data_path.exists():
                try:
                    books.append(Book.from_dict(json.loads(data_path.read_text(encoding="utf-8"))))
                except (json.JSONDecodeError, OSError):
                    continue
        books.sort(key=lambda b: (b.created_at or "", b.name.lower()))
        return books

    def remove_book(self, book_name: str) -> bool:
        book = self.get_book(book_name)
        if book is None:
            return False
        import shutil

        shutil.rmtree(self.book_dir(book_name), ignore_errors=True)
        return True

    def list_pages(self, book_name: str) -> list[Page]:
        book = self._load_book(book_name)
        return sorted(book.pages, key=lambda p: (p.order, p.name.lower()))

    def _next_order(self, book: Book) -> int:
        return (max((p.order for p in book.pages), default=0) + 1)

    def _page_path(self, book_name: str, page: Page) -> Path:
        return self.pages_dir(book_name) / page.file

    def write_page(
        self,
        book_name: str,
        page_name: str,
        content: str,
    ) -> Page:
        """Create or overwrite a page. ``page_name`` becomes the page's display name."""
        book = self._load_book(book_name)
        page_name = (page_name or "Untitled").strip()
        existing = {p.name.lower(): p for p in book.pages}
        now = _now_iso()

        if page_name.lower() in existing:
            page = existing[page_name.lower()]
            self._page_path(book_name, page).write_text(
                content if content.endswith("\n") else content + "\n", encoding="utf-8"
            )
            page.updated_at = now
        else:
            order = self._next_order(book)
            page = Page(
                name=page_name,
                file=f"{order:03d}_{slugify(page_name)}.md",
                order=order,
                created_at=now,
                updated_at=now,
            )
            self._page_path(book_name, page).write_text(
                content if content.endswith("\n") else content + "\n", encoding="utf-8"
            )
            book.pages.append(page)

        book.updated_at = now
        self._write_json(book_name, book.as_dict())
        return page

    def read_page(self, book_name: str, page_name: str) -> Optional[str]:
        book = self._load_book(book_name)
        page = next((p for p in book.pages if p.name.lower() == page_name.lower()), None)
        if page is None:
            return None
        path = self._page_path(book_name, page)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def delete_page(self, book_name: str, page_name: str) -> bool:
        book = self._load_book(book_name)
        idx = next((i for i, p in enumerate(book.pages) if p.name.lower() == page_name.lower()), None)
        if idx is None:
            return False
        page = book.pages.pop(idx)
        path = self._page_path(book_name, page)
        if path.exists():
            path.unlink()
        # Re-number remaining pages so orders stay contiguous.
        for new_order, page in enumerate(book.pages, start=1):
            page.order = new_order
        book.updated_at = _now_iso()
        self._write_json(book_name, book.as_dict())
        return True

    def search(self, book_name: str, query: str) -> list[dict]:
        """Case-insensitive search across every page of a book.

        Returns a list of hits: ``{"page", "order", "line", "snippet", "match"}``.
        """
        book = self._load_book(book_name)
        query = (query or "").strip()
        if not query:
            return []
        results: list[dict] = []
        needle = query.lower()
        for page in sorted(book.pages, key=lambda p: p.order):
            path = self._page_path(book_name, page)
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if needle in line.lower():
                    snippet = line.strip()
                    if len(snippet) > 200:
                        snippet = snippet[:197] + "..."
                    results.append(
                        {
                            "page": page.name,
                            "order": page.order,
                            "file": page.file,
                            "line": lineno,
                            "snippet": snippet,
                            "match": query,
                        }
                    )
        return results

    # -- pure helpers -----------------------------------------------------
    @staticmethod
    def exists(root: str | Path, book_name: str) -> bool:
        return BookStore(root).get_book(book_name) is not None


# Backwards/forward friendly alias used in tests.
BookMetadata = Book
