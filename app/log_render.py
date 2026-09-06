"""Render the application log as a raster image (headless Pillow).

Turn the ring-buffer entries produced by :mod:`app.logs` into one tall RGBA image that
:class:`~app.gui.log_view.LogView` then shows.  Kept Pillow-only so it runs in headless
tests exactly like :mod:`app.chat_render`.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from .chat_render import _line_height, font_for
from .theme import to_rgb

MARGIN_X = 12
MARGIN_Y = 8
FONT_SIZE = 13
ROW_GAP = 2
CONTINUATION_INDENT = 14  # px before wrapped continuation lines

LEVEL_COLORS = {
    "DEBUG": "#8a8a95",
    "INFO": None,          # None → theme fg
    "WARNING": "#d97706",
    "ERROR": "#dc2626",
    "CRITICAL": "#b91c1c",
}


@dataclass
class LogRender:
    image: Image.Image
    content_w: int
    content_h: int


def _mono(size: int, bold: bool = False) -> ImageFont.ImageFont:
    return font_for("mono", size, bold=bold)


def _wrap(text: str, font: ImageFont.ImageFont, max_w: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = word if not current else f"{current} {word}"
        if not current or font.getlength(candidate) <= max_w:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _level_color(level: str, palette: dict) -> tuple:
    hex_color = LEVEL_COLORS.get(level.upper(), "#44444f")
    if hex_color is None:
        hex_color = palette.get("fg", "#44444f")
    return to_rgb(hex_color)


def render_log(entries: list[dict], palette: dict, width: int) -> LogRender:
    """Render log entries (each ``{"time","level","text"}``) into an RGBA image."""
    palette = palette or {}
    content_w = max(320, min(int(width), 1400))
    font = _mono(FONT_SIZE)
    bold = _mono(FONT_SIZE, bold=True)
    lh = _line_height(font, True)
    muted = to_rgb(palette.get("muted", "#8a8a95"))

    # Pre-compute draw rows: (prefix, prefix_color, text, color, bold, indent_for_text)
    rows: list[tuple] = []
    for entry in entries[-800:]:
        stamp = str(entry.get("time", ""))
        level = str(entry.get("level", "INFO")).upper()
        text = str(entry.get("text", ""))
        color = _level_color(level, palette)
        is_bold = level == "CRITICAL"
        f = bold if is_bold else font
        prefix = f"{stamp} {level:<8}"
        prefix_w = int(f.getlength(prefix))
        text_lines = _wrap(text, f, content_w - MARGIN_X * 2 - prefix_w - 6)
        if not text_lines:
            text_lines = [""]
        rows.append((prefix, muted, text_lines[0], color, is_bold,
                     MARGIN_X + prefix_w + 6))
        for continuation in text_lines[1:]:
            rows.append(("", muted, continuation, color, is_bold,
                         MARGIN_X + CONTINUATION_INDENT))

    if not rows:
        rows.append(("", muted, "(no log entries yet)", muted, False,
                     MARGIN_X + CONTINUATION_INDENT))

    total_h = MARGIN_Y * 2 + lh * len(rows) + ROW_GAP * (len(rows) - 1)
    bg_hex = palette.get("chat_bg") or palette.get("bg") or "#ffffff"
    img = Image.new("RGBA", (content_w, max(1, total_h)), to_rgb(bg_hex))
    draw = ImageDraw.Draw(img)

    y = MARGIN_Y
    for prefix, prefix_color, text, color, is_bold, text_x in rows:
        f = bold if is_bold else font
        if prefix:
            draw.text((MARGIN_X, y), prefix, font=f, fill=prefix_color)
        draw.text((text_x, y), text, font=f, fill=color)
        y += lh + ROW_GAP

    return LogRender(img, content_w, total_h)
