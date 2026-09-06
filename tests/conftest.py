"""Shared pytest fixtures and helpers."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = os.environ.get("BOOK_WRITER_PYTHON") or sys.executable


@pytest.fixture()
def books_root(tmp_path):
    """A fresh folder the BookStore will own."""
    return tmp_path / "books"


def wait_for_socket(path: Path, timeout: float = 10.0) -> bool:
    """Wait until a unix socket accepts connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.settimeout(0.2)
                    s.connect(str(path))
                return True
            except (OSError, ConnectionRefusedError):
                pass
        time.sleep(0.05)
    return False


class FakeServerProcess:
    """A fake OpenAI-compatible server running as a subprocess (like a real server)."""

    def __init__(self, socket_path: Path, book: str = "book-writer-agent"):
        self.socket_path = socket_path
        self.proc = subprocess.Popen(
            [PYTHON, "-m", "app.fake_server",
             "--transport", "unix", "--socket", str(socket_path), "--model", book],
            cwd=str(REPO_ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "KIVY_NO_FILELOG": "1"},
        )
        if not wait_for_socket(socket_path):
            self.stop()
            raise RuntimeError("fake server did not come up in time")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)


@pytest.fixture()
def fake_server(tmp_path):
    """Start a fake server on a unix socket; yield its address; stop afterwards."""
    socket_path = tmp_path / "agent.sock"
    server = FakeServerProcess(socket_path)
    yield f"unix:{socket_path}"
    server.stop()
