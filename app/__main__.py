"""Entry point for the Book Writer Kivy client GUI.

Run with either::

    book-writer                 # console script from `pip install .`
    python -m app               # module execution
    python -m app --server unix:/tmp/book_writer.sock --autoconnect

The CLI only chooses *where to connect*, *how the app looks* and whether to connect
automatically; everything else is configured at runtime (settings dialog) or in the window.
Settings changed in the GUI are remembered in ``~/.agentic-book-writer/config.json`` and
restored on the next start: the persisted settings are loaded first, then any explicit
command-line flags override them for that run.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Optional

from .config import (Config, ConfigError, Theme as ConfigTheme,
                     default_config, default_config_path, load_config)
from .theme import THEMES

_GEOMETRY_RE = re.compile(r"^\s*(\d+)x(\d+)([+-]\d+)?([+-]\d+)?\s*$")


def build_config(args: argparse.Namespace, base: Optional[Config] = None) -> Config:
    """Apply the command-line flags to ``base`` (defaults when ``base`` is None).

    Only flags that were actually given on the command line override the base, so
    callers can layer the CLI over previously-persisted settings.
    """
    cfg = default_config() if base is None else base
    if args.server:
        cfg.server_address = args.server
    if args.model:
        cfg.model = args.model
    if args.book_root:
        cfg.book_root = args.book_root
    if args.theme:
        cfg.theme = ConfigTheme(args.theme)
    if args.timeout is not None:
        cfg.connection_timeout = args.timeout
    if args.system_prompt:
        cfg.system_prompt = args.system_prompt
    return cfg


def startup_config(args: argparse.Namespace,
                   config_path: Optional[Path | str] = None) -> Config:
    """The effective config for a GUI run.

    Starts from the persisted settings file (``~/.agentic-book-writer/config.json`` by
    default, or ``config_path`` when given), falls back to built-in defaults when no
    settings file exists, then lets explicit CLI flags in ``args`` override either.
    A corrupt settings file is reported on stderr and ignored.
    """
    cfg = default_config()
    try:
        saved = load_config(config_path)
    except ConfigError as exc:
        where = Path(config_path) if config_path is not None else default_config_path()
        print(f"warning: ignoring invalid settings file {where}: {exc}",
              file=sys.stderr)
    else:
        if saved is not None:
            cfg = saved
    return build_config(args, cfg)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="book-writer", description="Book Writer Kivy GUI.")
    parser.add_argument("-s", "--server",
                        help="Server address, e.g. 'unix:/tmp/w.sock' or 'http://host:port'.")
    parser.add_argument("-m", "--model", help="Model id to select by default.")
    parser.add_argument("--book-root", help="Folder that holds your books.")
    parser.add_argument("--theme", choices=list(THEMES), help="Initial colour theme.")
    parser.add_argument("--geometry", help="Initial window size, e.g. '1100x720'.")
    parser.add_argument("--timeout", type=float, help="Connection timeout in seconds.")
    parser.add_argument("--system-prompt", help="Initial global system prompt for the assistant.")
    parser.add_argument("--autoconnect", action="store_true",
                        help="Automatically connect to the configured server once the "
                             "window is up (used by system tests and demos).")
    return parser


def _apply_window_geometry(geometry: Optional[str]) -> None:
    """Configure the Kivy window size from an ``WxH`` / ``WxH+X+Y`` string."""
    if not geometry:
        return
    m = _GEOMETRY_RE.match(geometry or "")
    if not m:
        print(f"ignoring unrecognised --geometry {geometry!r}", file=sys.stderr)
        return
    from kivy.config import Config as KivyConfig

    width, height = int(m.group(1)), int(m.group(2))
    KivyConfig.set("graphics", "width", str(width))
    KivyConfig.set("graphics", "height", str(height))
    KivyConfig.set("graphics", "resizable", "1")


def main(argv: Optional[list[str]] = None) -> int:
    # Kivy must not write log files into a read-only HOME (~/.kivy) and must not
    # hijack our own command line with its argument parser.
    os.environ.setdefault("KIVY_NO_FILELOG", "1")
    os.environ.setdefault("KIVY_NO_ARGS", "1")

    import logging

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    from kivy.config import Config as KivyConfig

    KivyConfig.set("input", "mouse", "mouse,disable_multitouch")
    # Kivy's default is to quit the whole app when Escape is pressed. That is a
    # data-loss trap while a modal (e.g. Settings) is open, so keep the app up;
    # dialogs are closed explicitly via their buttons instead.
    KivyConfig.set("kivy", "exit_on_escape", "0")

    args = build_parser().parse_args(argv)
    config = startup_config(args)
    _apply_window_geometry(args.geometry)

    # Import the App lazily: importing kivy.uix is only needed for a display session,
    # so non-GUI tooling (and unit/system tests) never pays for it.
    from .gui.application import BookWriterApp  # noqa: local import

    app = BookWriterApp(config, autoconnect=args.autoconnect)
    try:
        app.run()
    except Exception as exc:  # pragma: no cover - top-level guard for a GUI
        print(f"Book Writer failed to start: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
