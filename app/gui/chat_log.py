"""The Conversation tab widget: a self-rendered chat transcript on a canvas.

The transcript is kept here as a list of ``{"kind", "text"}`` entries.  Each turn is
painted by :mod:`app.chat_render` (headless Pillow) into one tall RGBA image which is
sliced into vertical stripes and uploaded; :class:`~app.gui.scroll_canvas.ScrollCanvas`
draws it with a custom painted scrollbar.  New messages re-render the whole transcript
once per frame (coalesced), i.e. it is rendered *in turns*.
"""

from __future__ import annotations

import json

from .. import chat_render
from ..images import image_to_texture
from .scroll_canvas import STRIPE_H, ScrollCanvas

MAX_MESSAGES = 260        # transcript length guard (oldest messages are dropped)


def _strip_image(pil_image) -> tuple:
    """Slice a rendered image into vertical stripes: (list, height, width)."""
    stripes = []
    y = 0
    while y < pil_image.height:
        box = (0, y, pil_image.width, min(pil_image.height, y + STRIPE_H))
        part = pil_image.crop(box)
        stripes.append((y, image_to_texture(part), part.height))
        y += part.height
    return stripes


class ChatLog(ScrollCanvas):
    """The conversation canvas (user/assistant/tool/error/system bubbles)."""

    bg_key = "chat_bg"

    def __init__(self, theme, messages: list | None = None, **kwargs):
        super().__init__(**kwargs)
        self.theme = theme
        self.messages = messages if messages is not None else []

    # -- message API -------------------------------------------------------
    def _push(self, kind: str, text: str) -> None:
        self.messages.append({"kind": kind, "text": text})
        if len(self.messages) > MAX_MESSAGES:
            drop = len(self.messages) - MAX_MESSAGES
            self.messages = self.messages[drop:]
            self.messages.insert(0, {"kind": "system",
                                     "text": "(older messages were trimmed)"})
        self._schedule_render()

    def add_system(self, text: str) -> None:
        self._push("system", text)

    def add_user(self, text: str) -> None:
        self._push("user", text)

    def add_assistant(self, text: str) -> None:
        if text:
            self._push("assistant", text)

    def stream_append(self, chunk: str) -> None:
        """Grow the currently-streaming assistant bubble with a new chunk.

        Used by real-time SSE rendering: one bubble is kept open and its text is
        re-rendered (per frame) as tokens arrive; it is only closed when the next
        message of a different kind is added.
        """
        if not chunk:
            return
        if not self.messages or self.messages[-1].get("kind") != "assistant":
            self._push("assistant", "")
        self.messages[-1]["text"] += chunk
        self._schedule_render()

    def add_tool_call(self, name: str, args: dict) -> None:
        try:
            rendered = json.dumps(args, ensure_ascii=False)[:900]
        except (TypeError, ValueError):
            rendered = str(args)[:900]
        self._push("tool_call", f"Calling tool: {name}\n{rendered}")

    def add_tool_result(self, name: str, result) -> None:
        if isinstance(result, dict):
            ok = bool(result.get("ok"))
            head = "ok" if ok else "error"
            text = f"{name} -> {head}"
            if result.get("message"):
                text += f"\n{str(result['message'])[:400]}"
        else:
            text = f"{name} -> {str(result)[:400]}"
        self._push("tool_result", text)

    def add_error(self, message: str) -> None:
        self._push("error", str(message))

    def clear(self) -> None:
        self.messages = []
        self._schedule_render()

    def set_theme(self, theme) -> None:
        self.theme = theme
        self._schedule_render()

    # -- ScrollCanvas hooks ------------------------------------------------
    def has_content(self) -> bool:
        return bool(self.messages)

    def follow_bottom(self) -> bool:
        # Preserve the user's reading position when new messages arrive; auto-follow
        # only happens when they were already at the bottom.
        return False

    def paint(self, width: int):
        palette = self.theme.colors()
        try:
            render = chat_render.render_chat(self.messages, palette, width)
        except Exception as exc:  # never let a paint failure kill the UI
            render = chat_render.render_chat(
                self.messages + [{"kind": "error", "text": f"(render failed: {exc})"}],
                palette, width)
        return _strip_image(render.image), render.image.height, render.content_w
