"""The Logs tab widget: a self-rendered view over :mod:`app.logs` ring buffer."""

from __future__ import annotations

from .. import log_render, logs
from ..images import image_to_texture
from .scroll_canvas import STRIPE_H, ScrollCanvas


def _strip_image(pil_image) -> tuple:
    stripes = []
    y = 0
    while y < pil_image.height:
        box = (0, y, pil_image.width, min(pil_image.height, y + STRIPE_H))
        part = pil_image.crop(box)
        stripes.append((y, image_to_texture(part), part.height))
        y += part.height
    return stripes


class LogView(ScrollCanvas):
    """Canvas that renders the application log lines with custom scrolling."""

    bg_key = "chat_bg"

    def __init__(self, theme, **kwargs):
        super().__init__(**kwargs)
        self.theme = theme
        self.entries: list[dict] = []
        self._changed_since_refresh = True

    # -- data --------------------------------------------------------------
    def refresh(self) -> None:
        """Re-read the ring buffer; call whenever :func:`app.logs.take_dirty` says so."""
        self.entries = logs.snapshot(limit=800)
        self._schedule_render()

    def clear(self) -> None:
        logs.clear_log()
        self.refresh()

    def has_content(self) -> bool:
        return bool(self.entries)

    def follow_bottom(self) -> bool:
        return False

    def paint(self, width: int):
        render = log_render.render_log(self.entries, self.theme.colors(), width)
        return _strip_image(render.image), render.content_h, render.content_w
