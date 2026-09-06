#!/usr/bin/env python3
"""Seed a sample book directly on disk, exercising the real tool dispatch path.

This drives the bundled fake *agent* straight through the client's tool dispatcher and a
real :class:`BookStore`, so it works without any network server. Handy for a quick look at
what a authored book looks like::

    python tools/demo_book.py --name "The Little Lighthouse" --pages 4
    python tools/demo_book.py --name "Demo" --pages 3 --root ./temp/demo-books
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from app.books_store import BookStore
from app.client import Client
from app.fake_server import BookAgent
from app.tools import ToolContext, dispatch


def seed_book(name: str, pages: int, root: str) -> str:
    store = BookStore(root)
    ctx = ToolContext(store)
    agent = BookAgent()

    # Drive the agent as if it were answering a user message.
    body = {"model": "book-writer-agent", "messages": [
        {"role": "user", "content": f"Create a book called {name!r} with {pages} pages."},
    ]}
    resp = agent.respond(body)
    choices = resp.get("choices", [])
    message = choices[0]["message"] if choices else {}

    written = 0
    for tc in message.get("tool_calls", []):
        fn = tc.get("function", {})
        args = json.loads(fn.get("arguments", "{}")) if fn.get("arguments") else {}
        result = dispatch(ctx, fn["name"], args)
        if result.get("ok") and fn["name"] != "create_book":
            written += 1
    return f"Book '{name}' created at {root} with {written} page(s)."


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Seed a demo book via the tool dispatcher.")
    parser.add_argument("--name", default="The Little Lighthouse")
    parser.add_argument("--pages", type=int, default=4)
    parser.add_argument("--root", default=os.path.join(tempfile.gettempdir(), "book_writer_demo"),
                        help="Folder that will hold the book (created if needed).")
    args = parser.parse_args(argv)
    print(seed_book(args.name, args.pages, args.root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
