"""A reusable "render-to-canvas with custom scrolling" widget base.

Subclasses only need to implement :meth:`ScrollCanvas.paint`, which returns vertical
*stripes* (list of ``(row0, texture, height)``) plus the total rendered height.  This
widget then draws the stripes clipped to its content area with a **custom** painted
scrollbar (mouse wheel, click/drag on the bar, click on the track).  Both the chat log
and the logs tab are built on it.

GPU textures are capped at ~16384 px on some drivers, hence the striping.
"""

from __future__ import annotations

from kivy.clock import Clock
from kivy.graphics import (
    Color, Rectangle, StencilPop, StencilPush, StencilUnUse, StencilUse,
)
from kivy.uix.widget import Widget

from .colors import rgba

BAR_W = 12                 # scrollbar width in px
SCROLL_STEP = 64           # wheel step in px
MARGIN = 6                 # right margin between content and the scrollbar
MAX_TEXT_WIDTH = 1100      # hard ceiling for the rendered text column
STRIPE_H = 6000            # max texture height per stripe


class ScrollCanvas(Widget):
    """Base widget: renders itself from a paint function and scrolls itself."""

    bg_key = "bg"          # theme attribute that provides the background hex colour

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._stripes: list = []   # [(row0, texture, stripe_height_px)]
        self._total_h = 0
        self._content_w = 0
        self._offset = 0.0
        self._render_pending = False
        self._drag = None
        self.bind(size=lambda *_a: self._on_resize())
        # repaint when moved; (re)render when attached to a new parent (tab switches)
        self.bind(pos=lambda *_a: self._redraw())
        self.bind(parent=lambda *_a: self._schedule_render())

    # -- subclass hook -----------------------------------------------------
    def content_width(self) -> int:
        """Pixel width the content is rendered at."""
        return max(320, min(int(self.width) - BAR_W - MARGIN, MAX_TEXT_WIDTH))

    def has_content(self) -> bool:
        raise NotImplementedError

    def paint(self, width: int) -> list:
        """Render content and return ``(stripes, total_h, content_w)``.

        ``stripes`` is a list of ``(row0, texture, stripe_height)`` tuples covering the
        whole rendered image from top (row 0) to bottom.
        """
        raise NotImplementedError

    def follow_bottom(self) -> bool:
        """Return True when rendering should auto-scroll to the newest content."""
        return True

    # -- rendering ---------------------------------------------------------
    def _on_resize(self) -> None:
        self._schedule_render()

    def schedule_render(self) -> None:
        self._schedule_render()

    def _schedule_render(self) -> None:
        if self._render_pending:
            return
        self._render_pending = True
        Clock.schedule_once(lambda _dt: self._do_render(), 0)

    def _do_render(self) -> None:
        self._render_pending = False
        width = self.content_width()
        if width < 100 or not self.has_content():
            self._stripes = []
            self._total_h = 0
            self._redraw()
            return

        was_at_bottom = self._at_bottom()
        stripes, total_h, content_w = self.paint(width)
        self._stripes = stripes
        self._total_h = total_h
        self._content_w = content_w

        max_off = max(0.0, float(total_h) - self.height)
        if was_at_bottom or self.follow_bottom():
            self._offset = max_off
        else:
            self._offset = max(0.0, min(self._offset, max_off))
        self._redraw()

    def _at_bottom(self) -> bool:
        if not self._total_h:
            return True
        max_off = max(0.0, float(self._total_h) - self.height)
        return self._offset >= max_off - 4.0

    # -- painting ----------------------------------------------------------
    def _redraw(self, *_args) -> None:
        self.canvas.clear()
        self._last_paint = (self.pos, self.size, len(self._stripes), self._offset, self._total_h)
        if not getattr(self, "theme", None):
            return
        w, h = self.width, self.height
        if w <= 1 or h <= 1:
            return
        ox, oy = self.x, self.y
        bg = rgba(getattr(self.theme, self.bg_key, self.theme.bg))
        avail = max(1, int(w - BAR_W - MARGIN))
        total = float(self._total_h)
        # Render the stripes at their natural pixel width (never stretched): when the
        # viewport is wider than the rendered column we centre it; when it is narrower
        # we squeeze only as a last resort (very small windows).
        dw = self._stripes[0][1].width if self._stripes else avail
        if dw > avail:
            dw = avail
        x_off = max(0, int((avail - dw) // 2))
        with self.canvas:
            Color(*bg)
            Rectangle(pos=(ox, oy), size=(w, h))
            if self._stripes and total > 0:
                max_off = max(0.0, total - h)
                off = max(0.0, min(float(self._offset), max_off))
                StencilPush()
                Color(*bg)
                Rectangle(pos=(ox, oy), size=(avail, h))
                StencilUse()
                Color(1, 1, 1, 1)
                for row0, tex, sh in self._stripes:
                    draw_y = oy + (h + off - row0 - sh)
                    if draw_y + sh < oy or draw_y > oy + h:
                        continue
                    Rectangle(texture=tex, pos=(ox + x_off, draw_y),
                              size=(dw, sh))
                StencilUnUse()
                StencilPop()
            self._draw_scrollbar(oy, h)

    def _draw_scrollbar(self, oy: float, h: int) -> None:
        x = self.right - BAR_W
        with self.canvas:
            Color(*rgba(self.theme.scrollbar, 0.35))
            Rectangle(pos=(x, oy), size=(BAR_W, h))
            total = float(self._total_h)
            if total <= h or total <= 0:
                return
            view_ratio = h / total
            thumb_h = max(26.0, h * view_ratio)
            max_off = total - h
            frac = 0.0 if max_off <= 0 else min(1.0, self._offset / max_off)
            y = oy + frac * (h - thumb_h)
            Color(*rgba(self.theme.scrollbar, 0.9))
            Rectangle(pos=(x + 2, y), size=(BAR_W - 4, thumb_h))

    # -- interaction -------------------------------------------------------
    def _clamp_offset(self) -> None:
        total = float(self._total_h)
        self._offset = max(0.0, min(self._offset, max(0.0, total - self.height)))

    def _scroll_by(self, delta: float) -> None:
        self._offset += delta
        self._clamp_offset()
        self._redraw()

    def _scroll_to(self, ratio: float) -> None:
        total = float(self._total_h)
        max_off = max(0.0, total - self.height)
        self._offset = ratio * max_off
        self._clamp_offset()
        self._redraw()

    def on_touch_down(self, touch):
        if touch.button in ("scrollup", "scrolldown") and self.collide_point(*touch.pos):
            step = SCROLL_STEP * 3 if getattr(touch, "is_double_tap", False) else SCROLL_STEP
            self._scroll_by(-step if touch.button == "scrollup" else step)
            return True
        x, y = touch.pos
        if self.width - BAR_W <= x <= self.width and 0 <= y <= self.height:
            if float(self._total_h) > self.height:
                self._drag = {}
                touch.grab(self)
                self._scroll_to(y / self.height if self.height else 0.0)
                return True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if self._drag is not None and touch.grab_current is self:
            if float(self._total_h) > self.height:
                self._scroll_to(touch.pos[1] / max(1.0, self.height))
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if self._drag is not None:
            self._drag = None
            if touch.grab_current is self:
                touch.ungrab(self)
            return True
        return super().on_touch_up(touch)

    def scroll_to_bottom(self) -> None:
        self._offset = max(0.0, float(self._total_h) - self.height)
        self._redraw()
