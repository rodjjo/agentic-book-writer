"""On-disk management of books and their chapters.

Layout on disk::

    <book_root>/
        <slug(book-name)>/
            book.json        # metadata (including uuid4 id) + ordered list of chapters
            chapters/
                <book_uuid>_0001.md
                <book_uuid>_0002.md
                ...

Each ``*.md`` file holds *pure* Markdown — all chapter metadata lives in ``book.json`` so the
chapters render cleanly and can be edited with any editor.
"""

from __future__ import annotations

import json
import re
import time
import uuid
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
class Chapter:
    """A single chapter (one Markdown file) inside a book."""

    name: str
    file: str           # filename relative to the book folder (e.g. chapters/<id>_0001.md)
    chapter_number: int # 1-based order
    created_at: str = ""
    updated_at: str = ""

    @property
    def order(self) -> int:
        return self.chapter_number

    @order.setter
    def order(self, val: int) -> None:
        self.chapter_number = val

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "file": self.file,
            "chapter_number": self.chapter_number,
            "order": self.chapter_number,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Chapter":
        num = int(data.get("chapter_number") or data.get("order", 0))
        return cls(
            name=str(data.get("name") or data.get("title", f"Chapter {num}")),
            file=str(data.get("file", "chapter.md")),
            chapter_number=num,
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


# Backwards compatibility alias
Page = Chapter


@dataclass
class Book:
    """Metadata describing a book and the chapters it contains."""

    name: str
    folder: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    author: str = ""
    description: str = ""
    custom_instruction: str = ""
    created_at: str = ""
    updated_at: str = ""
    chapters: list[Chapter] = field(default_factory=list)

    @property
    def chapter_count(self) -> int:
        return len(self.chapters)

    @property
    def page_count(self) -> int:
        return len(self.chapters)

    @property
    def pages(self) -> list[Chapter]:
        return self.chapters

    @pages.setter
    def pages(self, val: list[Chapter]) -> None:
        self.chapters = val

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "folder": self.folder,
            "author": self.author,
            "description": self.description,
            "custom_instruction": self.custom_instruction,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "chapters": [c.as_dict() for c in self.chapters],
            "pages": [c.as_dict() for c in self.chapters],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Book":
        raw_items = data.get("chapters")
        if raw_items is None:
            raw_items = data.get("pages", [])
        return cls(
            id=str(data.get("id") or uuid.uuid4()),
            name=str(data.get("name", "Untitled")),
            folder=str(data.get("folder", "")),
            author=str(data.get("author", "")),
            description=str(data.get("description", "")),
            custom_instruction=str(data.get("custom_instruction", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            chapters=[Chapter.from_dict(c) for c in raw_items],
        )


class BookStoreError(Exception):
    """Raised for user-facing book/store problems (missing book, duplicate name, ...)."""


class BookStore:
    """Creates, reads and deletes books and their chapters under ``root``."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- paths ------------------------------------------------------------
    def book_dir(self, identifier: str) -> Path:
        direct = self.root / slugify(identifier)
        if (direct / "book.json").exists():
            return direct
        for b in self.list_books():
            if b.id == identifier or b.name.lower() == identifier.lower():
                return self.root / b.folder
        return direct

    def chapters_dir(self, identifier: str) -> Path:
        bdir = self.book_dir(identifier)
        ch_dir = bdir / "chapters"
        if ch_dir.exists():
            return ch_dir
        # fallback to legacy pages directory if existing
        pg_dir = bdir / "pages"
        if pg_dir.exists():
            return pg_dir
        return ch_dir

    def pages_dir(self, identifier: str) -> Path:
        """Alias for backwards compatibility."""
        return self.chapters_dir(identifier)

    def _book_json_path(self, identifier: str) -> Path:
        return self.book_dir(identifier) / "book.json"

    def _read_json(self, identifier: str) -> Optional[dict]:
        path = self._book_json_path(identifier)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise BookStoreError(f"{identifier}: corrupt book.json ({exc})") from exc

    def _write_json(self, identifier: str, data: dict) -> None:
        path = self._book_json_path(identifier)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_book_by_id(self, book_id: str) -> Optional[Book]:
        if not book_id:
            return None
        for book in self.list_books():
            if book.id == book_id:
                return book
        return None

    def get_book(self, identifier: str) -> Optional[Book]:
        if not identifier:
            return None
        data = self._read_json(identifier)
        if data is not None:
            return Book.from_dict(data)
        for book in self.list_books():
            if book.id == identifier or book.name.lower() == identifier.lower() or book.folder == identifier:
                return book
        return None

    # -- CRUD -------------------------------------------------------------
    def create_book(
        self,
        name: str,
        author: str = "",
        description: str = "",
        book_id: Optional[str] = None,
        custom_instruction: str = "",
    ) -> Book:
        """Create a new (empty) book. Raises if a book with that name exists."""
        name = (name or "").strip()
        if not name:
            raise BookStoreError("book name cannot be empty")
        if self.get_book(name) is not None:
            raise BookStoreError(f"a book named '{name}' already exists")
        now = _now_iso()
        book = Book(
            id=book_id or str(uuid.uuid4()),
            name=name,
            folder=slugify(name),
            author=author,
            description=description,
            custom_instruction=custom_instruction,
            created_at=now,
            updated_at=now,
            chapters=[],
        )
        self.chapters_dir(name).mkdir(parents=True, exist_ok=True)
        self._write_json(name, book.as_dict())
        return book

    def update_book_instruction(self, identifier: str, instruction: str) -> Book:
        """Update and persist the custom instruction for a book."""
        book = self._load_book(identifier)
        book.custom_instruction = (instruction or "").strip()
        book.updated_at = _now_iso()
        self._write_json(book.folder, book.as_dict())
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

        shutil.rmtree(self.book_dir(book.folder), ignore_errors=True)
        return True

    def delete_book(self, book_name: str) -> bool:
        """Alias for remove_book."""
        return self.remove_book(book_name)

    def list_chapters(self, book_name: str) -> list[Chapter]:
        book = self._load_book(book_name)
        return sorted(book.chapters, key=lambda c: (c.chapter_number, c.name.lower()))

    def list_pages(self, book_name: str) -> list[Chapter]:
        """Backwards compatibility alias."""
        return self.list_chapters(book_name)

    def _next_chapter_number(self, book: Book) -> int:
        return (max((c.chapter_number for c in book.chapters), default=0) + 1)

    def _chapter_path(self, book_name: str, chapter: Chapter) -> Path:
        return self.chapters_dir(book_name) / Path(chapter.file).name

    def _page_path(self, book_name: str, page: Chapter) -> Path:
        """Backwards compatibility alias."""
        return self._chapter_path(book_name, page)

    def write_chapter(
        self,
        book_name: str,
        chapter_number: int | str,
        content: str,
        title: Optional[str] = None,
    ) -> Chapter:
        """Create or overwrite a chapter by its chapter number."""
        book = self._load_book(book_name)
        now = _now_iso()
        self.chapters_dir(book.folder).mkdir(parents=True, exist_ok=True)
        try:
            num = int(chapter_number)
            if num <= 0:
                num = self._next_chapter_number(book)
        except (ValueError, TypeError):
            if title is None and isinstance(chapter_number, str) and chapter_number not in ("next", ""):
                title = chapter_number
            num = self._next_chapter_number(book)

        existing = next((c for c in book.chapters if c.chapter_number == num), None)
        if existing:
            if title and title.strip():
                existing.name = title.strip()
            path = self._chapter_path(book.folder, existing)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                content if content.endswith("\n") else content + "\n", encoding="utf-8"
            )
            existing.updated_at = now
            chapter = existing
        else:
            ch_title = (title or f"Chapter {num}").strip()
            filename = f"{book.id}_{num:04d}.md"
            chapter = Chapter(
                name=ch_title,
                file=filename,
                chapter_number=num,
                created_at=now,
                updated_at=now,
            )
            path = self._chapter_path(book.folder, chapter)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                content if content.endswith("\n") else content + "\n", encoding="utf-8"
            )
            book.chapters.append(chapter)
            book.chapters.sort(key=lambda c: c.chapter_number)

        book.updated_at = now
        self._write_json(book.folder, book.as_dict())
        return chapter

    def write_page(
        self,
        book_name: str,
        page_name: str,
        content: str,
    ) -> Chapter:
        """Backwards compatibility: create or overwrite chapter by title or number."""
        book = self._load_book(book_name)
        clean = (page_name or "Untitled").strip()
        existing = next((c for c in book.chapters if c.name.lower() == clean.lower()), None)
        if existing:
            return self.write_chapter(book_name, existing.chapter_number, content, title=clean)
        if clean.isdigit():
            return self.write_chapter(book_name, int(clean), content)
        num = self._next_chapter_number(book)
        return self.write_chapter(book_name, num, content, title=clean)

    def read_chapter(self, book_name: str, chapter_target: int | str) -> Optional[str]:
        """Read chapter content by chapter number or title."""
        book = self._load_book(book_name)
        target = str(chapter_target).strip()
        ch = None
        if target.isdigit():
            num = int(target)
            ch = next((c for c in book.chapters if c.chapter_number == num), None)
        if ch is None:
            low = target.lower()
            ch = next((c for c in book.chapters if c.name.lower() == low), None)
        if ch is None:
            low = target.lower()
            ch = next((c for c in book.chapters if Path(c.file).name.lower() == low or Path(c.file).stem.lower() == low), None)
        if ch is None:
            return None
        path = self._chapter_path(book.folder, ch)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def read_page(self, book_name: str, page_name: str) -> Optional[str]:
        """Backwards compatibility alias."""
        return self.read_chapter(book_name, page_name)

    def delete_chapter(self, book_name: str, chapter_target: int | str) -> bool:
        """Delete a chapter by chapter number or title and renumber remaining chapters."""
        book = self._load_book(book_name)
        target = str(chapter_target).strip()
        idx = None
        if target.isdigit():
            num = int(target)
            idx = next((i for i, c in enumerate(book.chapters) if c.chapter_number == num), None)
        if idx is None:
            low = target.lower()
            idx = next((i for i, c in enumerate(book.chapters) if c.name.lower() == low), None)
        if idx is None:
            low = target.lower()
            idx = next((i for i, c in enumerate(book.chapters) if Path(c.file).name.lower() == low or Path(c.file).stem.lower() == low), None)
        if idx is None:
            return False

        ch = book.chapters.pop(idx)
        path = self._chapter_path(book.folder, ch)
        if path.exists():
            path.unlink()

        # Re-number remaining chapters so numbers stay contiguous.
        for new_num, c in enumerate(book.chapters, start=1):
            old_file = c.file
            new_file = f"{book.id}_{new_num:04d}.md"
            old_path = self._chapter_path(book.folder, c)
            c.chapter_number = new_num
            c.file = new_file
            new_path = self._chapter_path(book.folder, c)
            if old_path.exists() and old_path != new_path and not new_path.exists():
                new_path.parent.mkdir(parents=True, exist_ok=True)
                old_path.rename(new_path)

        book.updated_at = _now_iso()
        self._write_json(book.folder, book.as_dict())
        return True

    def delete_page(self, book_name: str, page_name: str) -> bool:
        """Backwards compatibility alias."""
        return self.delete_chapter(book_name, page_name)

    def search(self, book_name: str, query: str) -> list[dict]:
        """Case-insensitive search across every chapter of a book.

        Returns a list of hits: ``{"chapter", "chapter_number", "page", "order", "line", "snippet", "match"}``.
        """
        book = self._load_book(book_name)
        query = (query or "").strip()
        if not query:
            return []
        results: list[dict] = []
        needle = query.lower()
        for ch in sorted(book.chapters, key=lambda c: c.chapter_number):
            path = self._chapter_path(book.folder, ch)
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
                            "chapter": ch.name,
                            "chapter_number": ch.chapter_number,
                            "page": ch.name,
                            "order": ch.chapter_number,
                            "file": ch.file,
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
