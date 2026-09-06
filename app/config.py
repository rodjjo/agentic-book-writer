"""Application configuration.

The :class:`Config` object holds everything the GUI and the client need: how to reach
the server (HTTP or a unix domain socket), which model to use, where books are stored and
which theme to paint the UI with.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


class Transport(str, Enum):
    """How the client reaches the OpenAI-compatible server."""

    HTTP = "http"
    UNIX = "unix"


class Theme(str, Enum):
    LIGHT = "light"
    DARK = "dark"


# The OpenAI-compatible endpoint every request is sent to, regardless of transport.
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
MODELS_PATH = "/v1/models"


class ConfigError(ValueError):
    """Raised when a configuration value is invalid."""


@dataclass
class ServerSpec:
    """A parsed, validated description of a server address."""

    transport: Transport
    host: str = ""          # host:port for http (without scheme)
    socket_path: str = ""   # path for unix

    @property
    def is_unix(self) -> bool:
        return self.transport is Transport.UNIX

    @property
    def label(self) -> str:
        if self.is_unix:
            return f"unix://{self.socket_path}"
        return f"http://{self.host}"

    @property
    def base_url(self) -> str:
        if self.is_unix:
            return self.socket_path
        return f"http://{self.host}"

    def to_address(self) -> str:
        """Render back into the human-friendly address string."""
        return self.label


def parse_server_address(address: str) -> ServerSpec:
    """Parse a ``http(s)://host:port`` or ``unix:/path/to.sock`` address.

    Raises :class:`ConfigError` when the address cannot be understood.
    """
    if not address or not address.strip():
        raise ConfigError("Server address is empty")

    address = address.strip()
    lowered = address.lower()

    if lowered.startswith("unix://") or lowered.startswith("unix:"):
        # Support both "unix:/path" and "unix:/path" with a trailing scheme.
        path = address[len("unix://"):] if lowered.startswith("unix://") else address[len("unix:"):]
        path = path.strip()
        if not path:
            raise ConfigError("unix address has no socket path")
        return ServerSpec(transport=Transport.UNIX, socket_path=path)

    parsed = urlparse(address)
    if parsed.scheme in ("http", "https"):
        if not parsed.hostname:
            raise ConfigError(f"server address '{address}' has no host")
        port = parsed.port
        if port is None and parsed.scheme == "https":
            port = 443
        host = parsed.netloc
        # urlparse keeps userinfo in netloc; strip it for cleanliness.
        if "@" in host:
            host = host.rsplit("@", 1)[-1]
        if not host:
            raise ConfigError(f"server address '{address}' has no host")
        return ServerSpec(transport=Transport.HTTP, host=host)

    # Fall back to treating "host:port" (no scheme) as plain HTTP for convenience.
    if ":" in address and "/" not in address and "://" not in address:
        return ServerSpec(transport=Transport.HTTP, host=address)

    raise ConfigError(
        f"cannot parse server address {address!r}. "
        "Use 'http://host:port' or 'unix:/path/to.sock'."
    )


@dataclass
class Config:
    """Runtime configuration for the Book Writer client."""

    server_address: str = "unix:/tmp/book_writer.sock"
    model: str = "book-writer-agent"
    book_root: str = ""
    theme: Theme = Theme.LIGHT
    connection_timeout: float = 30.0
    max_chat_turns: int = 8  # guard against runaway tool loops
    stream: bool = True       # stream assistant tokens and render them live

    # Populated lazily.
    _server_spec: Optional[ServerSpec] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.book_root:
            self.book_root = str(Path.home() / "books")

    # -- server -----------------------------------------------------------
    @property
    def server_spec(self) -> ServerSpec:
        if self._server_spec is None:
            self._server_spec = parse_server_address(self.server_address)
        return self._server_spec

    @property
    def transport(self) -> Transport:
        return self.server_spec.transport

    def update_server_address(self, address: str) -> None:
        """Validate and store a new server address, clearing the cached spec."""
        self._server_spec = parse_server_address(address)
        self.server_address = address

    # -- convenience ------------------------------------------------------
    @property
    def is_dark(self) -> bool:
        return self.theme is Theme.DARK

    def with_overrides(self, **changes) -> "Config":
        """Return a copy of the config with the given fields overridden."""
        allowed = {f for f in self.__dataclass_fields__}  # type: ignore[attr-defined]
        unknown = set(changes) - allowed
        if unknown:
            raise ConfigError(f"unknown config fields: {sorted(unknown)}")
        return replace(self, **changes)

    def default_model(self) -> str:
        """The model name used when the server has not reported its models yet."""
        return self.model


def default_config() -> Config:
    """Return a :class:`Config` tailored for the bundled fake server."""
    sock = os.path.join(tempfile_get_tmp_dir(), "book_writer.sock")
    return Config(
        server_address=f"unix://{sock}",
        model="book-writer-agent",
        book_root=str(Path.home() / "books"),
    )


def tempfile_get_tmp_dir() -> str:
    """Return a writable temp directory (imported lazily to keep top-level light)."""
    import tempfile

    return tempfile.gettempdir()
