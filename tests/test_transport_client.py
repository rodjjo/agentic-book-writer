"""Transport layer tests: unix-socket + HTTP against the in-process fake server."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
    assert "create_book" in names and ("write_chapter" in names or "write_page" in names)

    store = BookStore(root)
    book = store.get_book("Unix Tales")
    assert book is not None and (book.chapter_count == 2 or book.page_count == 2)
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


def test_current_book_context_with_uuid(unix_server, tmp_path):
    root = tmp_path / "books"
    store = BookStore(root)
    book = store.create_book("Context Book")
    client = Client(_config(unix_server, root), tool_context=ToolContext(store))
    client.set_current_book("Context Book")
    assert client.context_messages
    content = client.context_messages[0]["content"]
    assert "Context Book" in content
    assert book.id in content

    # Server identifies the book and its UUID
    res = client.send("What book am I working on?")
    assert res.final_content
    assert "Context Book" in res.final_content
    assert book.id in res.final_content


def test_system_prompt_and_book_custom_instruction(unix_server, tmp_path):
    root = tmp_path / "books"
    store = BookStore(root)
    book = store.create_book("Mystery Book", custom_instruction="Use 1920s slang.")
    cfg = _config(unix_server, root)
    cfg.system_prompt = "You are an award-winning novelist."
    client = Client(cfg, tool_context=ToolContext(store))
    client.set_current_book("Mystery Book")

    assert client.context_messages
    ctx = client.context_messages[0]["content"]
    assert "You are an award-winning novelist." in ctx
    assert "Use 1920s slang." in ctx
    assert "Mystery Book" in ctx
    assert book.id in ctx

    # Dynamically update system prompt
    client.update_system_prompt("You are a sci-fi author.")
    ctx2 = client.context_messages[0]["content"]
    assert "You are a sci-fi author." in ctx2
    assert "You are an award-winning novelist." not in ctx2
    assert "Use 1920s slang." in ctx2


def test_global_system_prompt_without_book(unix_server, tmp_path):
    root = tmp_path / "books"
    cfg = _config(unix_server, root)
    cfg.system_prompt = "Always speak like a pirate."
    client = Client(cfg, tool_context=ToolContext(BookStore(root)))
    assert client.context_messages
    assert client.context_messages[0]["content"] == "Always speak like a pirate."

    # When deselecting book, global prompt remains
    client.set_current_book(None)
    assert client.context_messages
    assert client.context_messages[0]["content"] == "Always speak like a pirate."


def test_client_and_transport_api_key(unix_server, tmp_path):
    from app.transport import HTTPTransport, UnixTransport

    root = tmp_path / "books"
    cfg = _config(unix_server, root)
    client = Client(cfg, tool_context=ToolContext(BookStore(root)), api_key="sk-secret123")
    assert client.api_key == "sk-secret123"
    assert client.transport.api_key == "sk-secret123"

    client.set_api_key("sk-updated456")
    assert client.api_key == "sk-updated456"
    assert client.transport.api_key == "sk-updated456"

    # HTTP transport initialization
    http_tr = HTTPTransport("http://localhost:8000", api_key="sk-test")
    assert http_tr.api_key == "sk-test"


class RedirectHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress logging in tests

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def _handle(self):
        path = self.path
        if path.startswith("/redirect-count/"):
            n = int(path.split("/")[-1])
            if n > 0:
                self.send_response(302)
                self.send_header("Location", f"/redirect-count/{n - 1}")
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"count": 0, "status": "ok"}')
        elif path == "/loop":
            self.send_response(302)
            self.send_header("Location", "/loop")
            self.end_headers()
        elif path == "/no-location":
            self.send_response(301)
            self.end_headers()
        elif path == "/stream-redirect":
            self.send_response(307)
            self.send_header("Location", "/stream-target")
            self.end_headers()
        elif path == "/stream-target":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"choices": [{"delta": {"content": "redirected stream"}}]}\n\n')
            self.wfile.write(b'data: [DONE]\n\n')
        elif path == "/see-other":
            self.send_response(303)
            self.send_header("Location", "/see-other-target")
            self.end_headers()
        elif path == "/see-other-target":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"method": self.command}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture()
def redirect_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    host, port = server.server_address
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


def test_http_transport_follows_redirects(redirect_server):
    from app.transport import HTTPTransport

    tr = HTTPTransport(redirect_server)
    resp = tr.get_json("/redirect-count/3")
    assert resp.status == 200
    assert resp.data == {"count": 0, "status": "ok"}


def test_http_transport_follows_up_to_10_redirects(redirect_server):
    from app.transport import HTTPTransport

    tr = HTTPTransport(redirect_server)
    # Exactly 10 redirects: 10 -> 9 -> ... -> 0 -> 200 OK
    resp = tr.get_json("/redirect-count/10")
    assert resp.status == 200
    assert resp.data == {"count": 0, "status": "ok"}


def test_http_transport_exceeds_10_redirects_raises(redirect_server):
    from app.transport import HTTPTransport

    tr = HTTPTransport(redirect_server)
    # 11 redirects: exceeds limit of 10
    with pytest.raises(TransportError, match="too many redirections"):
        tr.get_json("/redirect-count/11")


def test_http_transport_redirect_loop_raises(redirect_server):
    from app.transport import HTTPTransport

    tr = HTTPTransport(redirect_server)
    with pytest.raises(TransportError, match="too many redirections"):
        tr.get_json("/loop")


def test_http_transport_redirect_missing_location_raises(redirect_server):
    from app.transport import HTTPTransport

    tr = HTTPTransport(redirect_server)
    with pytest.raises(TransportError, match="missing Location header"):
        tr.get_json("/no-location")


def test_http_transport_stream_chat_follows_redirect(redirect_server):
    from app.transport import HTTPTransport

    tr = HTTPTransport(redirect_server)
    payloads = list(tr.stream_chat("/stream-redirect", {"prompt": "hi"}))
    assert len(payloads) == 1
    assert payloads[0]["choices"][0]["delta"]["content"] == "redirected stream"


def test_http_transport_303_see_other_changes_to_get(redirect_server):
    from app.transport import HTTPTransport

    tr = HTTPTransport(redirect_server)
    resp = tr.post_json("/see-other", {"data": "test"})
    assert resp.status == 200
    assert resp.data == {"method": "GET"}




