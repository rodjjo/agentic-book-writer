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
LIST_PAGES = "list_pages"
READ_PAGE = "read_page"
WRITE_PAGE = "write_page"
EDIT_PAGE = "edit_page"
DELETE_PAGE = "delete_page"
SEARCH_IN_BOOK = "search_in_book"

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
                "to start, create, or begin a book (optionally with a title, author and "
                "short description)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": _prop("string", "Human-readable title of the book.", required=True),
                    "author": _prop("string", "Author name (optional)."),
                    "description": _prop("string", "One-line description of the book (optional)."),
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": REMOVE_BOOK,
            "description": "Delete a book and all of its pages permanently.",
            "parameters": {
                "type": "object",
                "properties": {"name": _prop("string", "Name of the book to remove.", required=True)},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": LIST_BOOKS,
            "description": "List every book available on this computer (name, author, page count).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": LIST_PAGES,
            "description": "List the pages of a book in order, useful before writing or editing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": _prop("string", "Name of the book.", required=True)
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": READ_PAGE,
            "description": "Read the raw content of a single page in a book.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": _prop("string", "Name of the book.", required=True),
                    "page_name": _prop("string", "Title of the page to read.", required=True),
                },
                "required": ["name", "page_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": WRITE_PAGE,
            "description": (
                "Create or overwrite a single page (Markdown) inside a book. Use this to "
                "write a whole page from scratch, add a new chapter, or replace a page's "
                "content. Prefer this over append for fresh pages."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": _prop("string", "Name of the book.", required=True),
                    "page_name": _prop("string", "Title / slug for the page.", required=True),
                    "content": _prop(
                        "string",
                        "The full Markdown content of the page.",
                        required=True,
                    ),
                },
                "required": ["name", "page_name", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": EDIT_PAGE,
            "description": (
                "Edit an existing page. Use 'append' to add text to the end, "
                "'replace_section' to rewrite the body under a specific heading, or "
                "'update_section' to refine a heading together with its body."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": _prop("string", "Name of the book.", required=True),
                    "page_name": _prop("string", "Title of the page to edit.", required=True),
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
                },
                "required": ["name", "page_name", "operation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": DELETE_PAGE,
            "description": "Delete a single page from a book.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": _prop("string", "Name of the book.", required=True),
                    "page_name": _prop("string", "Title of the page to delete.", required=True),
                },
                "required": ["name", "page_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": SEARCH_IN_BOOK,
            "description": "Search for text across every page of a book and return matching snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": _prop("string", "Name of the book.", required=True),
                    "query": _prop("string", "Text to search for.", required=True),
                },
                "required": ["name", "query"],
            },
        },
    },
]

# Fast name -> schema lookup.
BY_NAME: dict[str, dict] = {t["function"]["name"]: t["function"] for t in TOOLS}


def tool_schemas() -> list[dict]:
    """Return the tools array in the exact shape expected by OpenAI-compatible APIs."""
    return [dict(t) for t in TOOLS]
