"""Transport layer tests: unix-socket + HTTP against the in-process fake server."""

from __future__ import annotations

import threading
import time

import pytest

from app.books_store import BookStore
from app.client import Client, ChatResult
from app.config import CHAT_COMPLETIONS_PATH, Config
from app.fake_server import build_agent, start_http, start_unix
from app.tools import ToolContext
from app.transport import TransportError


def _config(spec, book_root):
    return Config(server_address=spec, book_root=str(book_root),
                  model="book-writer-agent")


@pytest.fixture()
def unix_server(tmp_path):
    socket_path = tmp_path / "agent.sock"
    server = start_unix(str(socket_path), build_agent("book-writer-agent"))
    time.sleep(0.1)
    yield f"unix:{socket_path}"
    server.close()


@pytest.fixture()
def http_server():
    server = start_http("127.0.0.1", 0, build_agent("book-writer-agent"))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.1)
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_ping_returns_models(unix_server, tmp_path):
    client = Client(_config(unix_server, tmp_path / "b"))
    assert client.ping() == ["book-writer-agent"]


def test_http_transport(http_server, tmp_path):
    client = Client(_config(http_server, tmp_path / "b"))
    assert client.ping() == ["book-writer-agent"]


def test_connection_refused_is_transport_error(tmp_path):
    client = Client(_config("unix:/tmp/definitely-not-a-socket.sock", tmp_path / "b"))
    with pytest.raises(TransportError):
        client.ping()


def test_models_endpoint_shape(unix_server, tmp_path):
    from app.config import MODELS_PATH

    client = Client(_config(unix_server, tmp_path / "b"))
    resp = client.transport.get_json(MODELS_PATH)
    data = resp.data["data"]
    assert data[0]["id"] == "book-writer-agent"


def test_client_tool_loop_creates_real_book(unix_server, tmp_path):
    """End-to-end through the unix transport: assistant tool calls execute locally."""
    root = tmp_path / "books"
    client = Client(_config(unix_server, root), tool_context=ToolContext(BookStore(root)))
    result = client.send('Create a book called "Unix Tales" with 2 pages about shells.')
    assert isinstance(result, ChatResult)
    assert result.tool_calls, "expected the agent to call tools"
    names = {e.name for e in result.tool_calls}
    assert {"create_book", "write_page"} <= names

    store = BookStore(root)
    book = store.get_book("Unix Tales")
    assert book is not None and book.page_count == 2
    assert result.final_content


def test_event_stream_reports_assistant_and_tools(unix_server, tmp_path):
    root = tmp_path / "books"
    client = Client(_config(unix_server, root), tool_context=ToolContext(BookStore(root)))
    events: list[dict] = []
    client.send("Write a one-page book called Signals.", on_event=events.append)
    kinds = [e["kind"] for e in events]
    assert ("assistant_delta" in kinds or "assistant" in kinds)
    assert "tool_call" in kinds and "tool_result" in kinds


def test_streaming_renders_tokens_live(unix_server, tmp_path):
    """Pure-chat answers stream in pieces that reassemble to the final message."""
    root = tmp_path / "books"
    client = Client(_config(unix_server, root), tool_context=ToolContext(BookStore(root)))
    events: list[dict] = []
    result = client.send("hello there", on_event=events.append)

    deltas = [e["content"] for e in events if e["kind"] == "assistant_delta"]
    assert deltas, "expected streamed content deltas"
    assert "".join(deltas) == result.final_content
    # deltas arrive before the send finishes, i.e. rendering happens on the fly
    assert all(not e["kind"] == "send_finished" for e in events)  # sentinel unused
    assert result.tool_calls == []


def test_non_streaming_fallback_emits_full_assistant(unix_server, tmp_path):
    root = tmp_path / "books"
    cfg = _config(unix_server, root)
    cfg.stream = False
    client = Client(cfg, tool_context=ToolContext(BookStore(root)))
    events: list[dict] = []
    result = client.send("hello there", on_event=events.append)
    kinds = [e["kind"] for e in events]
    assert "assistant" in kinds and "assistant_delta" not in kinds
    assert result.final_content


def test_current_book_context_message(unix_server, tmp_path):
    root = tmp_path / "books"
    client = Client(_config(unix_server, root), tool_context=ToolContext(BookStore(root)))
    client.set_current_book("My Book")
    assert client.context_messages
    assert "My Book" in client.context_messages[0]["content"]


def test_clear_resets_conversation(unix_server, tmp_path):
    root = tmp_path / "books"
    client = Client(_config(unix_server, root), tool_context=ToolContext(BookStore(root)))
    client.add_user_message("hi")
    assert client.messages
    client.clear()
    assert client.messages == []
