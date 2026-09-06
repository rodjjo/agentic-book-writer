"""Kivy user-interface package for Book Writer.

The package replaces the original Tkinter GUI wholesale: every widget is a Kivy
widget and the whole application is a Kivy :class:`~kivy.app.App`.  All rendering
of Markdown pages and of the conversation transcript is still done headless by the
Pillow-based renderers in :mod:`app.markdown_render` and :mod:`app.chat_render`;
the Kivy widgets merely *display* the raster images on the GPU through textures.

Modules
-------
``colors``        hex-theme → RGBA float helpers.
``widgets``       shared UI pieces (flat buttons, fonts, icon paths).
``control_bar``   top bar: server address, connection, model, books folder, theme, settings.
``chat_log``      conversation canvas with self-managed scrolling + painted scrollbar.
``books_panel``   read-only book browser rendering each page from Markdown.
``compose_bar``   bottom instruction input with image attachment.
``dialogs``       every other window is a modal Kivy ``Popup``.
``application``   the :class:`~kivy.app.App` itself and the controller logic.
"""

from __future__ import annotations
