"""Application configuration.

The :class:`Config` object holds everything the GUI and the client need: how to reach
the server (HTTP or a unix domain socket), which model to use, where books are stored and
which theme to paint the UI with.

The user-editable settings are persisted to ``~/.agentic-book-writer/config.json``
(see :func:`default_config_path`) so they survive restarts: the client loads them at
startup and saves them whenever the user changes a setting in the GUI.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional
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
    system_prompt: str = ""   # global system instructions for the assistant

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

    # -- persistence ------------------------------------------------------
    def to_dict(self) -> dict:
        """The user-configurable settings as a JSON-friendly mapping."""
        return {
            "server_address": self.server_address,
            "model": self.model,
            "book_root": self.book_root,
            "theme": self.theme.value,
            "connection_timeout": self.connection_timeout,
            "max_chat_turns": self.max_chat_turns,
            "stream": self.stream,
            "system_prompt": self.system_prompt,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Config":
        """Rebuild a :class:`Config` from :meth:`to_dict` output.

        Missing keys fall back to the dataclass defaults; unknown keys (such as a
        ``version`` tag) are ignored.  Present-but-unusable values raise
        :class:`ConfigError` so a corrupt settings file can be reported instead of
        silently changing behaviour.
        """
        d = dict(data or {})

        theme_value = d.get("theme", Theme.LIGHT.value)
        if not isinstance(theme_value, Theme):
            try:
                theme_value = Theme(str(theme_value).strip().lower())
            except (ValueError, AttributeError):
                raise ConfigError(f"invalid theme {theme_value!r} in settings") from None

        def _text(key: str, default: str) -> str:
            value = d.get(key)
            return default if value is None or str(value).strip() == "" else str(value)

        def _number(key: str, default: float, *, integer: bool) -> float:
            value = d.get(key)
            if value is None or str(value).strip() == "":
                return default
            try:
                num = int(value) if integer else float(value)
            except (TypeError, ValueError):
                raise ConfigError(f"invalid number for '{key}': {value!r}") from None
            return num

        def _flag(key: str, default: bool) -> bool:
            value = d.get(key)
            if value is None:
                return default
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("1", "true", "yes", "on")

        return cls(
            server_address=_text("server_address", "unix:/tmp/book_writer.sock"),
            model=_text("model", "book-writer-agent"),
            book_root=_text("book_root", ""),
            theme=theme_value,
            connection_timeout=_number("connection_timeout", 30.0, integer=False),
            max_chat_turns=_number("max_chat_turns", 8, integer=True),
            stream=_flag("stream", True),
            system_prompt=_text("system_prompt", ""),
        )

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
    """Return a writable temp directory."""
    return tempfile.gettempdir()


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------

#: Directory name (under the user's home) that holds the client's settings file.
CONFIG_DIR_NAME = ".agentic-book-writer"
#: File name of the JSON settings file inside :data:`CONFIG_DIR_NAME`.
CONFIG_FILE_NAME = "config.json"
#: Version tag written into the settings file (currently informational only).
CONFIG_FORMAT_VERSION = 1


def default_config_path() -> Path:
    """Return the standard settings file location: ``~/.agentic-book-writer/config.json``."""
    return Path.home() / CONFIG_DIR_NAME / CONFIG_FILE_NAME


def save_config(cfg: Config, path: Optional[os.PathLike | str] = None) -> Path:
    """Write ``cfg``'s user settings to ``path`` (default: ~/.agentic-book-writer/config.json).

    The file is written atomically (temp file + rename) so a crash mid-write can
    never corrupt a previously-saved settings file.  The directory is created on
    demand and the file is chmod 0600 (owner read/write only).
    """
    target = Path(path) if path is not None else default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"version": CONFIG_FORMAT_VERSION, **cfg.to_dict()},
        indent=2, sort_keys=True,
    ) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent),
                                    prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    try:
        os.chmod(target, 0o600)
    except OSError:  # pragma: no cover - cosmetic, e.g. on platforms without chmod
        pass
    return target


def load_config(path: Optional[os.PathLike | str] = None) -> Optional[Config]:
    """Load the settings file written by :func:`save_config`.

    Returns ``None`` when no settings file exists yet.  Raises :class:`ConfigError`
    when the file exists but cannot be read or contains unusable values.
    """
    target = Path(path) if path is not None else default_config_path()
    if not target.exists():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read settings file {target}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"settings file {target} must contain a JSON object")
    return Config.from_dict(raw)
