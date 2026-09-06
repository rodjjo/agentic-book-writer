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
# Section helpers for edit_page
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

def _create_book(ctx: ToolContext, name: str, author: str = "", description: str = "") -> dict:
    book = ctx.store.create_book(name, author or "", description or "")
    ctx.notify(kind="book_created", book=book.name)
    return {
        "ok": True,
        "message": f"Created book '{book.name}' with {book.page_count} page(s).",
        "book": {"name": book.name, "author": book.author, "description": book.description,
                 "pages": book.page_count},
    }


def _remove_book(ctx: ToolContext, name: str) -> dict:
    if ctx.store.remove_book(name):
        ctx.notify(kind="book_removed", book=name)
        return {"ok": True, "message": f"Removed book '{name}'."}
    return {"ok": False, "message": f"No book named '{name}' exists."}


def _list_books(ctx: ToolContext) -> dict:
    books = ctx.store.list_books()
    if not books:
        return {"ok": True, "message": "No books yet. Create one to get started.", "books": []}
    rows = [
        {"name": b.name, "author": b.author, "pages": b.page_count,
         "description": b.description}
        for b in books
    ]
    lines = [
        f"- {b['name']}" + (f"  *by {b['author']}*" if b["author"] else "")
        + f"  — {b['pages']} page(s)"
        for b in rows
    ]
    return {"ok": True, "message": "\n".join(lines), "books": rows}


def _list_pages(ctx: ToolContext, name: str) -> dict:
    if ctx.store.get_book(name) is None:
        return {"ok": False, "message": f"No book named '{name}' exists."}
    pages = ctx.store.list_pages(name)
    if not pages:
        return {"ok": True, "message": f"Book '{name}' has no pages yet.", "pages": []}
    lines = [f"{p.order}. {p.name}" for p in pages]
    return {"ok": True, "message": "\n".join(lines), "pages": [p.name for p in pages]}


def _read_page(ctx: ToolContext, name: str, page_name: str) -> dict:
    content = ctx.store.read_page(name, page_name)
    if content is None:
        return {"ok": False, "message": f"No page '{page_name}' in book '{name}'."}
    return {"ok": True, "content": content, "page": page_name}


def _write_page(ctx: ToolContext, name: str, page_name: str, content: str) -> dict:
    page = ctx.store.write_page(name, page_name, content or "")
    ctx.notify(kind="page_written", book=name, page=page_name, order=page.order)
    return {
        "ok": True,
        "message": f"Wrote page '{page_name}' to '{name}' (page {page.order}).",
        "content": content or "",
    }


def _edit_page(ctx: ToolContext, name: str, page_name: str, operation: str,
               section: Optional[str] = None, content: Optional[str] = None,
               additions: Optional[list[str]] = None) -> dict:
    if ctx.store.read_page(name, page_name) is None:
        return {"ok": False, "message": f"No page '{page_name}' in book '{name}'."}
    if operation == "append":
        appended = append_text(ctx.store.read_page(name, page_name), additions or [], content)
        ctx.store.write_page(name, page_name, appended)
        ctx.notify(kind="page_edited", book=name, page=page_name, operation="append")
        return {"ok": True, "message": f"Appended to '{page_name}' in '{name}'."}
    if operation == "replace_section":
        if not section:
            return {"ok": False, "message": "replace_section requires a 'section' heading."}
        original = ctx.store.read_page(name, page_name) or ""
        updated = replace_section(original, section, content or "")
        ctx.store.write_page(name, page_name, updated)
        ctx.notify(kind="page_edited", book=name, page=page_name, operation="replace_section")
        return {"ok": True, "message": f"Replaced section '{section}' in '{page_name}'."}
    if operation == "update_section":
        if not section:
            return {"ok": False, "message": "update_section requires a 'section' heading."}
        original = ctx.store.read_page(name, page_name) or ""
        updated = replace_section(original, section, content or "")
        ctx.store.write_page(name, page_name, updated)
        ctx.notify(kind="page_edited", book=name, page=page_name, operation="update_section")
        return {"ok": True, "message": f"Updated section '{section}' in '{page_name}'."}
    return {"ok": False, "message": f"Unknown operation '{operation}'."}


def _delete_page(ctx: ToolContext, name: str, page_name: str) -> dict:
    if ctx.store.delete_page(name, page_name):
        ctx.notify(kind="page_deleted", book=name, page=page_name)
        return {"ok": True, "message": f"Deleted page '{page_name}' from '{name}'."}
    return {"ok": False, "message": f"No page '{page_name}' in book '{name}'."}


def _search_in_book(ctx: ToolContext, name: str, query: str) -> dict:
    if ctx.store.get_book(name) is None:
        return {"ok": False, "message": f"No book named '{name}' exists."}
    hits = ctx.store.search(name, query)
    if not hits:
        return {"ok": True, "message": f"No matches for '{query}' in '{name}'.", "hits": []}
    lines = [f"- {h['page']} (line {h['line']}): {h['snippet']}" for h in hits]
    return {"ok": True, "message": "\n".join(lines), "hits": hits}


HANDLERS: dict[str, Callable[..., dict]] = {
    reg.CREATE_BOOK: _create_book,
    reg.REMOVE_BOOK: _remove_book,
    reg.LIST_BOOKS: _list_books,
    reg.LIST_PAGES: _list_pages,
    reg.READ_PAGE: _read_page,
    reg.WRITE_PAGE: _write_page,
    reg.EDIT_PAGE: _edit_page,
    reg.DELETE_PAGE: _delete_page,
    reg.SEARCH_IN_BOOK: _search_in_book,
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
