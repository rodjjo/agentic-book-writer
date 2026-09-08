"""Transport abstraction for talking to the OpenAI-compatible server.

Two transports are supported, both speaking HTTP/1.1 over the standard
``/v1/chat/completions`` and ``/v1/models`` endpoints:

* :class:`HTTPTransport`   -- plain/HTTPS HTTP to ``host:port``.
* :class:`UnixTransport`   -- an HTTP/1.1 request over an AF_UNIX domain socket.

A single :func:`make_transport` factory turns a :class:`~app.config.ServerSpec` into the
right object.
"""

from __future__ import annotations

import http.client
import json
import socket
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse

from .config import ServerSpec, Transport

MAX_REDIRECTS = 10
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class TransportError(Exception):
    """Raised when the transport cannot complete a request."""


@dataclass
class Response:
    status: int
    data: object            # parsed JSON body (or an error envelope)
    raw: bytes

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def iter_sse_payloads(buffers):
    """Yield JSON payloads from server-sent-events fed as raw byte buffers.

    Accepts an iterable of ``bytes`` chunks; each yielded value is the decoded
    ``data:`` JSON of one event.  Stops (without yielding) at ``data: [DONE]``.
    """
    buf = b""
    for chunk in buffers:
        buf += chunk.replace(b"\r\n", b"\n")
        while True:
            sep = buf.find(b"\n\n")
            if sep == -1:
                break
            event = buf[:sep]
            buf = buf[sep + 2:]
            for line in event.split(b"\n"):
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    return
                if not data:
                    continue
                try:
                    yield json.loads(data.decode("utf-8"))
                except json.JSONDecodeError:
                    continue


def _socket_chunks(sock: socket.socket, first: bytes = b""):
    """Yield the ``first`` bytes, then keep reading chunks until EOF."""
    if first:
        yield first
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        yield chunk


def _raise_transport(exc) -> "TransportError":
    if isinstance(exc, TransportError):
        return exc
    return TransportError(str(exc))


def _parse_json(status: int, body: bytes) -> Response:
    try:
        data = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        data = {"error": body.decode("utf-8", "replace")}
    return Response(status=status, data=data, raw=body)


def _check(response: Response) -> Response:
    if not response.ok:
        raise TransportError(
            f"request failed (HTTP {response.status}): {response.data!r}"
        )
    return response


class HTTPTransport:
    """HTTP/HTTPS transport using the standard library's ``http.client``."""

    def __init__(self, base_url: str, timeout: float = 30.0, api_key: Optional[str] = None):
        parsed = urlparse(base_url)
        self.scheme = parsed.scheme or "http"
        self.host = parsed.hostname or "localhost"
        self.port = parsed.port
        if self.port is None and self.scheme == "https":
            self.port = 443
        self.timeout = timeout
        self.api_key = api_key or ""

    def _create_connection(self, scheme: str, host: str, port: Optional[int]):
        import ssl as _ssl

        if scheme == "https":
            effective_port = port or 443
            return http.client.HTTPSConnection(
                host, effective_port, timeout=self.timeout,
                context=_ssl.create_default_context(),
            )
        effective_port = port or 80
        return http.client.HTTPConnection(host, effective_port, timeout=self.timeout)

    def request(self, method: str, path: str, obj: Optional[dict]) -> Response:
        cur_scheme = self.scheme
        cur_host = self.host
        cur_port = self.port
        cur_path = path if path.startswith("/") else f"/{path}"
        cur_method = method
        cur_obj = obj

        for redirect_count in range(MAX_REDIRECTS + 1):
            conn = self._create_connection(cur_scheme, cur_host, cur_port)
            body = json.dumps(cur_obj).encode("utf-8") if cur_obj is not None else None
            headers = {"Content-Type": "application/json"} if body is not None else {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            try:
                conn.request(cur_method, cur_path, body=body, headers=headers)
                resp = conn.getresponse()
                status = resp.status
                data = resp.read()
            except (OSError, http.client.HTTPException) as exc:
                conn.close()
                raise TransportError(str(exc)) from exc

            if status in REDIRECT_STATUSES:
                conn.close()
                if redirect_count >= MAX_REDIRECTS:
                    raise TransportError(f"too many redirections (exceeded {MAX_REDIRECTS})")

                location = resp.getheader("Location")
                if not location:
                    raise TransportError(f"redirect status {status} missing Location header")

                port_part = f":{cur_port}" if (cur_port and not ((cur_scheme == "http" and cur_port == 80) or (cur_scheme == "https" and cur_port == 443))) else ""
                cur_base = f"{cur_scheme}://{cur_host}{port_part}{cur_path}"
                new_url = urljoin(cur_base, location)
                parsed = urlparse(new_url)
                cur_scheme = parsed.scheme or cur_scheme
                cur_host = parsed.hostname or cur_host
                cur_port = parsed.port
                if cur_port is None and cur_scheme == "https":
                    cur_port = 443
                elif cur_port is None and cur_scheme == "http":
                    cur_port = 80
                cur_path = parsed.path or "/"
                if parsed.query:
                    cur_path += f"?{parsed.query}"

                if status == 303:
                    cur_method = "GET"
                    cur_obj = None
                continue

            conn.close()
            return _check(_parse_json(status, data))

        raise TransportError(f"too many redirections (exceeded {MAX_REDIRECTS})")

    def post_json(self, path: str, obj: dict) -> Response:
        return _check(self.request("POST", path, obj))

    def get_json(self, path: str) -> Response:
        return _check(self.request("GET", path, None))

    def stream_chat(self, path: str, obj: dict):
        """POST ``obj`` and yield decoded SSE payloads as they arrive, following redirects."""
        cur_scheme = self.scheme
        cur_host = self.host
        cur_port = self.port
        cur_path = path if path.startswith("/") else f"/{path}"
        cur_method = "POST"
        cur_obj = obj

        for redirect_count in range(MAX_REDIRECTS + 1):
            conn = self._create_connection(cur_scheme, cur_host, cur_port)
            body = json.dumps(cur_obj).encode("utf-8") if cur_obj is not None else None
            headers = {"Accept": "text/event-stream"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            try:
                conn.request(cur_method, cur_path, body=body, headers=headers)
                resp = conn.getresponse()
            except (OSError, http.client.HTTPException) as exc:
                conn.close()
                raise TransportError(str(exc)) from exc

            status = resp.status
            if status in REDIRECT_STATUSES:
                try:
                    resp.read()
                finally:
                    conn.close()

                if redirect_count >= MAX_REDIRECTS:
                    raise TransportError(f"too many redirections (exceeded {MAX_REDIRECTS})")

                location = resp.getheader("Location")
                if not location:
                    raise TransportError(f"redirect status {status} missing Location header")

                port_part = f":{cur_port}" if (cur_port and not ((cur_scheme == "http" and cur_port == 80) or (cur_scheme == "https" and cur_port == 443))) else ""
                cur_base = f"{cur_scheme}://{cur_host}{port_part}{cur_path}"
                new_url = urljoin(cur_base, location)
                parsed = urlparse(new_url)
                cur_scheme = parsed.scheme or cur_scheme
                cur_host = parsed.hostname or cur_host
                cur_port = parsed.port
                if cur_port is None and cur_scheme == "https":
                    cur_port = 443
                elif cur_port is None and cur_scheme == "http":
                    cur_port = 80
                cur_path = parsed.path or "/"
                if parsed.query:
                    cur_path += f"?{parsed.query}"

                if status == 303:
                    cur_method = "GET"
                    cur_obj = None
                continue

            if not 200 <= status < 300:
                body_sample = resp.read()[:300]
                conn.close()
                raise TransportError(f"request failed (HTTP {status}): {body_sample!r}")

            try:
                yield from iter_sse_payloads(iter(resp))
            finally:
                conn.close()
            return

        raise TransportError(f"too many redirections (exceeded {MAX_REDIRECTS})")


class _UnixHTTPConnection:
    """Minimal HTTP/1.1 client over an AF_UNIX socket (Connection: close)."""

    def __init__(self, socket_path: str, timeout: float = 30.0, api_key: Optional[str] = None):
        self.socket_path = socket_path
        self.timeout = timeout
        self.api_key = api_key or ""
        self._sock: Optional[socket.socket] = None

    def request(self, method: str, path: str, obj: Optional[dict]) -> Response:
        cur_method = method
        cur_path = path
        cur_obj = obj

        for redirect_count in range(MAX_REDIRECTS + 1):
            body = json.dumps(cur_obj).encode("utf-8") if cur_obj is not None else b""
            headers = [
                f"{cur_method} {cur_path} HTTP/1.1",
                "Host: book-writer",
                "Content-Type: application/json",
                f"Content-Length: {len(body)}",
                "Connection: close",
            ]
            if self.api_key:
                headers.append(f"Authorization: Bearer {self.api_key}")
            request_bytes = ("\r\n".join(headers) + "\r\n\r\n").encode("utf-8") + body

            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.settimeout(self.timeout)
            try:
                self._sock.connect(self.socket_path)
                self._sock.sendall(request_bytes)
                chunks = []
                while True:
                    chunk = self._sock.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
            except (OSError, socket.timeout) as exc:
                raise TransportError(str(exc)) from exc
            finally:
                self._sock.close()
                self._sock = None

            raw = b"".join(chunks)
            head, _, body_bytes = raw.partition(b"\r\n\r\n")
            lines = head.decode("utf-8", "replace").split("\r\n")
            try:
                status = int(lines[0].split()[1])
            except (IndexError, ValueError):
                raise TransportError(f"malformed HTTP response: {raw[:200]!r}")

            if status in REDIRECT_STATUSES:
                if redirect_count >= MAX_REDIRECTS:
                    raise TransportError(f"too many redirections (exceeded {MAX_REDIRECTS})")
                loc = None
                for header in lines[1:]:
                    if header.lower().startswith("location:"):
                        loc = header.split(":", 1)[1].strip()
                        break
                if not loc:
                    raise TransportError(f"redirect status {status} missing Location header")
                cur_path = loc
                if status == 303:
                    cur_method = "GET"
                    cur_obj = None
                continue

            content_length: Optional[int] = None
            for header in lines[1:]:
                if header.lower().startswith("content-length:"):
                    content_length = int(header.split(":", 1)[1].strip())
                    break
            if content_length is not None and len(body_bytes) >= content_length:
                body_bytes = body_bytes[:content_length]
            return _check(_parse_json(status, body_bytes))

        raise TransportError(f"too many redirections (exceeded {MAX_REDIRECTS})")


class UnixTransport:
    """Send HTTP/1.1 requests over a unix domain socket to the server."""

    def __init__(self, socket_path: str, timeout: float = 30.0, api_key: Optional[str] = None):
        self.socket_path = socket_path
        self.timeout = timeout
        self._api_key = api_key or ""
        self._conn = _UnixHTTPConnection(socket_path, timeout, api_key=self._api_key)

    @property
    def api_key(self) -> str:
        return self._api_key

    @api_key.setter
    def api_key(self, value: str) -> None:
        self._api_key = value or ""
        if hasattr(self, "_conn") and self._conn is not None:
            self._conn.api_key = self._api_key

    def post_json(self, path: str, obj: dict) -> Response:
        return self._conn.request("POST", path, obj)

    def get_json(self, path: str) -> Response:
        return self._conn.request("GET", path, None)

    def stream_chat(self, path: str, obj: dict):
        """POST ``obj`` over the socket and yield SSE payloads as they arrive."""
        cur_method = "POST"
        cur_path = path
        cur_obj = obj

        for redirect_count in range(MAX_REDIRECTS + 1):
            body = json.dumps(cur_obj).encode("utf-8")
            headers = [
                f"{cur_method} {cur_path} HTTP/1.1",
                "Host: book-writer",
                "Content-Type: application/json",
                "Accept: text/event-stream",
                f"Content-Length: {len(body)}",
                "Connection: close",
            ]
            if self._api_key:
                headers.append(f"Authorization: Bearer {self._api_key}")
            request_bytes = ("\r\n".join(headers) + "\r\n\r\n").encode() + body
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            try:
                sock.connect(self.socket_path)
                sock.sendall(request_bytes)
                # read the HTTP response head first, then stream the SSE body
                head = b""
                while b"\r\n\r\n" not in head:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    head += chunk
                status = 0
                try:
                    status = int(head.split(b"\r\n", 1)[0].split()[1])
                except (IndexError, ValueError):
                    raise TransportError(f"malformed HTTP response: {head[:200]!r}")

                if status in REDIRECT_STATUSES:
                    sock.close()
                    if redirect_count >= MAX_REDIRECTS:
                        raise TransportError(f"too many redirections (exceeded {MAX_REDIRECTS})")
                    lines = head.decode("utf-8", "replace").split("\r\n")
                    loc = None
                    for header in lines[1:]:
                        if header.lower().startswith("location:"):
                            loc = header.split(":", 1)[1].strip()
                            break
                    if not loc:
                        raise TransportError(f"redirect status {status} missing Location header")
                    cur_path = loc
                    if status == 303:
                        cur_method = "GET"
                        cur_obj = None
                    continue

                if not 200 <= status < 300:
                    raise TransportError(f"request failed (HTTP {status})")
                buf = head.partition(b"\r\n\r\n")[2]
                yield from iter_sse_payloads(_socket_chunks(sock, buf))
                return
            except (OSError, socket.timeout) as exc:
                raise TransportError(str(exc)) from exc
            finally:
                sock.close()

        raise TransportError(f"too many redirections (exceeded {MAX_REDIRECTS})")


def make_transport(spec: ServerSpec, timeout: float = 30.0, api_key: Optional[str] = None):
    """Create the appropriate transport for a parsed :class:`ServerSpec`."""
    if spec.is_unix:
        return UnixTransport(spec.socket_path, timeout, api_key=api_key)
    return HTTPTransport(f"http://{spec.host}", timeout, api_key=api_key)
