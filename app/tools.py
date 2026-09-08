"""The file tools the model can invoke.

These run *locally* on the client. The OpenAI-compatible server only proposes the calls;
this module performs the actual read/write/delete operations against :class:`BookStore`.
Each handler receives a :class:`ToolContext` (which wraps a store and an optional event
callback) and returns a plain ``dict`` result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from .books_store import BookStore
from . import model_registry as reg

TOOL_DIR = "tools"


class ToolError(Exception):
    """A user-facing error while executing a tool."""


@dataclass
class ToolContext:
    """Bundles the book store with an optional event notifier for the GUI."""

    store: BookStore
    on_event: Optional[Callable[[dict], None]] = None
    extra: dict = field(default_factory=dict)

    def notify(self, **data: object) -> None:
        if self.on_event is not None:
            try:
                self.on_event(data)
            except Exception:  # pragma: no cover - never let notifications crash a tool
                pass


# --------------------------------------------------------------------------
# Section helpers for edit_page / edit_chapter
# --------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(?P<hash>#{0,6})\s+(?P<text>.*?)\s*#*\s*$")


def _split_body(body: str) -> str:
    body = (body or "").strip()
    if not body.endswith("\n"):
        body += "\n"
    return body


def _find_section(lines: list[str], section: str) -> tuple[int, int]:
    target = section.strip().lower()
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m and m.group("text").strip().lower() == target:
            start = i
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if _HEADING_RE.match(lines[j]):
                    end = j
                    break
            return start, end
    return -1, -1


def replace_section(text: str, section: str, new_body: str) -> str:
    """Replace the body found under ``section`` with ``new_body``."""
    lines = text.splitlines()
    start, end = _find_section(lines, section)
    if start == -1:
        raise ToolError(f"section '{section}' not found")
    heading_line = lines[start].rstrip("\n")
    body_lines = _split_body(new_body).splitlines(keepends=True)
    if body_lines and not body_lines[-1].endswith("\n"):
        body_lines[-1] += "\n"
    new_lines = list(lines[:start]) + [heading_line + "\n"] + body_lines
    # keep whatever came after the replaced section
    new_lines += list(lines[end:])
    return "".join(new_lines)


def append_text(text: str, additions: list[str], content: Optional[str] = None) -> str:
    """Append ``additions`` (or ``content``) to ``text``."""
    if additions:
        chunk = "\n\n".join(a.strip() for a in additions if a.strip())
    else:
        chunk = (content or "").strip()
    if not chunk:
        return text
    sep = "" if text.strip() == "" else ("\n" if text.endswith("\n") else "\n\n")
    return text + sep + chunk + ("\n" if not chunk.endswith("\n") else "")


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------

def _resolve_book_target(name: Optional[str] = None, book_id: Optional[str] = None) -> str:
    target = book_id or name or ""
    if not target:
        raise ToolError("Missing book name or book_id.")
    return target


def _create_book(ctx: ToolContext, name: str, author: str = "", description: str = "",
                 book_id: Optional[str] = None) -> dict:
    book = ctx.store.create_book(name, author or "", description or "", book_id=book_id)
    ctx.notify(kind="book_created", book=book.name, book_id=book.id)
    return {
        "ok": True,
        "message": f"Created book '{book.name}' (ID: {book.id}) with {book.chapter_count} chapter(s).",
        "book": {"id": book.id, "name": book.name, "author": book.author, "description": book.description,
                 "pages": book.page_count, "chapters": book.chapter_count},
    }


def _remove_book(ctx: ToolContext, name: Optional[str] = None, book_id: Optional[str] = None) -> dict:
    target = _resolve_book_target(name, book_id)
    book = ctx.store.get_book(target)
    if book and ctx.store.remove_book(book.name):
        ctx.notify(kind="book_removed", book=book.name, book_id=book.id)
        return {"ok": True, "book_id": book.id, "message": f"Removed book '{book.name}' (ID: {book.id})."}
    return {"ok": False, "message": f"No book '{target}' exists."}


def _list_books(ctx: ToolContext) -> dict:
    books = ctx.store.list_books()
    if not books:
        return {"ok": True, "message": "No books yet. Create one to get started.", "books": []}
    rows = [
        {"id": b.id, "name": b.name, "author": b.author, "pages": b.page_count,
         "chapters": b.chapter_count, "description": b.description}
        for b in books
    ]
    lines = [
        f"- {b['name']} (ID: {b['id']})" + (f"  *by {b['author']}*" if b["author"] else "")
        + f"  — {b['chapters']} chapter(s)"
        for b in rows
    ]
    return {"ok": True, "message": "\n".join(lines), "books": rows}


def _get_book_info(ctx: ToolContext, book_id: Optional[str] = None, name: Optional[str] = None) -> dict:
    target = _resolve_book_target(name, book_id)
    book = ctx.store.get_book(target)
    if book is None:
        return {"ok": False, "message": f"No book '{target}' exists."}
    chapters = ctx.store.list_chapters(book.name)
    rows = [
        {"chapter_number": c.chapter_number, "title": c.name, "file": c.file, "updated_at": c.updated_at}
        for c in chapters
    ]
    lines = [f"{c['chapter_number']}. {c['title']}" for c in rows]
    msg = f"Book '{book.name}' (ID: {book.id})" + (f" by {book.author}" if book.author else "") + f" has {len(chapters)} chapter(s)."
    if lines:
        msg += "\nChapters:\n" + "\n".join(lines)
    return {
        "ok": True,
        "book_id": book.id,
        "name": book.name,
        "author": book.author,
        "description": book.description,
        "chapter_count": len(chapters),
        "chapters": rows,
        "message": msg,
    }


def _list_chapters(ctx: ToolContext, book_id: Optional[str] = None, name: Optional[str] = None) -> dict:
    target = _resolve_book_target(name, book_id)
    book = ctx.store.get_book(target)
    if book is None:
        return {"ok": False, "message": f"No book '{target}' exists."}
    chapters = ctx.store.list_chapters(book.name)
    if not chapters:
        return {"ok": True, "book_id": book.id, "message": f"Book '{book.name}' (ID: {book.id}) has no chapters yet.", "chapters": []}
    lines = [f"{c.chapter_number}. {c.name} ({c.file})" for c in chapters]
    return {
        "ok": True,
        "book_id": book.id,
        "message": f"Book '{book.name}' (ID: {book.id}):\n" + "\n".join(lines),
        "chapters": [{"chapter_number": c.chapter_number, "title": c.name, "file": c.file} for c in chapters],
    }


def _read_chapter(ctx: ToolContext, chapter_number: int | str, book_id: Optional[str] = None,
                  name: Optional[str] = None) -> dict:
    target = _resolve_book_target(name, book_id)
    book = ctx.store.get_book(target)
    if book is None:
        return {"ok": False, "message": f"No book '{target}' exists."}
    content = ctx.store.read_chapter(book.name, chapter_number)
    if content is None:
        return {"ok": False, "message": f"No chapter {chapter_number} in book '{book.name}' (ID: {book.id})."}
    return {"ok": True, "book_id": book.id, "chapter_number": chapter_number, "content": content}


def _write_chapter(ctx: ToolContext, chapter_number: int | str, content: str,
                   title: Optional[str] = None, book_id: Optional[str] = None,
                   name: Optional[str] = None) -> dict:
    target = _resolve_book_target(name, book_id)
    book = ctx.store.get_book(target)
    if book is None:
        book = ctx.store.create_book(target, book_id=book_id)
    chapter = ctx.store.write_chapter(book.name, chapter_number, content or "", title=title)
    ctx.notify(kind="chapter_written", book=book.name, book_id=book.id, chapter=chapter.name, chapter_number=chapter.chapter_number)
    ctx.notify(kind="page_written", book=book.name, book_id=book.id, page=chapter.name, order=chapter.chapter_number)
    return {
        "ok": True,
        "book_id": book.id,
        "chapter_number": chapter.chapter_number,
        "title": chapter.name,
        "message": f"Wrote chapter {chapter.chapter_number} '{chapter.name}' ({chapter.file}) to '{book.name}' (ID: {book.id}).",
        "content": content or "",
    }


def _edit_chapter(ctx: ToolContext, chapter_number: int | str, operation: str,
                  book_id: Optional[str] = None, name: Optional[str] = None,
                  section: Optional[str] = None, content: Optional[str] = None,
                  additions: Optional[list[str]] = None) -> dict:
    target = _resolve_book_target(name, book_id)
    book = ctx.store.get_book(target)
    original = ctx.store.read_chapter(book.name, chapter_number) if book else None
    if book is None or original is None:
        book_desc = f"'{book.name}' (ID: {book.id})" if book else f"'{target}'"
        return {"ok": False, "message": f"No chapter {chapter_number} in book {book_desc}."}
    if operation == "append":
        appended = append_text(original, additions or [], content)
        ctx.store.write_chapter(book.name, chapter_number, appended)
        ctx.notify(kind="chapter_edited", book=book.name, book_id=book.id, chapter_number=chapter_number, operation="append")
        ctx.notify(kind="page_edited", book=book.name, book_id=book.id, page=str(chapter_number), operation="append")
        return {"ok": True, "book_id": book.id, "chapter_number": chapter_number, "message": f"Appended to chapter {chapter_number} in '{book.name}' (ID: {book.id})."}
    if operation in ("replace_section", "update_section"):
        if not section:
            return {"ok": False, "message": f"{operation} requires a 'section' heading."}
        updated = replace_section(original, section, content or "")
        ctx.store.write_chapter(book.name, chapter_number, updated)
        ctx.notify(kind="chapter_edited", book=book.name, book_id=book.id, chapter_number=chapter_number, operation=operation)
        ctx.notify(kind="page_edited", book=book.name, book_id=book.id, page=str(chapter_number), operation=operation)
        return {"ok": True, "book_id": book.id, "chapter_number": chapter_number, "message": f"{operation} on section '{section}' in chapter {chapter_number} (book ID: {book.id})."}
    return {"ok": False, "message": f"Unknown operation '{operation}'."}


def _delete_chapter(ctx: ToolContext, chapter_number: int | str, book_id: Optional[str] = None,
                    name: Optional[str] = None) -> dict:
    target = _resolve_book_target(name, book_id)
    book = ctx.store.get_book(target)
    if book is None:
        return {"ok": False, "message": f"No book '{target}' exists."}
    if ctx.store.delete_chapter(book.name, chapter_number):
        ctx.notify(kind="chapter_deleted", book=book.name, book_id=book.id, chapter_number=chapter_number)
        ctx.notify(kind="page_deleted", book=book.name, book_id=book.id, page=str(chapter_number))
        return {"ok": True, "book_id": book.id, "message": f"Deleted chapter {chapter_number} from '{book.name}' (ID: {book.id})."}
    return {"ok": False, "message": f"No chapter {chapter_number} in book '{book.name}' (ID: {book.id})."}


# --- Legacy page handlers (backwards compatibility) ---

def _list_pages(ctx: ToolContext, name: Optional[str] = None, book_id: Optional[str] = None) -> dict:
    target = _resolve_book_target(name, book_id)
    book = ctx.store.get_book(target)
    if book is None:
        return {"ok": False, "message": f"No book '{target}' exists."}
    pages = ctx.store.list_pages(book.name)
    if not pages:
        return {"ok": True, "book_id": book.id, "message": f"Book '{book.name}' (ID: {book.id}) has no pages yet.", "pages": []}
    lines = [f"{p.order}. {p.name} ({p.file})" for p in pages]
    return {"ok": True, "book_id": book.id, "message": f"Book '{book.name}' (ID: {book.id}):\n" + "\n".join(lines), "pages": [p.name for p in pages]}


def _read_page(ctx: ToolContext, page_name: str, name: Optional[str] = None,
               book_id: Optional[str] = None) -> dict:
    target = _resolve_book_target(name, book_id)
    book = ctx.store.get_book(target)
    if book is None:
        return {"ok": False, "message": f"No book '{target}' exists."}
    content = ctx.store.read_page(book.name, page_name)
    if content is None:
        return {"ok": False, "message": f"No page '{page_name}' in book '{book.name}' (ID: {book.id})."}
    return {"ok": True, "book_id": book.id, "content": content, "page": page_name}


def _write_page(ctx: ToolContext, page_name: str, content: str, name: Optional[str] = None,
                book_id: Optional[str] = None) -> dict:
    target = _resolve_book_target(name, book_id)
    book = ctx.store.get_book(target)
    if book is None:
        book = ctx.store.create_book(target, book_id=book_id)
    page = ctx.store.write_page(book.name, page_name, content or "")
    ctx.notify(kind="page_written", book=book.name, book_id=book.id, page=page_name, order=page.order)
    return {
        "ok": True,
        "book_id": book.id,
        "message": f"Wrote page '{page_name}' ({page.file}) to '{book.name}' (ID: {book.id}) (page {page.order}).",
        "content": content or "",
    }


def _edit_page(ctx: ToolContext, page_name: str, operation: str,
               name: Optional[str] = None, book_id: Optional[str] = None,
               section: Optional[str] = None, content: Optional[str] = None,
               additions: Optional[list[str]] = None) -> dict:
    target = _resolve_book_target(name, book_id)
    book = ctx.store.get_book(target)
    if book is None or ctx.store.read_page(book.name, page_name) is None:
        book_desc = f"'{book.name}' (ID: {book.id})" if book else f"'{target}'"
        return {"ok": False, "message": f"No page '{page_name}' in book {book_desc}."}
    if operation == "append":
        appended = append_text(ctx.store.read_page(book.name, page_name) or "", additions or [], content)
        ctx.store.write_page(book.name, page_name, appended)
        ctx.notify(kind="page_edited", book=book.name, book_id=book.id, page=page_name, operation="append")
        return {"ok": True, "book_id": book.id, "message": f"Appended to '{page_name}' in '{book.name}' (ID: {book.id})."}
    if operation in ("replace_section", "update_section"):
        if not section:
            return {"ok": False, "message": f"{operation} requires a 'section' heading."}
        original = ctx.store.read_page(book.name, page_name) or ""
        updated = replace_section(original, section, content or "")
        ctx.store.write_page(book.name, page_name, updated)
        ctx.notify(kind="page_edited", book=book.name, book_id=book.id, page=page_name, operation=operation)
        return {"ok": True, "book_id": book.id, "message": f"{operation} on section '{section}' in '{page_name}' (book ID: {book.id})."}
    return {"ok": False, "message": f"Unknown operation '{operation}'."}


def _delete_page(ctx: ToolContext, page_name: str, name: Optional[str] = None,
                 book_id: Optional[str] = None) -> dict:
    target = _resolve_book_target(name, book_id)
    book = ctx.store.get_book(target)
    if book is None:
        return {"ok": False, "message": f"No book '{target}' exists."}
    if ctx.store.delete_page(book.name, page_name):
        ctx.notify(kind="page_deleted", book=book.name, book_id=book.id, page=page_name)
        return {"ok": True, "book_id": book.id, "message": f"Deleted page '{page_name}' from '{book.name}' (ID: {book.id})."}
    return {"ok": False, "message": f"No page '{page_name}' in book '{book.name}' (ID: {book.id})."}


def _search_in_book(ctx: ToolContext, query: str, name: Optional[str] = None,
                    book_id: Optional[str] = None) -> dict:
    target = _resolve_book_target(name, book_id)
    book = ctx.store.get_book(target)
    if book is None:
        return {"ok": False, "message": f"No book '{target}' exists."}
    hits = ctx.store.search(book.name, query)
    if not hits:
        return {"ok": True, "book_id": book.id, "message": f"No matches for '{query}' in '{book.name}' (ID: {book.id}).", "hits": []}
    lines = [f"- Chapter {h['chapter_number']} ({h['chapter']}) line {h['line']}: {h['snippet']}" for h in hits]
    return {"ok": True, "book_id": book.id, "message": "\n".join(lines), "hits": hits}


HANDLERS: dict[str, Callable[..., dict]] = {
    reg.CREATE_BOOK: _create_book,
    reg.REMOVE_BOOK: _remove_book,
    reg.LIST_BOOKS: _list_books,
    reg.GET_BOOK_INFO: _get_book_info,
    reg.LIST_CHAPTERS: _list_chapters,
    reg.READ_CHAPTER: _read_chapter,
    reg.WRITE_CHAPTER: _write_chapter,
    reg.EDIT_CHAPTER: _edit_chapter,
    reg.DELETE_CHAPTER: _delete_chapter,
    reg.SEARCH_IN_BOOK: _search_in_book,
    # Legacy handlers
    reg.LIST_PAGES: _list_pages,
    reg.READ_PAGE: _read_page,
    reg.WRITE_PAGE: _write_page,
    reg.EDIT_PAGE: _edit_page,
    reg.DELETE_PAGE: _delete_page,
}


def _validate(name: str, kwargs: dict) -> None:
    spec = reg.BY_NAME.get(name)
    if not spec:
        raise ToolError(f"unknown tool '{name}'")
    params = spec.get("parameters", {})
    props = params.get("properties", {})
    for required in params.get("required", []):
        if required not in kwargs or kwargs.get(required) is None:
            raise ToolError(f"missing required argument '{required}' for {name}")
    for key, value in kwargs.items():
        if key not in props:
            raise ToolError(f"unexpected argument '{key}' for {name}")
        prop = props[key]
        if "enum" in prop and value not in prop["enum"]:
            raise ToolError(f"argument '{key}' must be one of {prop['enum']}")


def dispatch(ctx: ToolContext, name: str, arguments: dict) -> dict:
    """Validate and run a tool by ``name`` with ``arguments`` (a dict)."""
    _validate(name, arguments or {})
    handler = HANDLERS.get(name)
    if handler is None:
        raise ToolError(f"unknown tool '{name}'")
    try:
        return handler(ctx, **(arguments or {}))
    except ToolError:
        raise
    except TypeError as exc:
        raise ToolError(f"invalid arguments for {name}: {exc}") from exc
    except Exception as exc:  # defensive: turn unexpected errors into friendly messages
        raise ToolError(f"{name} failed: {exc}") from exc


def format_tool_result(name: str, result: dict) -> str:
    """Render a tool result as a short string for the conversation view."""
    if not isinstance(result, dict):
        return str(result)
    message = result.get("message")
    if message:
        return message
    return f"{name} succeeded"
