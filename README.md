# Book Writer

A **Kivy** desktop client that lets a large language model *author and edit books* for you.
You type (or paste) instructions, optionally attach an image, and an **OpenAI-compatible server**
— acting as an agent with **vision** and **tool calling** — writes and revises the *chapters* of
your book. Assistant answers are **streamed** and the text appears in the conversation live,
token by token. You can keep several independent chat **sessions** side by side in a sidebar.

A book lives on **your computer** as Markdown chapters inside a per-book folder. Each chapter is
one `*.md` file; the book's metadata (including a `uuid4` id) lives in `book.json`. The server
never stores books or chapters; it only decides *what* to do. The actual file work (create /
remove books, write / edit / delete / search **chapters**) is performed by **the application**
through tool calls, tied to the book you currently have selected.

```
 ┌────────────────────┐   HTTP  (/v1/chat/completions, SSE)    ┌─────────────────────┐
 │  Book Writer (Kivy)│  ──────── or ────────────────────────▶ │ OpenAI-compatible   │
 │  • sessions sidebar│        unix domain socket              │ server (agent)      │
 │  • tools handled   │  ◀──────────────────────────────────── │  • vision model     │
 │  • books on disk   │     streamed tokens + tool calls       │  • tool calling      │
 └────────────────────┘                                        └─────────────────────┘
         │  book folders (markdown chapters)
         ▼
    <books-root>/<slug(book)>/  book.json  +  chapters/<book_uuid>_000N.md
```

## Features

- **Two transports to the same server**: `http://host:port` or `unix:/path/to.sock`. Both speak
  the OpenAI-compatible `POST /v1/chat/completions` protocol. The HTTP transport also follows
  redirects (up to 10) and sends a `Bearer` **API key** when one is configured.
- **API key**: your key is entered in the control bar (hidden, password field) or the Settings
  dialog and forwarded to the server as `Authorization: Bearer <key>`. The bundled fake server
  ignores it, but a real endpoint (OpenAI, Ollama, vLLM, LM Studio, …) can require it.
- **Streaming responses**: SSE chunks are parsed on the fly; the conversation tab renders the
  assistant's message as it is generated, and tool calls are only executed **after** the message
  has fully streamed.
- **Sessions sidebar**: keep several independent conversations going at once. Each session has
  its own message history, so switching between them never mixes up their state. Create a new
  session with **+ New**, pick one to make it active, and **Remove** the active one.
- **Vision**: attach an image to any message; it is base64-encoded and sent to the server.
- **Tool calling loop, client-side**: the server proposes tool calls, the client executes them
  locally and feeds the results back — exactly like the real OpenAI API.
- **Chapter-first file model**: a book is a list of *chapters*, each one Markdown file. Newer
  tools address chapters by `book_id` + a 1-based `chapter_number`. The older **page**-based
  tools are still provided for backwards compatibility.
- **File tools** the model can call (the user never edits files directly):
  | Tool | Purpose |
  |------|---------|
  | `create_book` | Create a new book (folder + `book.json`, with a `uuid4` id) |
  | `remove_book` | Delete a book and all its chapters (by name or `book_id`) |
  | `list_books`  | List all books (name, author, chapter count, id) |
  | `get_book_info` | Return a book's details, chapters and their numbers/titles |
  | `list_chapters` | List a book's chapters in order |
  | `read_chapter` | Return the raw Markdown of a chapter (by number) |
  | `write_chapter` | Create / overwrite a chapter |
  | `edit_chapter` | Edit a chapter (append / replace_section / update_section) |
  | `delete_chapter` | Remove a chapter from a book |
  | `search_in_book` | Search text across every chapter of a book |
  | `list_pages` / `read_page` / `write_page` / `edit_page` / `delete_page` | Legacy page-based tools, kept for backwards compatibility |
- **Perfectly rendered books**: Markdown is parsed and laid out block-by-block onto a raster
  image (Pillow) and shown in a scrollable reader — headings, bold/italic, lists, code blocks,
  blockquotes, tables, images, horizontal rules. Pages reflow when the window is resized.
- **Three tabs**: *Conversation* (self-rendered chat canvas with custom scrolling, a painted
  scrollbar and a sessions sidebar), *Books* (read-only rendered book reader) and *Logs* (live
  view of the application/protocol log).
- **Responsive control bar** — now three rows: server address + connect, **API key** + model,
  and theme / settings / status. It never scrolls or drags; content absorbs window resizes.
- **Global system prompt**: set custom instructions for the assistant in Settings; they are
  prepended to every request (alongside the selected book's context/instructions).
- **Per-book custom instructions**: a book can carry its own instructions, applied automatically
  when you select it as the active book.
- **Modal dialogs only** for everything except the main window (settings, book instructions,
  file pickers, image preview, confirms).
- **SVG icons** converted to PNGs (multiple sizes) and shipped inside the package.

## Layout

```
book_writer/
├── app/
│   ├── __main__.py        # GUI entry point (python -m app); argparse flags
│   ├── __init__.py
│   ├── config.py          # configuration (server, model, book root, theme, stream, system prompt)
│   ├── transport.py       # HTTP + unix-socket transports with SSE streaming & redirects
│   ├── client.py          # OpenAI-compatible client + tool-calling loop (streamed)
│   ├── books_store.py     # book / chapter filesystem management (uuid4 ids)
│   ├── tools.py           # the file tools the client executes
│   ├── model_registry.py  # tool JSON-schemas sent to the server (chapter + legacy tools)
│   ├── markdown_render.py # Markdown -> raster page rendering (Pillow, headless)
│   ├── chat_render.py     # conversation -> raster rendering (Pillow, headless)
│   ├── log_render.py      # log ring-buffer -> raster rendering (Pillow, headless)
│   ├── logs.py            # in-app logging ring buffer (shown on the Logs tab)
│   ├── images.py          # image helpers incl. PIL -> Kivy texture
│   ├── theme.py           # light/dark palettes (hex)
│   ├── fake_server.py     # bundled OpenAI-compatible mock agent (SSE aware)
│   ├── icons/             # PNG icons packaged with the app
│   └── gui/               # the Kivy UI
│       ├── application.py # App + controller (threads -> UI queue), session manager
│       ├── control_bar.py # responsive three-row top bar (server, API key, model/…)
│       ├── chat_log.py    # conversation canvas (streamed live bubbles)
│       ├── scroll_canvas.py # shared custom-scroll canvas base
│       ├── session_list.py # sessions sidebar (create / switch / remove sessions)
│       ├── books_panel.py # read-only rendered book reader
│       ├── log_view.py    # logs tab
│       ├── compose_bar.py # instruction input + image attach + send
│       ├── dialogs.py     # modal popup dialogs / file choosers / settings
│       ├── widgets.py     # flat buttons, fonts, icon paths
│       └── colors.py      # hex themes -> Kivy RGBA
├── non_py/
│   ├── svg/               # icon sources
│   ├── pngs/              # rasterised icon sizes
│   └── fonts/             # fonts used by the (headless) renderers
├── tools/                 # dev helpers (icons, demo book, server/GUI launchers)
├── tests/                 # pytest suite
│   ├── test_*.py          # unit tests (store, tools, transports, client, renderers,
│   │                       #   sessions, settings dialog, books panel)
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

Or, with the bundled `Makefile`:

```bash
make install   # create .venv and pip install -e ".[dev]"
make server    # start the bundled fake OpenAI-compatible server
make run       # launch the GUI (auto-connects to the fake server)
make dev       # start the fake server, then launch the GUI on top of it
make test      # run the pytest suite
```

## Running

```bash
# 1) start the bundled fake OpenAI-compatible server (agent)
python tools/run_server.py --transport unix --socket /tmp/book_writer.sock
#    or an HTTP endpoint:  python tools/run_server.py --transport http --port 8000

# 2) start the GUI (it auto-connects when started like this)
python -m app --server unix:/tmp/book_writer.sock --autoconnect
#    or simply:  book-writer
```

Headless / screenshot friendly:

```bash
python tools/run_gui.py --headless        # runs under Xvfb
python tools/run_gui.py --server unix:/tmp/book_writer.sock
```

Optional GUI flags: `--model`, `--book-root`, `--theme`, `--geometry`, `--timeout`,
`--system-prompt`, `--autoconnect` (see `python -m app --help`).

### Trying it out

1. Start the fake server and connect (or pass `--autoconnect`).
2. Pick a model from the *Model* dropdown (the server reports them via `GET /v1/models`).
3. In the bottom input type something like:
   > *Create a children's book called "The Curious Moon" with six short chapters.*
   Watch the assistant's answer stream in, then read the book on the **Books** tab.
4. Select a book on the Books tab so later instructions (add/edit/search chapters) target it.
5. Open the **Sessions** sidebar to start a second, independent conversation if you like.

## Notes

- The fake server is a deterministic, rule-based stand-in for a real model. Swap it for any
  OpenAI-compatible endpoint (OpenAI, Ollama, vLLM, LM Studio, …) without touching the client —
  only the *Server address* changes (and, for real endpoints, the *API key*). It simulates SSE
  streaming the same way a real endpoint does.
- Everything the model writes stays on the client; nothing is persisted on the server.
- Chapters are stored as `<book_uuid>_NNNN.md`; older books written with the page-based model are
  read transparently (a `Page` is an alias for a `Chapter`).
- The client sends a global system prompt and the selected book's context to every request.
- Network calls happen on worker threads; every UI mutation is marshalled to Kivy's main thread
  through a queue drained by `Clock`.

## Tests

```bash
pytest                    # unit + system tests (system = client vs fake server)
pytest -m system          # only the system tests (incl. the GUI end-to-end test)
pytest -m "not gui"       # skip the GUI test (needs an X display + xdotool)
```

The suite covers the books/chapters store, the file tools, both transports (incl. API key and
redirects), the streaming client, the Markdown / chat renderers, chat **sessions**, and the
settings dialog. The GUI system test launches the real app on the X display, types an
instruction with `xdotool`, and verifies the streamed tool calls wrote chapters to disk (a
screenshot is kept in the pytest tmp dir).

## License

MIT
