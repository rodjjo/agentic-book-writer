"""Declarations of the tools the model may call.

These are expressed as OpenAI-compatible *function* definitions so a real OpenAI
endpoint (or the bundled fake server) can propose tool calls that the client then
executes.  The JSON-Schema ``parameters`` here are exactly what is sent to the server.
"""

from __future__ import annotations

# Stable tool names. The client maps these to implementations in :mod:`app.tools`.
CREATE_BOOK = "create_book"
REMOVE_BOOK = "remove_book"
LIST_BOOKS = "list_books"
GET_BOOK_INFO = "get_book_info"
LIST_CHAPTERS = "list_chapters"
READ_CHAPTER = "read_chapter"
WRITE_CHAPTER = "write_chapter"
EDIT_CHAPTER = "edit_chapter"
DELETE_CHAPTER = "delete_chapter"
SEARCH_IN_BOOK = "search_in_book"

# Legacy tool names (maintained for backwards compatibility)
LIST_PAGES = "list_pages"
READ_PAGE = "read_page"
WRITE_PAGE = "write_page"
EDIT_PAGE = "edit_page"
DELETE_PAGE = "delete_page"

EDIT_OPERATIONS = ["append", "replace_section", "update_section"]


def _prop(kind, description, enum=None, required=False):
    spec = {"type": kind, "description": description}
    if enum is not None:
        spec["enum"] = enum
    return spec


# Each entry is the OpenAI ``tools`` element for one function.
TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": CREATE_BOOK,
            "description": (
                "Create a brand new, empty book in a fresh folder. Use when the user asks "
                "to start, create, or begin a book (optionally with a title, author, "
                "short description, or book_id)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": _prop("string", "Human-readable title of the book.", required=True),
                    "author": _prop("string", "Author name (optional)."),
                    "description": _prop("string", "One-line description of the book (optional)."),
                    "book_id": _prop("string", "UUID4 identifier for the book (optional)."),
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": REMOVE_BOOK,
            "description": "Delete a book and all of its chapters permanently.",
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": _prop("string", "UUID of the book to remove (optional)."),
                    "name": _prop("string", "Name or UUID of the book to remove (optional)."),
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": LIST_BOOKS,
            "description": "List every book available on this computer (UUID, name, author, chapter count).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": GET_BOOK_INFO,
            "description": (
                "Get detailed information about a book including its title, author, "
                "description, total chapter count, and list of chapters with their numbers and titles."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": _prop("string", "UUID of the book.", required=True),
                    "name": _prop("string", "Name of the book (optional fallback)."),
                },
                "required": ["book_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": LIST_CHAPTERS,
            "description": "List the chapters of a book in order with their numbers and titles.",
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": _prop("string", "UUID of the book.", required=True),
                    "name": _prop("string", "Name of the book (optional fallback)."),
                },
                "required": ["book_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": READ_CHAPTER,
            "description": "Read the raw Markdown content of a chapter in a book by its chapter number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": _prop("string", "UUID of the book.", required=True),
                    "chapter_number": _prop("integer", "1-based number of the chapter to read.", required=True),
                    "name": _prop("string", "Name of the book (optional fallback)."),
                },
                "required": ["book_id", "chapter_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": WRITE_CHAPTER,
            "description": (
                "Create or overwrite a single chapter (Markdown) inside a book. "
                "Always pass book_id and chapter_number."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": _prop("string", "UUID of the book.", required=True),
                    "chapter_number": _prop("integer", "1-based number of the chapter to write.", required=True),
                    "content": _prop("string", "The full Markdown content of the chapter.", required=True),
                    "title": _prop("string", "Title for the chapter (optional)."),
                    "name": _prop("string", "Name of the book (optional fallback)."),
                },
                "required": ["book_id", "chapter_number", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": EDIT_CHAPTER,
            "description": (
                "Edit an existing chapter by chapter number. Use 'append' to add text to the end, "
                "'replace_section' to rewrite the body under a specific heading, or "
                "'update_section' to refine a heading together with its body."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": _prop("string", "UUID of the book.", required=True),
                    "chapter_number": _prop("integer", "1-based number of the chapter to edit.", required=True),
                    "operation": _prop(
                        "string",
                        "One of: append, replace_section, update_section.",
                        enum=EDIT_OPERATIONS,
                        required=True,
                    ),
                    "section": _prop(
                        "string",
                        "Heading text (without '#') for section operations. "
                        "Required for replace_section / update_section.",
                    ),
                    "content": _prop(
                        "string",
                        "New Markdown content. For replace_section/update_section this "
                        "replaces the section body; for append it is added to the end.",
                    ),
                    "additions": _prop(
                        "array",
                        "List of Markdown strings to append (operation == append).",
                        enum=None,
                    ),
                    "name": _prop("string", "Name of the book (optional fallback)."),
                },
                "required": ["book_id", "chapter_number", "operation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": DELETE_CHAPTER,
            "description": "Delete a single chapter from a book by its chapter number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": _prop("string", "UUID of the book.", required=True),
                    "chapter_number": _prop("integer", "1-based number of the chapter to delete.", required=True),
                    "name": _prop("string", "Name of the book (optional fallback)."),
                },
                "required": ["book_id", "chapter_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": SEARCH_IN_BOOK,
            "description": "Search for text across every chapter of a book and return matching snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": _prop("string", "UUID of the book (optional if name is provided)."),
                    "name": _prop("string", "Name of the book (optional if book_id is provided)."),
                    "query": _prop("string", "Text to search for.", required=True),
                },
                "required": ["query"],
            },
        },
    },
    # Legacy page tools for backwards compatibility
    {
        "type": "function",
        "function": {
            "name": LIST_PAGES,
            "description": "List the pages/chapters of a book in order.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": _prop("string", "Name or UUID of the book.", required=True),
                    "book_id": _prop("string", "UUID of the book (optional)."),
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": READ_PAGE,
            "description": "Read the raw content of a single page/chapter in a book.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": _prop("string", "Name or UUID of the book.", required=True),
                    "page_name": _prop("string", "Title or filename of the page to read.", required=True),
                    "book_id": _prop("string", "UUID of the book (optional)."),
                },
                "required": ["name", "page_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": WRITE_PAGE,
            "description": "Create or overwrite a single page/chapter inside a book.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": _prop("string", "Name or UUID of the book.", required=True),
                    "page_name": _prop("string", "Title / slug for the page.", required=True),
                    "content": _prop("string", "The full Markdown content of the page.", required=True),
                    "book_id": _prop("string", "UUID of the book (optional)."),
                },
                "required": ["name", "page_name", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": EDIT_PAGE,
            "description": "Edit an existing page/chapter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": _prop("string", "Name or UUID of the book.", required=True),
                    "page_name": _prop("string", "Title of the page to edit.", required=True),
                    "operation": _prop(
                        "string",
                        "One of: append, replace_section, update_section.",
                        enum=EDIT_OPERATIONS,
                        required=True,
                    ),
                    "section": _prop("string", "Heading text for section operations."),
                    "content": _prop("string", "New Markdown content."),
                    "additions": _prop("array", "List of Markdown strings to append."),
                    "book_id": _prop("string", "UUID of the book (optional)."),
                },
                "required": ["name", "page_name", "operation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": DELETE_PAGE,
            "description": "Delete a single page/chapter from a book.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": _prop("string", "Name or UUID of the book.", required=True),
                    "page_name": _prop("string", "Title or filename of the page to delete.", required=True),
                    "book_id": _prop("string", "UUID of the book (optional)."),
                },
                "required": ["name", "page_name"],
            },
        },
    },
]

# Fast name -> schema lookup.
BY_NAME: dict[str, dict] = {t["function"]["name"]: t["function"] for t in TOOLS}


def tool_schemas() -> list[dict]:
    """Return the tools array in the exact shape expected by OpenAI-compatible APIs."""
    return [dict(t) for t in TOOLS]
