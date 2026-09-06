#!/usr/bin/env python3
"""Launch the Book Writer GUI.

With a real display::

    python tools/run_gui.py

Headless, under a virtual X display (great for CI / screenshots)::

    python tools/run_gui.py --headless --geometry 1100x720+40+40

The GUI can be pointed at the fake server via ``--server`` (defaults to the bundled
``unix:/tmp/book_writer.sock``).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from app.__main__ import build_config, build_parser


def _run_headless(geometry: str | None, server: str | None, extra: list[str]) -> int:
    """Start Xvfb in the background, then run the GUI on that display."""
    display = " :99"
    proc = subprocess.Popen(
        ["xvfb-run", "-a", "-s", f"-screen 0 1280x900x24",
         sys.executable, "-m", "app", *(extra or [])],
        env={**os.environ, "DISPLAY": display},
    )
    return proc.wait()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the Book Writer GUI.")
    parser.add_argument("--headless", action="store_true", help="Run under Xvfb (headless).")
    parser.add_argument("--server", help="Server address, e.g. 'unix:/tmp/w.sock'.")
    parser.add_argument("--model", help="Model id to select by default.")
    parser.add_argument("--geometry", help="Initial window geometry, e.g. '1100x720+40+40'.")
    args, rest = parser.parse_known_args(argv)

    base = build_parser().parse_args(rest)
    config = build_config(base)
    if args.server:
        config.server_address = args.server

    if args.headless:
        geo_args = []
        if args.geometry:
            geo_args = ["--geometry", args.geometry]
        return _run_headless(args.geometry, args.server, geo_args)

    from app.__main__ import main as gui_main  # local import: needs a display

    return gui_main(rest)


if __name__ == "__main__":
    raise SystemExit(main())
