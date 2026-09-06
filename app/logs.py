"""In-memory application log ring buffer + Python ``logging`` bridge.

The GUI keeps a *logs tab* that shows what the application and its network layer are
doing (connections, models, tool calls, results, errors).  We therefore capture the
standard :mod:`logging` stream of the ``bookwriter`` logger tree (and anything the
client/transport modules log) into a small thread-safe ring buffer that the UI polls
once a frame; when the flag is dirty the logs tab re-renders.  The records also still
flow to the normal root handlers, so running from a terminal shows the same lines.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

_LOGGER_NAME = "bookwriter"
_BUFFER_LIMIT = 2000

_lock = threading.Lock()
_buffer: deque[dict] = deque(maxlen=_BUFFER_LIMIT)
_dirty = False
_installed = False


def get_logger(name: str) -> logging.Logger:
    """Return ``bookwriter.<name>`` logger (child of the captured tree)."""
    return logging.getLogger(f"{_LOGGER_NAME}.{name}" if name else _LOGGER_NAME)


class RingBufferHandler(logging.Handler):
    """Appends formatted records to the ring buffer and marks the UI dirty."""

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - thin bridge
        try:
            message = record.getMessage()
        except Exception:  # defensive: never crash logging
            message = record.msg if isinstance(record.msg, str) else repr(record.msg)
        stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        entry = {
            "time": stamp,
            "level": record.levelname,
            "text": message,
            "logger": record.name,
        }
        global _dirty
        with _lock:
            _buffer.append(entry)
            _dirty = True


def install(level: int = logging.INFO) -> bool:
    """Attach the ring-buffer handler to the ``bookwriter`` logger tree once."""
    global _installed
    if _installed:
        return False
    handler = RingBufferHandler()
    handler.setLevel(level)
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    logger.addHandler(handler)
    logger.propagate = True
    _installed = True
    return True


def clear_log() -> None:
    global _dirty
    with _lock:
        _buffer.clear()
        _dirty = True


def snapshot(limit: int = 500) -> list[dict]:
    """Copy the most recent ``limit`` entries (oldest first)."""
    with _lock:
        items = list(_buffer)
    return items[-limit:]


def take_dirty() -> bool:
    """Return True when new records arrived since the last check (and reset)."""
    global _dirty
    with _lock:
        changed = _dirty
        _dirty = False
    return changed


def log_connect(server_address: str) -> None:
    get_logger("conn").info("connecting to %s", server_address)


def log_tool(name: str, result) -> None:
    """Record a tool-call report (shown on the Logs tab) into the ring buffer."""
    logger = get_logger("tools")
    if isinstance(result, dict):
        ok = bool(result.get("ok"))
        msg = result.get("message")
        detail = f": {str(msg)[:400]}" if msg else ""
        line = f"tool {name} -> {'ok' if ok else 'error'}{detail}"
        (logger.error if not ok else logger.info)(line)
    else:
        logger.info("tool %s -> %s", name, str(result)[:400])
