"""Book Writer — a Kivy desktop client that lets an LLM author and edit books.

The package contains:

* :mod:`app.config`        -- runtime configuration (server, model, book root, theme).
* :mod:`app.transport`     -- HTTP and unix-domain-socket transport abstraction.
* :mod:`app.client`        -- OpenAI-compatible client running the tool-calling loop.
* :mod:`app.books_store`   -- on-disk book / page management.
* :mod:`app.tools`         -- the file tools the model can invoke (client-side).
* :mod:`app.model_registry`-- the tool schemas exposed to the server.
* :mod:`app.markdown_render` -- Markdown -> raster image rendering (Pillow).
* :mod:`app.chat_render`   -- conversation transcript -> raster image rendering.
* :mod:`app.log_render`    -- application log -> raster image rendering.
* :mod:`app.fake_server`   -- an OpenAI-compatible agent server for local use/testing.
* :mod:`app.gui`           -- the Kivy user interface (control bar, chat canvas,
  books panel, logs tab, compose bar, modal dialogs).
"""

__version__ = "0.1.0"
