#!/usr/bin/env python3
"""Launch the Book Writer fake OpenAI-compatible server.

Start it ideally inside its own tmux window so you can watch / control it::

    python tools/run_server.py --transport unix --socket /tmp/book_writer.sock
    python tools/run_server.py --transport http --port 8000

The server speaks the OpenAI-compatible ``/v1/chat/completions`` and ``/v1/models``
endpoints over either a unix domain socket or plain HTTP. The tools it requests are
executed *locally by the client*.
"""

from __future__ import annotations

import argparse

from app.fake_server import main as server_main


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Book Writer fake OpenAI-compatible server (in a tmux window)."
    )
    parser.add_argument("--transport", choices=["http", "unix"], default="unix")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--socket", default="/tmp/book_writer.sock")
    parser.add_argument("--model", default="book-writer-agent")
    args = parser.parse_args(argv)
    return server_main(
        argv=[
            "--transport", args.transport,
            "--host", args.host,
            "--port", str(args.port),
            "--socket", args.socket,
            "--model", args.model,
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
