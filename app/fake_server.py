"""A small, self-contained OpenAI-compatible server that behaves like an agent.

It is **not** a real model. Instead it inspects the conversation and decides — with a
handful of heuristics — whether to *call a tool* (to author a book) or simply *answer*.
The tools it calls are executed locally by the client, exactly as a real OpenAI-compatible
endpoint would drive them.

Run it directly (in a tmux window, as the task asks) with its CLI:

    python tools/run_server.py --transport unix --socket /tmp/book_writer.sock
    python tools/run_server.py --transport http --port 8000
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

from . import model_registry as reg

SERVICE_NAME = "book-writer-fake-server"
SERVICE_VERSION = "0.1.0"


# --------------------------------------------------------------------------
# Content generator (the "creative" part of the mock model)
# --------------------------------------------------------------------------

_CHAPTER_TITLES = [
    "The Beginning", "An Unexpected Turn", "Into the Unknown",
    "Friends New and Old", "The Trial", "A Hidden Truth",
    "The Long Road Home", "What Matters Most", "A Brighter Morning",
    "The Return", "A Quiet Wonder", "Where Stories Begin",
]

_PARAGRAPH_TEMPLATES = [
    "Once upon a time, in a place both ordinary and magical, {title} began to take shape.",
    "{title} was not like the stories everyone had been told — it kept a quiet wonder of "
    "its own.",
    "The morning light spilled across {title}, and something in the air whispered that a "
    "great adventure was about to begin.",
    "As the days passed, {title} taught its friends that courage is not the absence of "
    " fear, but the choice to keep going anyway.",
    "By the time {title} reached its turning point, even the oldest of listeners held "
    "their breath.",
    "And so {title} reminded everyone that the smallest acts of kindness can change the "
    "whole course of a story.",
]


def generate_book(title: str, page_count: int) -> list[tuple[str, str]]:
    """Return ``(page_title, markdown)`` pairs describing a whole book."""
    title = (title or "Untitled Book").strip()
    rng = random.Random(abs(hash(title)) % (2 ** 32))
    page_count = max(1, min(int(page_count) or 1, 24))

    pages: list[tuple[str, str]] = []
    for i in range(page_count):
        chapter = _CHAPTER_TITLES[i % len(_CHAPTER_TITLES)]
        heading = f"{i + 1}. {chapter}"
        paras = []
        if i == 0:
            paras.append(_PARAGRAPH_TEMPLATES[0].format(title=title))
            paras.append(_PARAGRAPH_TEMPLATES[1].format(title=title))
        elif i == page_count - 1:
            paras.append(_PARAGRAPH_TEMPLATES[5].format(title=title))
            paras.append(_PARAGRAPH_TEMPLATES[4].format(title=title))
        else:
            picks = rng.sample(_PARAGRAPH_TEMPLATES[2:5], k=min(3, 3))
            paras.extend(picks)
        md = f"# {heading}\n\n" + "\n\n".join(paras)
        pages.append((chapter, md))
    return pages


# --------------------------------------------------------------------------
# Text extraction helpers
# --------------------------------------------------------------------------

def _quoted(text: str) -> Optional[str]:
    for pat in (r'"([^"]+)"', r"'([^']+)'"):
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    return None


def _extract_book_name(text: str, fallback: str = "") -> str:
    q = _quoted(text)
    if q:
        return q
    for pat in (
        r'book(?:\s+(?:named|called|titled|is|:))?\s*"?([A-Za-z0-9][A-Za-z0-9 \'\-]{1,40})',
        r'called\s+"?([A-Za-z0-9][A-Za-z0-9 \'\-]{1,40})',
    ):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return fallback or "Untitled Book"


def _explicit_book_name(text: str) -> str:
    """Return a book name the user *explicitly* pointed at.

    Unlike :func:`_extract_book_name`, this ignores incidental quotes (e.g. the search
    query in ``search for "lighthouse"``) and only matches phrases that clearly refer to a
    book: ``book X``, ``in book X``, ``called X`` or ``book named X``.
    """
    for pat in (
        r'book(?:\s+(?:named|called|titled|is):?)\s*"?([A-Za-z0-9][A-Za-z0-9 \'\-]{1,40})',
        r'in\s+book\s+"?([A-Za-z0-9][A-Za-z0-9 \'\-]{1,40})',
        r'called\s+"?([A-Za-z0-9][A-Za-z0-9 \'\-]{1,40})',
    ):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def _extract_page_name(text: str) -> str:
    q = _quoted(text)
    if q:
        return q
    m = re.search(r'(?:page|chapter)\s+(?:named|called|titled|about|:)?\s*"?([A-Za-z0-9][A-Za-z ]{2,40})',
                  text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r'(?:page|chapter)\s+(\d+)', text, re.IGNORECASE)
    if m:
        return f"Page {m.group(1)}"
    return "Untitled Page"


def _extract_chapter_number(text: str, default: Optional[int] = 1) -> Optional[int]:
    m = re.search(r'(?:chapter|page)\s+(\d+)', text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return default


def _extract_page_count(text: str, default: int = 6) -> int:
    m = re.search(r'(\d+)\s+(?:pages?|chapters?)', text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r'(?:with|of|about)\s+(\d+)', text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r'(\d+)', text)
    if m:
        return int(m.group(1))
    return default


def _extract_search_query(text: str) -> str:
    m = re.search(r'(?:search|find)\s+(.+?)\s+(?:in|inside)\s+(?:the )?book', text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r'(?:search|find)\s+"?([^"]+?)",?\s*(?:in|in the|inside)', text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text.strip()


def _has_image(messages: list[dict]) -> bool:
    for m in messages:
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "image" in json.dumps(part).lower():
                    return True
    return False


# --------------------------------------------------------------------------
# The agent
# --------------------------------------------------------------------------

def _tool_call(name: str, arguments: dict, idx: int) -> dict:
    return {
        "id": f"call_{idx}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }


class BookAgent:
    """Decides tool calls or plain answers from the conversation."""

    def __init__(self, model: str = "book-writer-agent", stream_delay: float = 0.004):
        self.model = model
        # artificial per-chunk delay so streaming is visible in the GUI
        self.stream_delay = stream_delay

    # -- public -----------------------------------------------------------
    def respond(self, body: dict) -> dict:
        plan = self._plan(body)
        return self._assistant_message(plan)

    def _plan(self, body: dict) -> dict:
        """Decide the assistant turn: either plain content or tool calls."""
        messages = body.get("messages", []) or []
        # The client drives the loop; when the last message is a tool result we must
        # produce the final assistant answer. Otherwise, decide from the user's text.
        last_role = messages[-1].get("role") if messages else None
        if last_role == "tool":
            # Only summarise the tool results from *this* turn (trailing tool messages that
            # follow the most recent assistant response) so counts don't accumulate across
            # turns of the running conversation.
            turn_tools = self._current_turn_tool_results(messages)
            return {"content": self._final_answer(turn_tools)}

        latest = self._latest_user_text(messages)
        text = latest.get("text", "")
        image = latest.get("image")

        call = self._choose_tools(text, messages)
        if call:
            return {"tool_calls": call}
        current_book, current_book_id = self._current_book_from_messages(messages)
        return {"content": self._chat_answer(text, image, current_book, current_book_id)}

    def stream_response(self, body: dict):
        """Yield OpenAI ``chat.completion.chunk`` SSE events for one assistant turn."""
        import time as _time

        plan = self._plan(body)
        delay = self.stream_delay

        def chunk_text(value: str, size: int = 12):
            for i in range(0, max(1, len(value)), size):
                yield value[i:i + size]

        # kick off with the role
        yield self._chunk({"role": "assistant", "content": ""})
        content = plan.get("content")
        tool_calls = plan.get("tool_calls") or []

        if content:
            for piece in chunk_text(content):
                if delay:
                    _time.sleep(delay)
                yield self._chunk({"content": piece})

        for index, call in enumerate(tool_calls):
            fn = call.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments", "") or ""
            # first delta announces the call id + function name
            if delay:
                _time.sleep(delay)
            yield self._chunk({"tool_calls": [
                {"index": index, "id": call.get("id", f"call_stream_{index}"),
                 "type": "function",
                 "function": {"name": name, "arguments": ""}}
            ]})
            # then the JSON arguments trickle in
            for piece in chunk_text(args, size=28):
                if delay:
                    _time.sleep(delay)
                yield self._chunk({"tool_calls": [
                    {"index": index, "function": {"arguments": piece}}
                ]})

        finish = "stop" if content else "tool_calls"
        yield self._chunk({}, finish=finish)

    def _chunk(self, delta: dict, finish: Optional[str] = None) -> dict:
        """One OpenAI SSE payload."""
        return {
            "id": "chatcmpl-stream",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": self.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _current_book_from_messages(messages: list[dict]) -> tuple[str, str]:
        """Return the (current_book_name, current_book_id) carried in a system context message.

        The client sends a system message like:
          *The user is currently working on the book named "Sea Stories" with ID "..."...*
        This lets tool calls stay tied to the selected book even when the user's phrasing
        ("add a page about the lighthouse") omits the book name entirely.
        """
        name = ""
        book_id = ""
        for m in messages:
            if m.get("role") != "system":
                continue
            text = m.get("content") or ""
            id_match = re.search(r'ID\s+"([^"]+)"', text, re.IGNORECASE) or re.search(r'book_id:\s*"?([a-f0-9\-]{36})"?', text, re.IGNORECASE)
            if id_match:
                book_id = id_match.group(1).strip()
            name_match = re.search(r'named\s+"(.*?)"', text, re.IGNORECASE)
            if name_match:
                name = name_match.group(1).strip()
            elif not name:
                quoted = re.search(r'"(.*?)"', text)
                if quoted:
                    name = quoted.group(1).strip()
        return name, book_id

    @staticmethod
    def _current_turn_tool_results(messages: list[dict]) -> list[dict]:
        """Return the tool results produced during the current turn.

        These are the ``tool`` messages that trail the most recent ``assistant`` message;
        everything earlier belongs to previous turns of the running conversation.
        """
        last_ai = -1
        for i, m in enumerate(messages):
            if m.get("role") == "assistant":
                last_ai = i
        return [m for i, m in enumerate(messages) if i > last_ai and m.get("role") == "tool"]

    @staticmethod
    def _latest_user_text(messages: list[dict]) -> dict:
        """Return {"text": str, "image": bool} from the most recent user message."""
        for m in reversed(messages):
            if m.get("role") != "user":
                continue
            content = m.get("content")
            if isinstance(content, list):
                text_parts = [p.get("text", "") for p in content
                              if isinstance(p, dict) and p.get("type") == "text"]
                has_image = any(
                    isinstance(p, dict) and "image" in json.dumps(p).lower() for p in content
                )
                return {"text": " ".join(text_parts).strip(), "image": has_image}
            return {"text": str(content or ""), "image": False}
        return {"text": "", "image": False}

    def _choose_tools(self, text: str, messages: list[dict]) -> list[dict]:
        low = text.lower()
        calls: list[dict] = []
        current_book, current_book_id = self._current_book_from_messages(messages)

        def pick_book() -> tuple[str, str]:
            explicit = _explicit_book_name(text)
            if explicit:
                if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', explicit, re.I):
                    return explicit, explicit
                return explicit, (current_book_id if explicit == current_book else "")
            name = current_book if current_book else _extract_book_name(text)
            b_id = current_book_id if name == current_book else ""
            return name, b_id

        # whole-book generation
        if any(k in low for k in ("create a book", "new book", "start a book", "write a book",
                                  "make a book", "begin a book")):
            name, book_id = pick_book()
            count = _extract_page_count(text)
            book_id = book_id or str(uuid.uuid4())
            calls.append(_tool_call(reg.CREATE_BOOK, {"name": name, "book_id": book_id}, len(calls)))
            for i, (_title, md) in enumerate(generate_book(name, count), start=1):
                args = {"book_id": book_id, "chapter_number": i, "title": _title, "content": md, "name": name}
                calls.append(_tool_call(reg.WRITE_CHAPTER, args, len(calls)))
            return calls

        if any(k in low for k in ("list books", "what books", "show books", "books available")):
            return [_tool_call(reg.LIST_BOOKS, {}, 0)]

        if any(k in low for k in ("get book info", "book info", "info about book", "about book")):
            name, book_id = pick_book()
            args = {"book_id": book_id or name, "name": name}
            return [_tool_call(reg.GET_BOOK_INFO, args, 0)]

        if any(k in low for k in ("list chapters", "chapters of", "table of contents", "list pages", "pages of")):
            name, book_id = pick_book()
            args = {"book_id": book_id or name, "name": name}
            return [_tool_call(reg.GET_BOOK_INFO, args, 0)]

        if any(k in low for k in ("read chapter", "open chapter", "show chapter", "view chapter",
                                  "read page", "open page", "show page", "view page")):
            name, book_id = pick_book()
            ch_num = _extract_chapter_number(text)
            args = {"book_id": book_id or name, "chapter_number": ch_num, "name": name}
            return [_tool_call(reg.READ_CHAPTER, args, 0)]

        if any(k in low for k in ("delete chapter", "remove chapter", "delete page", "remove page")):
            name, book_id = pick_book()
            ch_num = _extract_chapter_number(text)
            args = {"book_id": book_id or name, "chapter_number": ch_num, "name": name}
            return [_tool_call(reg.DELETE_CHAPTER, args, 0)]

        if any(k in low for k in ("search", "find ")):
            name, book_id = pick_book()
            query = _extract_search_query(text)
            args = {"query": query}
            if book_id:
                args["book_id"] = book_id
            if name:
                args["name"] = name
            return [_tool_call(reg.SEARCH_IN_BOOK, args, 0)]

        if any(k in low for k in ("remove book", "delete book")):
            name, book_id = pick_book()
            args = {"name": name}
            if book_id:
                args["book_id"] = book_id
            return [_tool_call(reg.REMOVE_BOOK, args, 0)]

        if any(k in low for k in ("write a chapter", "add a chapter", "add chapter", "write chapter", "new chapter", "next chapter", "chapter ",
                                  "write a page", "add a page", "new page", "next page", "page ")):
            name, book_id = pick_book()
            ch_num = _extract_chapter_number(text, default=None)
            page = _extract_page_name(text)
            _title, md = generate_book(name, 1)[0]
            # Prefer the requested chapter title.
            md = f"# {page}\n\n" + _PARAGRAPH_TEMPLATES[2].format(title=page) + "\n\n" \
                 + _PARAGRAPH_TEMPLATES[3].format(title=page) + "\n\n" + _PARAGRAPH_TEMPLATES[4].format(title=page) + "\n"
            args = {"book_id": book_id or name, "chapter_number": ch_num if ch_num is not None else "next", "title": page, "content": md, "name": name}
            return [_tool_call(reg.WRITE_CHAPTER, args, 0)]

        if any(k in low for k in ("edit", "revise", "change", "improve", "rewrite", "fix",
                                  "make it", "add to", "append")):
            name, book_id = pick_book()
            ch_num = _extract_chapter_number(text)
            page = _extract_page_name(text)
            args = {
                "book_id": book_id or name, "chapter_number": ch_num, "operation": "append",
                "additions": [_PARAGRAPH_TEMPLATES[5].format(title=page)],
                "name": name,
            }
            return [_tool_call(reg.EDIT_CHAPTER, args, 0)]

        return []  # plain chat

    # -- answers ----------------------------------------------------------
    @staticmethod
    def _assistant_message(payload) -> dict:
        if isinstance(payload, str):
            payload = {"content": payload}
        message = {"role": "assistant", "content": None}
        if "tool_calls" in payload:
            message["tool_calls"] = payload["tool_calls"]
        else:
            message["content"] = payload["content"]
        return {"model": None, "choices": [{"index": 0, "message": message,
                                            "finish_reason": "tool_calls" if "tool_calls" in payload
                                            else "stop"}]}

    def _chat_answer(self, text: str, image: bool, current_book: str = "", current_book_id: str = "") -> str:
        if not text.strip():
            return "Hi! I'm your book-writing assistant. Tell me what book you'd like and " \
                   "I'll author it page by page, or ask me to edit and search your books."
        note = ""
        if image:
            note = " _(I can see the image you attached — I'll keep it in mind!)_  "
        lower = text.lower()
        if any(w in lower for w in ("current book", "which book", "what book", "book id", "uuid", "working on")):
            if current_book:
                id_str = f" with ID {current_book_id}" if current_book_id else ""
                return f"You are currently working on \"{current_book}\"{id_str}."
            return "No book is currently selected."
        if any(w in lower for w in ("hello", "hi ", "hey", "good morning", "good evening")):
            return f"Hello{note} What shall we write today?"
        if any(w in lower for w in ("thank", "thanks", "cheers")):
            return "You're welcome{note} Just say the word when you want to write, edit or " \
                   "search a book.".format(note=" " + note if note else "")
        if "?" in text:
            return f"Great question! {note} Ask me to create a book, add pages, edit " \
                   "existing ones, or search for text, and I'll take care of it."
        return (
            "Got it{note} Tell me a bit more — for instance: "
            '"Create a book called "The Wandering Star" with five pages."'
        ).format(note=note)

    def _final_answer(self, tool_results: list[dict]) -> str:
        """Summarise the tool results that were just executed into a friendly answer."""
        wrote_pages = 0
        created = None
        created_id = None
        removed = None
        preview: Optional[str] = None

        for m in tool_results:
            content = m.get("content") or "{}"
            try:
                res = json.loads(content)
            except json.JSONDecodeError:
                continue
            name = m.get("name", "")
            if name == reg.CREATE_BOOK and res.get("ok"):
                book_data = res.get("book", {})
                created = book_data.get("name") or res.get("book")
                created_id = book_data.get("id") or res.get("book_id")
            elif name == reg.REMOVE_BOOK and res.get("ok"):
                removed = res.get("book")
            elif name in (reg.WRITE_CHAPTER, reg.WRITE_PAGE) and res.get("ok"):
                wrote_pages += 1
                if preview is None and res.get("content"):
                    preview = res["content"].strip().splitlines()
            elif name in (reg.EDIT_CHAPTER, reg.EDIT_PAGE) and res.get("ok"):
                if preview is None:
                    preview = [f":pencil: Edited chapter → {res.get('message', '')}"]
            elif name == reg.SEARCH_IN_BOOK and res.get("message"):
                if preview is None:
                    preview = [res["message"]]
            elif name == reg.LIST_BOOKS and res.get("message"):
                if preview is None:
                    preview = [res["message"]]
            elif name in (reg.GET_BOOK_INFO, reg.LIST_CHAPTERS, reg.LIST_PAGES) and res.get("message"):
                if preview is None:
                    preview = [res["message"]]
            elif name in (reg.READ_CHAPTER, reg.READ_PAGE) and res.get("content"):
                if preview is None:
                    preview = [f"> {res['content'][:200].strip()}"]
            elif name in (reg.DELETE_CHAPTER, reg.DELETE_PAGE) and res.get("ok"):
                if preview is None:
                    preview = [res.get("message", "Deleted chapter.")]

        parts = []
        if created:
            id_info = f" (ID: {created_id})" if created_id else ""
            parts.append(f"Created the book **{created}**{id_info}.")
        if wrote_pages:
            line = preview[wrote_pages - 1] if wrote_pages and preview else ""
            parts.append(f"Wrote {wrote_pages} chapter(s).")
            if preview:
                first = "\n".join(preview[:1]) if isinstance(preview[0], list) else preview[0]
                parts.append(f"\n_{first[:280]}_")
        if removed:
            parts.append(f"Removed the book **{removed}**.")
        if created and wrote_pages:
            id_info = f" (ID: {created_id})" if created_id else ""
            parts.append(f"\nYour book **{created}**{id_info} is ready — open the **Books** tab to "
                         "read it. I can keep adding chapters, edit wording, or search "
                         "through the pages whenever you like.")
        if not parts:
            return "Done — the changes were applied to your books. Is there anything else " \
                   "you'd like me to write or fix?"
        return " ".join(parts)


def handle_chat_completion(agent: BookAgent, body: dict) -> dict:
    return agent.respond(body or {})


def handle_models(agent: BookAgent) -> dict:
    return {
        "object": "list",
        "data": [
            {"id": agent.model, "object": "model", "owned_by": SERVICE_NAME,
             "created": 0, "context_window": 200_000,
             "description": "Vision + tool-calling book-writing agent (mock)."}
        ],
    }


def route(path: str, method: str, agent: BookAgent, body: Optional[dict] = None):
    """Dispatch a single request to the right handler (shared by both transports)."""
    norm = urlparse(path).path
    if method == "GET" and (norm == "/v1/models" or norm.startswith("/v1/models")):
        return 200, handle_models(agent)
    if method == "GET" and norm in ("/healthz", "/health"):
        return 200, {"status": "ok", "service": SERVICE_NAME, "version": SERVICE_VERSION}
    if method == "GET" and norm in ("/", "/v1"):
        return 200, {"service": SERVICE_NAME,
                     "endpoints": ["/v1/chat/completions", "/v1/models", "/healthz"]}
    if method == "POST" and (norm == "/v1/chat/completions" or norm.startswith("/v1/chat/completions")):
        return 200, handle_chat_completion(agent, body or {})
    return 404, {"error": {"message": f"no route for {method} {norm}", "type": "invalid_request_error"}}


# --------------------------------------------------------------------------
# Servers
# --------------------------------------------------------------------------


def _is_chat_completions(path: str) -> bool:
    from urllib.parse import urlparse as _up
    norm = _up(path).path
    return norm == "/v1/chat/completions" or norm.startswith("/v1/chat/completions")


def _write_stream(emit, agent: BookAgent, body: dict) -> None:
    """Write the SSE body of a streaming chat completion through ``emit``."""
    try:
        for event in agent.stream_response(body):
            emit(b"data: " + json.dumps(event).encode("utf-8") + b"\r\n\r\n")
        emit(b"data: [DONE]\r\n\r\n")
    except Exception:  # pragma: no cover - never kill the connection
        pass


class _HTTPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "BookWriterFakeServer/0.1"

    def _send(self, status: int, obj: dict) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):  # pragma: no cover
            pass

    def do_GET(self) -> None:  # noqa: N802
        status, obj = route(self.path, "GET", self.server.fake)
        self._send(status, obj)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}
        if body.get("stream") and _is_chat_completions(self.path):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            def emit(chunk: bytes) -> None:
                self.wfile.write(chunk)
                self.wfile.flush()

            _write_stream(emit, self.server.fake, body)
            return
        status, obj = route(self.path, "POST", self.server.fake, body)
        self._send(status, obj)

    def log_message(self, *args) -> None:  # keep the tmux window quiet
        pass


def start_http(host: str, port: int, agent: BookAgent) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), _HTTPHandler)
    httpd.fake = agent
    httpd.daemon_threads = True
    return httpd


def _read_headers(conn: socket.socket) -> tuple[bytes, Optional[int], bytes]:
    """Read the request line + headers. Returns (head_bytes, content_length, leftover_body)."""
    request = b""
    while b"\r\n\r\n" not in request:
        chunk = conn.recv(4096)
        if not chunk:
            return request, 0, b""
        request += chunk
    head, _, leftover = request.partition(b"\r\n\r\n")
    length = 0
    for header in head.decode("utf-8", "replace").split("\r\n")[1:]:
        if header.lower().startswith("content-length:"):
            try:
                length = int(header.split(":", 1)[1].strip())
            except ValueError:
                length = 0
    return head, length, leftover


def _read_body(conn: socket.socket, need: int, have: bytes) -> bytes:
    """Read ``need`` body bytes, already counting ``have`` bytes (Connection: close)."""
    body = have
    while len(body) < need:
        chunk = conn.recv(65536 - len(body))
        if not chunk:
            break
        body += chunk
    return body[:need]


def _serve_one_connection(conn: socket.socket, agent: BookAgent) -> None:
    try:
        conn.settimeout(30)
        head, length, leftover = _read_headers(conn)
        body_bytes = _read_body(conn, length, leftover) if length else leftover
        lines = head.decode("utf-8", "replace").split("\r\n")
        request_line = lines[0].split(" ")
        method = request_line[0] if len(request_line) > 0 else "GET"
        path = request_line[1] if len(request_line) > 1 else "/"
        try:
            body = json.loads(body_bytes) if body_bytes else {}
        except json.JSONDecodeError:
            body = {}
        if body.get("stream") and method.upper() == "POST" and _is_chat_completions(path):
            conn.sendall(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/event-stream\r\n"
                b"Cache-Control: no-cache\r\n"
                b"Connection: close\r\n\r\n")

            def emit(chunk: bytes) -> None:
                conn.sendall(chunk)

            _write_stream(emit, agent, body)
            return
        status, obj = route(path, method, agent, body)
        data = json.dumps(obj).encode("utf-8")
        reason = "OK" if 200 <= status < 300 else "Error"
        response = (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(data)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("utf-8") + data
        conn.sendall(response)
    except Exception:  # pragma: no cover - defensive
        pass
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover
            pass


def start_unix(path: str, agent: BookAgent) -> socket.socket:
    """Start a threaded unix-socket server in a background thread. Returns the socket."""
    parent = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    parent.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        if os.path.exists(str(path)):
            os.unlink(str(path))
    except Exception:  # pragma: no cover
        pass
    parent.bind(path)
    parent.listen(64)

    def accept_loop() -> None:
        while True:
            try:
                conn, _ = parent.accept()
            except OSError:  # pragma: no cover - server shutting down
                break
            threading.Thread(target=_serve_one_connection, args=(conn, agent), daemon=True).start()

    threading.Thread(target=accept_loop, daemon=True).start()
    return parent


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_agent(model: str) -> BookAgent:
    return BookAgent(model=model)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Book Writer fake OpenAI-compatible server.")
    parser.add_argument("--transport", choices=["http", "unix"], default="unix")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--socket", default="/tmp/book_writer.sock")
    parser.add_argument("--model", default="book-writer-agent")
    args = parser.parse_args(argv)

    agent = build_agent(args.model)
    if args.transport == "http":
        httpd = start_http(args.host, args.port, agent)
        print(f"[{SERVICE_NAME}] HTTP server listening on http://{args.host}:{args.port}")
        print(f"[{SERVICE_NAME}] endpoints: /v1/chat/completions, /v1/models, /healthz")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:  # pragma: no cover
            httpd.shutdown()
    else:
        sock = start_unix(args.socket, agent)
        print(f"[{SERVICE_NAME}] unix server listening on unix://{args.socket}")
        print(f"[{SERVICE_NAME}] endpoints: /v1/chat/completions, /v1/models, /healthz")
        try:
            while True:
                import time as _time

                _time.sleep(1)
        except KeyboardInterrupt:  # pragma: no cover
            pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
