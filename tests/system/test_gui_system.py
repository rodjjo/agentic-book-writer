"""SYSTEM TEST (GUI): drive the actual Kivy app with real keystrokes.

A fake OpenAI-compatible server runs as a subprocess; the Book Writer GUI is launched
as another subprocess on the current X display with ``--autoconnect``; xdotool types a
real instruction into the compose box and presses Enter.  The test then verifies that
the assistant's tool calls created a real book on disk — i.e. the whole chain works:

    keyboard -> GUI -> client -> fake server (agent) -> tool calls -> local files

Run with:  pytest -m system        (or  pytest -m "system and not gui" to skip GUI)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.conftest import FakeServerProcess, PYTHON, REPO_ROOT, wait_for_socket

pytestmark = [pytest.mark.system, pytest.mark.gui]

DISPLAY = os.environ.get("DISPLAY", ":99")


def _xdotool(*args: str) -> bool:
    try:
        subprocess.run(["xdotool", *args], check=True, timeout=10,
                       capture_output=True)
        return True
    except Exception:
        return False


def _find_window(name: str, timeout: float = 40.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            out = subprocess.run(["xdotool", "search", "--onlyvisible", "."],
                                 capture_output=True, text=True, timeout=10)
            for wid in out.stdout.split():
                if not wid.isdigit():
                    continue
                title = subprocess.run(["xdotool", "getwindowname", wid],
                                       capture_output=True, text=True, timeout=5)
                if title.returncode == 0 and name in title.stdout:
                    return wid
        except Exception:
            pass
        time.sleep(0.5)
    return None


def _wait_for_book(book_root: Path, name: str, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        meta = book_root / (name.lower().replace(" ", "-")) / "book.json"
        if meta.exists():
            pages = list(meta.parent.glob("chapters/*.md")) or list(meta.parent.glob("pages/*.md"))
            if pages:
                return True
        time.sleep(0.5)
    return False


@pytest.mark.skipif(not shutil.which("xdotool"),
                    reason="xdotool is required for the GUI system test")
@pytest.mark.skipif(not DISPLAY,
                    reason="no X display available")
def test_gui_types_an_instruction_and_creates_a_book(tmp_path):
    sock = tmp_path / "agent.sock"
    book_root = tmp_path / "books"
    server = FakeServerProcess(sock)
    gui = None
    try:
        env = {**os.environ, "KIVY_NO_FILELOG": "1", "DISPLAY": DISPLAY}
        gui = subprocess.Popen(
            [PYTHON, "-m", "app", "--server", f"unix:{sock}",
             "--book-root", str(book_root), "--model", "book-writer-agent",
             "--autoconnect", "--geometry", "980x680"],
            cwd=str(REPO_ROOT), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        window = _find_window("Book Writer")
        assert window, "the Book Writer window never appeared"
        time.sleep(1.0)
        _xdotool("windowactivate", "--sync", window)
        time.sleep(1.0)

        instruction = ('Create a book called "GUI Automation Tales" '
                       "with 2 pages about friendly robots.")
        ok = _xdotool("type", "--delay", "25", instruction)
        assert ok, "could not type into the app"
        _xdotool("key", "Return")

        if not _wait_for_book(book_root, "GUI Automation Tales"):
            # Retry once: focus may not have been in the compose box.
            _xdotool("windowactivate", "--sync", window)
            time.sleep(0.8)
            _xdotool("type", "--delay", "25", instruction)
            _xdotool("key", "Return")

        created = _wait_for_book(book_root, "GUI Automation Tales")
        # keep a screenshot as evidence (visible with `pytest -s`)
        shot = tmp_path / "gui_system_shot.png"
        subprocess.run(["scrot", str(shot)], env={**os.environ, "DISPLAY": DISPLAY},
                       timeout=10, capture_output=True)
        print(f"\n[gui-system] screenshot: {shot}", flush=True)
        assert created, f"the book was not created on disk under {book_root}"

        meta = (book_root / "gui-automation-tales" / "book.json").read_text()
        pages = list((book_root / "gui-automation-tales" / "chapters").glob("*.md")) or list((book_root / "gui-automation-tales" / "pages").glob("*.md"))
        assert len(pages) >= 2, f"expected >=2 pages, got {len(pages)}"
        assert '"GUI Automation Tales"' in meta
    finally:
        if gui is not None:
            gui.terminate()
            try:
                gui.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                gui.kill()
                gui.wait(timeout=5)
        server.stop()
