"""Render a conversation transcript into a raster image for the chat canvas.

The :class:`~app.gui.chat_log.ChatLog` widget owns the layout, word wrapping, scrolling
and the painted scrollbar; this module is purely the *paint* step: it turns a list of
messages into an RGBA :class:`~PIL.Image.Image` that the widget then blits and clips.

Rendering is done entirely with Pillow (headless-capable), so the same code path runs in
headless tests and on screen.

Bubbles mirror the original design:

* ``user``        -- right aligned, blue bubble.
* ``assistant``   -- left aligned, light bubble.
* ``tool_call`` / ``tool_result`` -- left aligned, amber bubble in mono type.
* ``error``       -- left aligned, red bubble.
* ``system``      -- muted, centred, no bubble (hint / divider line).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Sequence

from PIL import Image, ImageDraw, ImageFont

from .theme import to_rgb

# --------------------------------------------------------------------------
# Fonts (DejaVu, bundled with virtually every Linux desktop)
# --------------------------------------------------------------------------

_FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")

_FONT_NAMES = {
    "body": ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf"),
    "mono": ("DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf", "DejaVuSansMono-Oblique.ttf"),
}


@lru_cache(maxsize=None)
def _load_font(style: str, name: str, size: int):
    path = _FONT_DIR / name
    try:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    except Exception:
        pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - Pillow version difference
        return ImageFont.load_default()


def font_for(style: str, size: int, bold: bool = False, italic: bool = False):
    key = "body" if style == "body" else "mono"
    regular, bold_name, italic_name = _FONT_NAMES[key]
    if bold:
        return _load_font(style, bold_name, size)
    if italic:
        return _load_font(style, italic_name, size)
    return _load_font(style, regular, size)


# --------------------------------------------------------------------------
# Metrics / geometry
# --------------------------------------------------------------------------

MARGIN = 14                 # padding inside the rendered content
H_GAP = 12                  # vertical gap between messages
PAD_X = 14                  # horizontal padding inside a bubble
PAD_Y = 9                   # vertical padding inside a bubble
SMALL_FONT = 12
BODY_FONT = 15
TOOL_FONT = 13
MAX_WIDTH = 1200            # hard ceiling on content width


def _measure(font: ImageFont.ImageFont, text: str) -> float:
    if not text:
        return 0.0
    try:
        return float(font.getlength(text))
    except Exception:  # pragma: no cover - defensive
        bbox = font.getbbox(text)
        return float(bbox[2] - bbox[0])


def _split_words(text: str) -> List[str]:
    """Split a paragraph into whitespace-free word tokens."""
    return text.split()


def _chunk_word(word: str, font: ImageFont.ImageFont, max_w: float) -> List[str]:
    """Break a single word that is wider than ``max_w`` into fitting chunks.

    Without this an unbreakable token (a long URL, a pasted blob, ...) would
    overflow the wrap column and get clipped at the side of the chat box.
    """
    if not word or _measure(font, word) <= max_w:
        return [word] if word else []
    chunks: List[str] = []
    buf = ""
    for ch in word:
        if buf and _measure(font, buf + ch) > max_w:
            chunks.append(buf)
            buf = ch
        else:
            buf += ch
    if buf:
        chunks.append(buf)
    return chunks


def _wrap_words(words: List[str], font: ImageFont.ImageFont, max_w: float) -> List[List[str]]:
    """Greedily wrap ``words`` into rows each at most ``max_w`` px wide.

    Every returned row is a list of words that fit together; an empty row ``[]``
    is kept so blank paragraphs still occupy a line.  Words are joined with a
    single space when measured/drawn (see :func:`_line_w`).
    """
    lines: List[List[str]] = []
    cur: List[str] = []
    cur_w = 0.0
    space_w = _measure(font, " ")
    for word in words:
        for piece in _chunk_word(word, font, max_w):
            ww = _measure(font, piece)
            if cur and (cur_w + space_w + ww) > max_w:
                lines.append(cur)
                cur, cur_w = [], 0.0
            if cur:
                cur_w += space_w
            cur.append(piece)
            cur_w += ww
    lines.append(cur if cur else [])
    return lines


def _line_w(font: ImageFont.ImageFont, line: Sequence[str]) -> float:
    """Pixel width of a wrapped row as drawn (words joined by single spaces)."""
    if not line:
        return 0.0
    space_w = _measure(font, " ")
    return sum(_measure(font, w) for w in line) + space_w * (len(line) - 1)


def _wrap_text(text: str, font: ImageFont.ImageFont, max_w: float) -> List[List[str]]:
    """Word-wrap ``text`` honouring explicit newlines.

    Each ``\n``-separated paragraph is wrapped independently, so a hard line break
    (and its empty rows) becomes its own layout row.  The rows are later both measured
    for height and drawn one-per-baseline by :func:`_draw_bubble` / :func:`_draw_system`.
    """
    lines: List[List[str]] = []
    for para in re.split(r"\r?\n", text or ""):
        lines.extend(_wrap_words(_split_words(para), font, max_w))
    return lines


def _line_height(font: ImageFont.ImageFont, multiline: bool) -> int:
    size = getattr(font, "size", 15) or 15
    return int(size * (1.55 if multiline else 1.35))


@dataclass
class Block:
    kind: str
    y: int = 0
    h: int = 0
    text: str = ""
    font: object = None
    text_color: tuple = ()
    bg_color: tuple = ()
    is_right: bool = False
    lines: List[List[str]] = field(default_factory=list)
    rect_w: int = 0
    rect_h: int = 0


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------

def _layout_block(msg: dict, font_size: int, content_w: int, palette: Dict[str, str]) -> Block:
    kind = msg.get("kind", "assistant")
    text = msg.get("text", "") or ""
    block = Block(kind=kind, text=text)

    if kind == "system":
        font = font_for("body", SMALL_FONT)
        block.font = font
        block.lines = _wrap_text(text, font, content_w - 2 * MARGIN)
        block.h = _line_height(font, len(block.lines) > 1) * len(block.lines)
        return block

    # bubble message
    if kind in ("tool_call", "tool_result"):
        block.font = font_for("mono", TOOL_FONT)
        block.text_color = to_rgb(palette.get("tool_fg", "#713f12"))
        block.bg_color = to_rgb(palette.get("tool_bubble", "#fef9c3"))
    elif kind == "user":
        block.font = font_for("body", font_size)
        block.text_color = to_rgb(palette.get("user_fg", "#1e3a8a"))
        block.bg_color = to_rgb(palette.get("user_bubble", "#dbeafe"))
    elif kind == "error":
        block.font = font_for("body", font_size)
        block.text_color = to_rgb(palette.get("error_fg", "#991b1b"))
        block.bg_color = to_rgb(palette.get("error_bubble", "#fee2e2"))
    else:
        block.font = font_for("body", font_size)
        block.text_color = to_rgb(palette.get("assistant_fg", "#1c1c22"))
        block.bg_color = to_rgb(palette.get("assistant_bubble", "#ffffff"))

    block.is_right = kind == "user"
    avail = max(120, content_w - 2 * MARGIN)
    lines = _wrap_text(text, block.font, avail - 2 * PAD_X)
    block.lines = lines
    lh = _line_height(block.font, len(lines) > 1)
    text_w = max((_line_w(block.font, ln) for ln in lines), default=0.0)
    text_h = lh * len(lines)
    rect_w = min(int(text_w) + 2 * PAD_X, content_w)
    rect_h = text_h + 2 * PAD_Y
    block.rect_w = rect_w
    block.rect_h = rect_h
    block.h = rect_h
    return block


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

@dataclass
class Render:
    image: Image.Image
    content_w: int
    content_h: int


def _draw_rounded(draw: ImageDraw.ImageDraw, box, radius, fill, outline=None, width=0):
    try:
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
    except TypeError:  # pragma: no cover - very old Pillow
        draw.rounded_rectangle(box, radius=radius, fill=fill)


def render_chat(
    messages: Sequence[dict],
    palette: Dict[str, str],
    width: int,
    *,
    font_size: int = BODY_FONT,
    max_width: int = MAX_WIDTH,
) -> Render:
    """Render ``messages`` (list of ``{"kind", "text"}``) into an image.

    ``width`` is the *viewport* content width; the returned image is as tall as needed
    and is filled with the theme background colour (so gaps between messages are not
    transparent).
    """
    palette = palette or {}
    content_w = max(260, min(int(width), max_width))
    bg = to_rgb(palette.get("chat_bg", "#fafafe"))

    blocks = [_layout_block(m, font_size, content_w, palette) for m in messages]

    # Layout pass: assign vertical positions.
    y = MARGIN
    for b in blocks:
        b.y = y
        y += b.h + H_GAP
    total_h = max(y - H_GAP, MARGIN)

    img = Image.new("RGBA", (content_w, total_h), bg)
    draw = ImageDraw.Draw(img)

    for b in blocks:
        if b.kind == "system":
            _draw_system(draw, b, content_w, palette)
        else:
            _draw_bubble(draw, b, content_w, bg)

    return Render(img, content_w, total_h)


def _draw_system(draw: ImageDraw.ImageDraw, b: Block, content_w: int, palette: Dict[str, str]) -> None:
    font = b.font
    color = to_rgb(palette.get("muted", "#8a8a95"))
    cx = content_w // 2
    lh = _line_height(font, len(b.lines) > 1)
    # Start near the middle of the (multi-line) block and step down one row at a time.
    ty = b.y + lh * len(b.lines) // 2
    for line in b.lines:
        if not line:
            ty += lh
            continue
        tx = cx - _line_w(font, line) / 2
        for word in line:
            draw.text((tx, ty), word, font=font, fill=color)
            tx += _measure(font, word) + _measure(font, " ")
        ty += lh


def _draw_bubble(draw: ImageDraw.ImageDraw, b: Block, content_w: int, bg: tuple) -> None:
    radius = 16
    if b.is_right:
        bx2 = content_w - MARGIN
        bx1 = bx2 - b.rect_w
    else:
        bx1 = MARGIN
        bx2 = bx1 + b.rect_w
    _draw_rounded(draw, [bx1, b.y, bx2, b.y + b.rect_h], radius=radius, fill=b.bg_color)

    if b.is_right:
        tx = bx2 - PAD_X
        align = "right"
    else:
        tx = bx1 + PAD_X
        align = "left"
    ty = b.y + PAD_Y
    for line in b.lines:
        cursor = tx
        words = list(reversed(line)) if align == "right" else line
        for word in words:
            ww = _measure(b.font, word)
            if align == "right":
                cursor -= ww
                draw.text((cursor, ty), word, font=b.font, fill=b.text_color)
                cursor -= _measure(b.font, " ")
            else:
                draw.text((cursor, ty), word, font=b.font, fill=b.text_color)
                cursor += ww + _measure(b.font, " ")
        ty += _line_height(b.font, len(b.lines) > 1)
