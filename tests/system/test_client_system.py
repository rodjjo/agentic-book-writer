"""SYSTEM TESTS: drive the real client against the fake OpenAI-compatible server.

The fake server runs as a **subprocess** (exactly like a remote server on another
computer), the client talks to it over a unix domain socket (or plain HTTP) and the
tool calls are executed locally on the client's disk.  Run with:

    pytest -m system
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.system

REPO = Path(__file__).resolve().parent.parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(params=["unix", "http"])
def server_address(request, tmp_path):
    """Start the fake server as a subprocess on the requested transport."""
    if request.param == "unix":
        sock = tmp_path / "agent.sock"
        cmd = ["--transport", "unix", "--socket", str(sock), "--model", "book-writer-agent"]
        addr = f"unix:{sock}"
    else:
        port = _free_port()
        cmd = ["--transport", "http", "--host", "127.0.0.1",
               "--port", str(port), "--model", "book-writer-agent"]
        addr = f"http://127.0.0.1:{port}"

    proc = subprocess.Popen([sys.executable, "-m", "app.fake_server", *cmd],
                            cwd=str(REPO), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    yield addr, tmp_path / "books", proc
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover
        proc.kill()
        proc.wait(timeout=5)


def _client(addr, book_root):
    from app.books_store import BookStore
    from app.client import Client
    from app.config import Config
    from app.tools import ToolContext

    cfg = Config(server_address=addr, book_root=str(book_root), model="book-writer-agent")
    store = BookStore(book_root)
    return Client(cfg, tool_context=ToolContext(store)), store


def _wait_ready(addr, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            from app.client import Client
            from app.config import Config

            client = Client(Config(server_address=addr, model="book-writer-agent"))
            client.ping()
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"server at {addr} never became reachable")


def test_models_endpoint(server_address):
    addr, _root, _proc = server_address
    _wait_ready(addr)
    client, _ = _client(addr, _root)
    assert client.ping() == ["book-writer-agent"]


def test_author_a_full_book(server_address):
    """The killer path: user asks for a book; agent tool-calls; pages land on disk."""
    addr, book_root, _proc = server_address
    _wait_ready(addr)
    client, store = _client(addr, book_root)

    result = client.send('Create a book called "The Silver Kite" '
                         "with 3 pages about flying kites.")

    names = {e.name for e in result.tool_calls}
    assert "create_book" in names and "write_page" in names
    assert result.final_content

    book = store.get_book("The Silver Kite")
    assert book is not None
    assert book.page_count == 3

    page_names = {p.name for p in store.list_pages("The Silver Kite")}
    assert len(page_names) == 3
    contents = [store.read_page("The Silver Kite", p.name) or "" for p in book.pages]
    assert all("#" in c and c.strip() for c in contents)

    # nothing was persisted server-side: only the client folder holds the book
    assert (book_root / "the-silver-kite").exists()
    assert len(list((book_root / "the-silver-kite" / "pages").glob("*.md"))) == 3


def test_edit_workflow_after_selection(server_address):
    """Once a book is selected the model may append, search and delete via tools."""
    addr, book_root, _proc = server_address
    _wait_ready(addr)
    client, store = _client(addr, book_root)

    client.send('Create a book called "Field Notes" with 1 page about rivers.')
    book = store.get_book("Field Notes")
    assert book and book.page_count == 1

    # selecting the book tells the agent where its tool calls must land
    client.set_current_book("Field Notes")
    res = client.send("Add a page about mountains to Field Notes.")
    names = {e.name for e in res.tool_calls}
    assert "write_page" in names
    assert store.get_book("Field Notes").page_count >= 2

    # a later instruction can search inside the selected book: use a real word
    # from the written page so the query is guaranteed to match.
    page0 = store.list_pages("Field Notes")[0]
    content = store.read_page("Field Notes", page0.name) or ""
    word = next((w.strip(".,;:!?()\"'") for w in content.split()
                 if len(w.strip(".,;:!?()\"'")) >= 5), "river")
    res = client.send(f'Search "{word}" in the current book.')
    hits = None
    for e in res.tool_calls:
        if e.name == "search_in_book":
            hits = e.result
    assert hits is not None and hits.get("hits")

    # cleanup via tool
    client.send('Remove book "Field Notes".')
    assert store.get_book("Field Notes") is None


def test_missing_server_is_a_clean_error(tmp_path):
    from app.books_store import BookStore
    from app.client import Client
    from app.config import Config
    from app.tools import ToolContext
    from app.transport import TransportError

    cfg = Config(server_address="unix:/tmp/nonexistent-book-writer.sock",
                 book_root=str(tmp_path / "b"), model="x")
    client = Client(cfg, tool_context=ToolContext(BookStore(cfg.book_root)))
    with pytest.raises(TransportError):
        client.ping()


def test_tool_events_fired(server_address):
    addr, book_root, _proc = server_address
    _wait_ready(addr)
    client, _ = _client(addr, book_root)
    events: list[dict] = []
    client.send("Write a one page book called Events.", on_event=events.append)
    kinds = {e["kind"] for e in events}
    assert ("assistant_delta" in kinds or "assistant" in kinds)
    assert {"tool_call", "tool_result"} <= kinds
