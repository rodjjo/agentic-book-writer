# Book Writer

A **Kivy** desktop client that lets a large language model *author and edit books* for you.
You type (or paste) instructions, optionally attach an image, and an
**OpenAI-compatible server** — acting as an agent with **vision** and **tool calling** —
writes and revises the pages of your book. Assistant answers are **streamed** and the text
appears in the conversation live, token by token.

The book lives on **your computer** as Markdown pages inside a per-book folder. The server
never stores books; it only decides *what* to do. The actual file work (write / edit /
search pages, create / remove books) is performed by **the application** through tool
calls, tied to the book you currently have selected.

```
 ┌────────────────────┐   HTTP  (/v1/chat/completions, SSE)    ┌─────────────────────┐
 │  Book Writer (Kivy)│  ──────── or ────────────────────────▶ │ OpenAI-compatible   │
 │  • client GUI      │        unix domain socket              │ server (agent)      │
 │  • tools handled   │  ◀──────────────────────────────────── │  • vision model     │
 │  • books on disk   │     streamed tokens + tool calls       │  • tool calling      │
 └────────────────────┘                                        └─────────────────────┘
        │  book folders (markdown pages)
        ▼
   <books-root>/<slug(book)>/pages/*.md   +  book.json
```

## Features

- **Two transports to the same server**: `http://host:port` or `unix:/path/to.sock`.
  Both speak the OpenAI-compatible `POST /v1/chat/completions` protocol.
- **Streaming responses**: SSE chunks are parsed on the fly; the conversation tab renders
  the assistant's message as it is generated, and tool calls are only executed **after**
  the message has fully streamed.
- **Vision**: attach an image to any message; it is base64-encoded and sent to the server.
- **Tool calling loop, client-side**: the server proposes tool calls, the client executes
  them locally and feeds the results back — exactly like the real OpenAI API.
- **File tools** the model can call (the user never edits pages directly):
  | Tool | Purpose |
  |------|---------|
  | `create_book` | Create a new book (folder + `book.json`) |
  | `remove_book` | Delete a book and all its pages |
  | `list_books`  | List all books (name, author, page count) |
  | `list_pages`  | List the pages of a book |
  | `read_page`   | Return the raw content of a page |
  | `write_page`  | Create / overwrite a page (Markdown) |
  | `edit_page`   | Edit a page (append / replace a section) |
  | `search_in_book` | Search text across every page of a book |
  | `delete_page` | Remove a page from a book |
- **Perfectly rendered books**: Markdown is parsed and laid out block-by-block onto a
  raster image (Pillow) and shown in a scrollable reader — headings, bold/italic, lists,
  code blocks, blockquotes, tables, images, horizontal rules. Pages reflow when the
  window is resized.
- **Three tabs**: *Conversation* (self-rendered chat canvas with custom scrolling and a
  painted scrollbar), *Books* (read-only page reader) and *Logs* (live view of the
  application/protocol log).
- **Responsive two-row control bar** — server address, connect, model, theme, settings
  and status. It never scrolls or drags; content absorbs window resizes.
- **Modal dialogs only** for everything except the main window (settings, file pickers,
  image preview, confirms).
- **SVG icons** converted to PNGs (multiple sizes) and shipped inside the package.

## Layout

```
book_writer/
├── app/
│   ├── __main__.py        # GUI entry point (python -m app)
│   ├── __init__.py
│   ├── config.py          # configuration (server, model, book root, theme, stream)
│   ├── transport.py       # HTTP + unix-socket transports with SSE streaming
│   ├── client.py          # OpenAI-compatible client + tool-calling loop (streamed)
│   ├── books_store.py     # book / page filesystem management
│   ├── tools.py           # the file tools the client executes
│   ├── model_registry.py  # tool JSON-schemas sent to the server
│   ├── markdown_render.py # Markdown -> raster page rendering (Pillow, headless)
│   ├── chat_render.py     # conversation -> raster rendering (Pillow, headless)
│   ├── log_render.py      # log ring-buffer -> raster rendering (Pillow, headless)
│   ├── logs.py            # in-app logging ring buffer (shown on the Logs tab)
│   ├── images.py          # image helpers incl. PIL -> Kivy texture
│   ├── theme.py           # light/dark palettes (hex)
│   ├── fake_server.py     # bundled OpenAI-compatible mock agent (SSE aware)
│   ├── icons/             # PNG icons packaged with the app
│   └── gui/               # the Kivy UI
│       ├── application.py # App + controller (threads -> UI queue)
│       ├── control_bar.py # responsive two-row top bar
│       ├── chat_log.py    # conversation canvas (streamed live bubbles)
│       ├── scroll_canvas.py # shared custom-scroll canvas base
│       ├── books_panel.py # read-only rendered book reader
│       ├── log_view.py    # logs tab
│       ├── compose_bar.py # instruction input + image attach + send
│       ├── dialogs.py     # modal popup dialogs / file choosers
│       ├── widgets.py     # flat buttons, fonts, icon paths
│       └── colors.py      # hex themes -> Kivy RGBA
├── non_py/svg, non_py/pngs # icon sources + rasterised sizes
├── tools/                 # dev helpers (icons, demo book, server/GUI launchers)
├── tests/                 # pytest suite
│   ├── test_*.py          # unit tests (store, tools, transports, client, renderers)
│   └── system/            # system tests vs the fake server + a real GUI e2e
└── temp/                  # git-ignored scratch space
```

## Installation

Uses a standard PEP 621 `pyproject.toml` and a real Python virtual environment (no Poetry):

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

## Running

```bash
# 1) start the bundled fake OpenAI-compatible server (agent)
python tools/run_server.py --transport unix --socket /tmp/book_writer.sock

# 2) start the GUI (it auto-connects when started like this)
python -m app --server unix:/tmp/book_writer.sock --autoconnect
#    or simply:  book-writer
```

Headless / screenshot friendly:

```bash
python tools/run_gui.py --headless        # runs under Xvfb
python tools/run_gui.py --server unix:/tmp/book_writer.sock
```

### Trying it out

1. Start the fake server and connect (or pass `--autoconnect`).
2. Pick a model from the *Model* dropdown (the server reports them via `GET /v1/models`).
3. In the bottom input type something like:
   > *Create a children's book called "The Curious Moon" with six short pages.*
   Watch the assistant's answer stream in, then read the book on the **Books** tab.
4. Select a book on the Books tab so later instructions (add/edit/search pages) target it.

## Notes

- The fake server is a deterministic, rule-based stand-in for a real model. Swap it for
  any OpenAI-compatible endpoint (OpenAI, Ollama, vLLM, LM Studio, ...) without touching
  the client — only the *Server address* changes. It simulates SSE streaming the same way
  a real endpoint does.
- Everything the model writes stays on the client; nothing is persisted on the server.
- Network calls happen on worker threads; every UI mutation is marshalled to Kivy's main
  thread through a queue drained by `Clock`.

## Tests

```bash
pytest                    # unit + system tests (system = client vs fake server)
pytest -m system          # only the system tests (incl. the GUI end-to-end test)
pytest -m "not gui"       # skip the GUI test (needs an X display + xdotool)
```

The GUI system test launches the real app on the X display, types an instruction with
`xdotool`, and verifies the streamed tool calls wrote pages to disk (a screenshot is kept
in the pytest tmp dir).

## License

MIT
