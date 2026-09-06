"""A small, dependency-light Markdown renderer that rasterises pages with Pillow.

The goal is *perfect, predictable rendering* of books on screen without pulling in a
full HTML engine or Cairo. Markdown is parsed into blocks, each block is laid out and
drawn onto a raster image, and the image is handed to a scrollable :class:`Canvas` by the
GUI.

Supported: ATX headings, bold/italic/strikethrough, inline code, links, images, fenced
code blocks (with a grey backdrop), blockquotes, ordered/unordered (nestable) lists,
GFM tables, horizontal rules and paragraphs.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence, Union

from PIL import Image, ImageDraw

# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------

_FONT_CANDIDATES = [
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/ubuntu"),
    Path("/usr/share/fonts/opentype/noto"),
    Path("/Library/Fonts"),
    Path("~/Library/Fonts"),
    Path("/usr/share/fonts/truetype"),
]


def _first_existing(names: Sequence[str]) -> Optional[Path]:
    for base in _FONT_CANDIDATES:
        base = base.expanduser()
        if not base.exists():
            continue
        for name in names:
            p = base / name
            if p.exists():
                return p
    return None


def _load_font(path: Optional[Path], size: int):
    from PIL import ImageFont

    try:
        if path and path.exists():
            return ImageFont.truetype(str(path), size)
    except Exception:
        pass
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


@dataclass
class FontSet:
    """The typefaces used while laying out a page (all at a base size)."""

    body: object
    bold: object
    italic: object
    bold_italic: object
    mono: object

    @classmethod
    def system(cls) -> "FontSet":
        serif = _first_existing(["DejaVuSerif.ttf", "Ubuntu-R.ttf"])
        bold = _first_existing(["DejaVuSerif-Bold.ttf", "Ubuntu-B.ttf"])
        italic = _first_existing(["DejaVuSerif-Italic.ttf", "Ubuntu-I.ttf"])
        bolditalic = _first_existing(["DejaVuSerif-BoldItalic.ttf", "Ubuntu-BI.ttf"])
        mono = _first_existing(["DejaVuSansMono.ttf", "UbuntuMono-R.ttf"])
        size = 16
        return cls(
            body=_load_font(serif, size),
            bold=_load_font(bold, size),
            italic=_load_font(italic, size),
            bold_italic=_load_font(bolditalic, size),
            mono=_load_font(mono, size),
        )

    def scaled(self, size: int) -> "FontSet":
        """Return a copy of these fonts resized to ``size`` (Pillow >= 10)."""

        def sz(f: object) -> object:
            try:
                return f.resize(int(round(size)))  # type: ignore[attr-defined]
            except Exception:
                return f

        return FontSet(
            body=sz(self.body),
            bold=sz(self.bold),
            italic=sz(self.italic),
            bold_italic=sz(self.bold_italic),
            mono=sz(self.mono),
        )


# --------------------------------------------------------------------------
# Colours
# --------------------------------------------------------------------------

@dataclass
class Palette:
    text: tuple = (24, 24, 28)
    bg: tuple = (255, 255, 255)
    muted: tuple = (90, 90, 100)
    heading: tuple = (15, 20, 40)
    code_bg: tuple = (246, 247, 249)
    code_text: tuple = (30, 30, 34)
    accent: tuple = (37, 99, 235)
    quote_bar: tuple = (37, 99, 235)
    table_border: tuple = (214, 217, 224)
    table_head_bg: tuple = (236, 239, 246)
    hr: tuple = (220, 220, 224)
    code_placeholder: tuple = (200, 200, 205)


DEFAULT_PALETTE = Palette()


# --------------------------------------------------------------------------
# Inline tokens
# --------------------------------------------------------------------------

@dataclass
class Span:
    text: str
    styles: tuple = ()  # subset of: bold, italic, code, strikethrough, link


def parse_inline(text: str) -> list[Span]:
    """Split inline Markdown into a flat list of :class:`Span` objects."""
    spans: list[Span] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]

        if c == "`":  # inline code
            end = text.find("`", i + 1)
            if end != -1 and end > i:
                spans.append(Span(text[i + 1:end], ("code",)))
                i = end + 1
                continue

        if c == "!" and i + 1 < n and text[i + 1] == "[":  # image
            m = re.match(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)", text[i:])
            if m:
                spans.append(Span(m.group(1), ("image",)))
                spans.append(Span(m.group(2), ("image_path",)))
                i += m.end()
                continue

        if c == "[":  # link
            m = re.match(r"\[([^\]]*)\]\(([^)\s]+)\)", text[i:])
            if m and m.group(1):
                spans.append(Span(m.group(1), ("link",)))
                spans.append(Span(m.group(2), ("link_path",)))
                i += m.end()
                continue

        if c == "~" and i + 1 < n and text[i + 1] == "~":  # strikethrough
            end = text.find("~~", i + 2)
            if end != -1:
                spans.append(Span(text[i + 2:end], ("strikethrough",)))
                i = end + 2
                continue

        if c == "*" and i + 2 < n and text[i + 1] == "*" and text[i + 2] == "*":  # ***/***
            end = text.find("***", i + 3)
            if end != -1:
                spans.append(Span(text[i + 3:end], ("bold", "italic")))
                i = end + 3
                continue
        if c == "_" and i + 2 < n and text[i + 1] == "_" and text[i + 2] == "_":
            end = text.find("___", i + 3)
            if end != -1:
                spans.append(Span(text[i + 3:end], ("bold", "italic")))
                i = end + 3
                continue

        if c == "*" and i + 1 < n and text[i + 1] == "*":  # **
            end = text.find("**", i + 2)
            if end != -1 and end > i + 1:
                spans.append(Span(text[i + 2:end], ("bold",)))
                i = end + 2
                continue
        if c == "_" and i + 1 < n and text[i + 1] == "_" and i + 2 < n and text[i + 2] != "_":
            end = text.find("__", i + 2)
            if end != -1 and end > i + 1:
                spans.append(Span(text[i + 2:end], ("bold",)))
                i = end + 2
                continue

        if c == "*":  # single *
            end = text.find("*", i + 1)
            if end != -1 and end != i + 1 and (end + 1 >= n or text[end + 1] != "*"):
                spans.append(Span(text[i + 1:end], ("italic",)))
                i = end + 1
                continue

        if c == "_" and i + 1 < n and text[i + 1] != "_":  # single _
            end = text.find("_", i + 1)
            if end != -1 and end != i + 1:
                spans.append(Span(text[i + 1:end], ("italic",)))
                i = end + 1
                continue

        spans.append(Span(c, ()))  # plain char
        i += 1

    merged: list[Span] = []
    for s in spans:
        if s.styles == () and merged and merged[-1].styles == ():
            merged[-1] = Span(merged[-1].text + s.text, ())
        else:
            merged.append(s)
    return merged


def _to_spans(x: Union[str, list[Span]]) -> list[Span]:
    return x if isinstance(x, list) else parse_inline(x)


def _font_for(styles: tuple, fonts: FontSet) -> object:
    if "code" in styles:
        return fonts.mono
    if "bold" in styles and "italic" in styles:
        return fonts.bold_italic
    if "bold" in styles:
        return fonts.bold
    if "italic" in styles:
        return fonts.italic
    return fonts.body


def _measure(font: object, text: str) -> float:
    if not text:
        return 0.0
    try:
        return float(font.getlength(text))
    except Exception:
        return float(font.getbbox(text)[2])


def _leading(size: float, multiline: bool) -> float:
    return size * 1.55 if multiline else size * 1.35


def wrap_inline(spans: Union[str, list[Span]], fonts: FontSet, max_w: float):
    """Wrap inline spans into lines.

    Returns ``(lines, width)`` where ``lines`` is a list of ``(text, font)`` tuples.
    """
    span_list = _to_spans(spans)
    tokens: list[tuple[str, object]] = []
    for span in span_list:
        font = _font_for(span.styles, fonts)
        words = span.text.split(" ")
        for idx, w in enumerate(words):
            tokens.append((w, font))
            if idx < len(words) - 1:
                tokens.append((" ", font))

    lines: list[list[tuple[str, object]]] = []
    cur: list[tuple[str, object]] = []
    cur_w = 0.0
    space_w = 0.0
    cap = 0.0

    def flush():
        nonlocal cur, cur_w
        if cur:
            lines.append(cur)
        cur = []
        cur_w = 0.0

    for text, font in tokens:
        w = _measure(font, text)
        if text == " ":
            space_w = w
        cap = max(cap, w)
        if cur and (cur_w + w + space_w) > max_w:
            flush()
        cur.append((text, font))
        cur_w += w + (space_w if text == " " else 0.0)
    flush()
    if not lines:
        lines.append([])
    return lines, cap


# --------------------------------------------------------------------------
# Block parser
# --------------------------------------------------------------------------

@dataclass
class Block:
    type: str
    data: dict = field(default_factory=dict)


def _split_lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*```(.*)$")
_HR_RE = re.compile(r"^\s*([-*_])( *\1){2,} *$")
_UL_RE = re.compile(r"^\s*([-*+])\s+(.*)$")
_OL_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_QUOTE_RE = re.compile(r"^\s*(>)\s*(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")


def _is_blank(line: str) -> bool:
    return line.strip() == ""


def _is_block_start(line: str) -> bool:
    if _is_blank(line):
        return True
    if _HEADING_RE.match(line) or _FENCE_RE.match(line) or _HR_RE.match(line):
        return True
    if _QUOTE_RE.match(line) or _UL_RE.match(line) or _OL_RE.match(line):
        return True
    if line.strip().startswith("|"):
        return True
    return False


def _split_table_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _parse_table_aligns(line: str) -> list[str]:
    aligns = []
    for c in _split_table_row(line):
        left = c.startswith(":")
        right = c.endswith(":")
        if left and right:
            aligns.append("center")
        elif right:
            aligns.append("right")
        elif left:
            aligns.append("left")
        else:
            aligns.append("left")
    return aligns


def _parse_list(lines, i, ordered, start=1):
    """Parse a (possibly nested) list starting at ``i``. Returns (items, new_i)."""
    items: list[dict] = []
    n = len(lines)
    base_indent = None
    while i < n:
        line = lines[i]
        if _is_blank(line):
            break
        m = _UL_RE.match(line) or _OL_RE.match(line)
        if not m:
            break
        indent = len(line) - len(line.lstrip())
        if base_indent is None:
            base_indent = indent
        elif indent < base_indent:
            break
        content = m.group(2)
        number = int(_OL_RE.match(line).group(1)) if _OL_RE.match(line) else None
        item = {"text": content, "number": number, "children": []}
        sub_i = i + 1
        child_items, sub_i = _parse_list(lines, sub_i, ordered, (number if ordered else start))
        if child_items and (sub_i > i + 1):
            item["children"] = child_items
        items.append(item)
        i = sub_i
    return items, i


def parse_blocks(raw: str) -> list[Block]:
    """Parse Markdown into a list of block dictionaries the layout step understands."""
    lines = _split_lines(raw)
    blocks: list[Block] = []
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]
        if _is_blank(line):
            i += 1
            continue

        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            blocks.append(Block("heading", {"level": level, "spans": parse_inline(m.group(2))}))
            i += 1
            continue

        m = _FENCE_RE.match(line)
        if m:
            lang = m.group(1).strip()
            code_lines: list[str] = []
            i += 1
            while i < n and not _FENCE_RE.match(lines[i]):
                code_lines.append(lines[i])
                i += 1
            if i < n:
                i += 1
            blocks.append(Block("code", {"lang": lang, "text": "\n".join(code_lines)}))
            continue

        if _HR_RE.match(line):
            blocks.append(Block("hr", {}))
            i += 1
            continue

        if _QUOTE_RE.match(line):
            q_lines: list[str] = []
            while i < n and (_QUOTE_RE.match(lines[i]) or not _is_blank(lines[i])):
                qm = _QUOTE_RE.match(lines[i])
                q_lines.append(qm.group(2) if qm else lines[i])
                i += 1
            blocks.append(Block("blockquote", {"blocks": parse_blocks("\n".join(q_lines))}))
            continue

        if "|" in line and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            header = [c.strip() for c in _split_table_row(line)]
            aligns = _parse_table_aligns(lines[i + 1])
            rows = []
            j = i + 2
            while j < n and "|" in lines[j] and not _is_blank(lines[j]):
                rows.append([c.strip() for c in _split_table_row(lines[j])])
                j += 1
            blocks.append(Block("table", {"header": header, "rows": rows, "aligns": aligns}))
            i = j
            continue

        if _UL_RE.match(line):
            items, i = _parse_list(lines, i, ordered=False)
            blocks.append(Block("ul", {"items": items}))
            continue

        if _OL_RE.match(line):
            start = int(_OL_RE.match(line).group(1))
            items, i = _parse_list(lines, i, ordered=True, start=start)
            blocks.append(Block("ol", {"items": items, "start": start}))
            continue

        p_lines: list[str] = []
        while i < n and not _is_blank(lines[i]) and not _is_block_start(lines[i]):
            p_lines.append(lines[i])
            i += 1
        text = " ".join(p_lines).strip()
        if text:
            blocks.append(Block("paragraph", {"spans": parse_inline(text)}))

    return blocks


# --------------------------------------------------------------------------
# Image helpers
# --------------------------------------------------------------------------

def _resolve_image(path: str, image_dir: Optional[Path]) -> Optional[Image.Image]:
    try:
        if path.startswith("data:"):
            from .images import base64_to_image

            return base64_to_image(path)
        p = Path(path)
        if p.is_absolute() and p.exists():
            img = Image.open(p)
            img.load()
            return img.convert("RGBA")
        if image_dir is not None:
            cand = image_dir / path
            if cand.exists():
                img = Image.open(cand)
                img.load()
                return img.convert("RGBA")
    except Exception:
        return None
    return None


# --------------------------------------------------------------------------
# Renderer
# --------------------------------------------------------------------------

# A primitive is (type, ...) and is drawn by ``draw``.
Item = tuple


class Renderer:
    """Lays out parsed blocks onto a raster page."""

    def __init__(self, fonts: FontSet, palette: Palette, max_text_width: float, margin: float):
        self.fonts = fonts
        self.palette = palette
        self.max_text_width = max(260.0, max_text_width)
        self.margin = margin
        self.items: list[Item] = []
        self.y = 0
        self.indent = margin
        self._measured_width = 2 * margin

    # -- public -----------------------------------------------------------
    def render(self, blocks: list[Block]) -> tuple[list[Item], int, int]:
        for block in blocks:
            self._render_block(block)
        page_width = self._measured_width
        page_height = self.y + self.margin
        return self.items, page_width, page_height

    # -- helpers ----------------------------------------------------------
    def _emit(self, y: int, prim: Item) -> None:
        self.items.append((y, prim))

    def _render_block(self, block: Block) -> None:
        t = block.type
        if t == "heading":
            self._heading(block)
        elif t == "paragraph":
            self._paragraph(block)
        elif t == "code":
            self._code(block)
        elif t == "hr":
            self._hr(block)
        elif t == "blockquote":
            self._blockquote(block)
        elif t == "table":
            self._table(block)
        elif t in ("ul", "ol"):
            ordered = t == "ol"
            self._render_items(block.data["items"], ordered, block.data.get("start", 1), 0)

    # -- individual blocks ------------------------------------------------
    def _heading(self, block: Block) -> None:
        level = block.data["level"]
        size = [32, 25, 20, 17, 15, 14][level - 1]
        fset = self.fonts.scaled(size)
        lines, w = wrap_inline(block.data["spans"], fset, self.max_text_width)
        lh = _leading(size, len(lines) > 1)
        h = int(lh * len(lines))
        self._emit(self.y, ("text_block", self.margin, self.y, lines, size, self.palette.heading))
        self.y += h + 14
        self._measured_width = max(self._measured_width, w + 2 * self.margin)

    def _paragraph(self, block: Block) -> None:
        spans = block.data["spans"]
        # Block-level image? ``![alt](path)`` on its own line.
        if len(spans) == 2 and spans[0].styles == ("image",) and spans[1].styles == ("image_path",):
            h = self._image(spans[1].text)
            self.y += h + 12
            return
        fset = self.fonts.scaled(16)
        lines, w = wrap_inline(spans, fset, self.max_text_width)
        lh = _leading(16, len(lines) > 1)
        h = int(lh * len(lines))
        self._emit(self.y, ("text_block", self.margin, self.y, lines, 16, self.palette.text))
        self.y += h + 12
        self._measured_width = max(self._measured_width, w + 2 * self.margin)

    def _image(self, path: str) -> int:
        img = _resolve_image(path, getattr(self, "_image_dir", None))
        box_w = int(self.max_text_width)
        if img is None:
            placeholder = Image.new("RGBA", (box_w, 160), self.palette.code_placeholder + (255,))
            d = ImageDraw.Draw(placeholder)
            d.text((12, 70), "[ image unavailable ]", fill=self.palette.muted,
                   font=self.fonts.scaled(14).body)
            img = placeholder
        ratio = min(box_w / img.width, 300 / img.height)
        new_size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
        img = img.resize(new_size, Image.LANCZOS)
        self._emit(self.y, ("image", self.margin, self.y, img))
        return new_size[1]

    def _code(self, block: Block) -> None:
        fset = self.fonts.scaled(13)
        src = block.data["text"]
        code_lines = src.split("\n") if src else [""]
        line_h = int(_leading(13, True))
        pad = 12
        code_w = max(_measure(fset.mono, ln) for ln in code_lines) if code_lines else 0
        total_w = int(code_w) + 2 * pad
        self._measured_width = max(self._measured_width, total_w)
        img_h = line_h * len(code_lines) + 2 * pad
        self._emit(self.y, ("code_block", self.margin, self.y, src, fset.mono, line_h,
                             len(code_lines), pad, self.palette))
        self.y += img_h + 12

    def _hr(self, block: Block) -> None:
        self._emit(self.y, ("hr", self.margin, self.y, self.max_text_width, self.palette))
        self.y += 16

    def _blockquote(self, block: Block) -> None:
        sub = Renderer(self.fonts, self.palette, self.max_text_width, self.margin)
        sub._image_dir = getattr(self, "_image_dir", None)
        sub.render(block.data["blocks"])
        bar_h = sub.y + 12
        self._emit(self.y, ("quote_bar", self.margin, self.y, bar_h, self.palette.quote_bar))
        for y, prim in sub.items:
            self._emit(self.y + y, prim)
        self.y += bar_h

    def _table(self, block: Block) -> None:
        header = block.data.get("header", [])
        rows = block.data.get("rows", [])
        aligns = block.data.get("aligns", ["left"] * max(len(header), 1))
        ncols = max(len(header), *[len(r) for r in rows], 1)
        aligns = (aligns + ["left"] * ncols)[:ncols]
        fset = self.fonts.scaled(14)

        def wrap(cell: str) -> list:
            return wrap_inline(cell, fset, 1)

        col_w = [0.0] * ncols
        table_lines: list[list] = []
        for rowdata in [header] + rows:
            row_lines = []
            for c in range(ncols):
                cell = rowdata[c] if c < len(rowdata) else ""
                lines, w = wrap(cell)
                col_w[c] = max(col_w[c], w + 20)
                row_lines.append(lines)
            table_lines.append(row_lines)

        total_w = sum(col_w) + 20 * ncols
        budget = self.max_text_width
        if total_w > budget:
            scale = (budget - 20 * ncols) / (sum(col_w) or 1)
            col_w = [c * scale for c in col_w]
            total_w = budget
        self._measured_width = max(self._measured_width, total_w)

        row_h = 28
        total_h = (len(table_lines) + 1) * row_h + 16
        self._emit(self.y, ("table", self.margin, self.y, table_lines, col_w, aligns, row_h,
                            self.palette))
        self.y += total_h

    def _render_items(self, items, ordered, start, level) -> None:
        fset = self.fonts.scaled(15)
        pad = level * 22
        indent = self.margin + pad
        self.indent = indent
        for item in items:
            prefix = f"{item['number']}. " if (ordered and item.get("number")) else "- "
            lines, w = wrap_inline(item["text"], fset, self.max_text_width - 30)
            lh = _leading(15, len(lines) > 1)
            item_h = int(lh * max(1, len(lines)))
            marker_w = _measure(fset.body, prefix) + 8
            self._emit(self.y, ("list_marker", indent, self.y, prefix, fset.body, self.palette.text))
            self._emit(self.y, ("text_block", indent + marker_w, self.y, lines, 15, self.palette.text))
            self.y += item_h + 8
            if item.get("children"):
                self._render_items(item["children"], ordered, item.get("number", start), level + 1)
        self._measured_width = max(self._measured_width, indent + w)


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------

def _draw_text_block(draw, x, y, lines, size, color, palette) -> None:
    for line in lines:
        tx = x
        for text, font in line:
            if text == " ":
                tx += _measure(font, " ")
                continue
            draw.text((tx, y), text, font=font, fill=color)
            tx += _measure(font, text)
        y += int(size * 1.55)


def _draw_code_block(draw, x, y, text, mono_font, line_h, count, pad, palette) -> None:
    box_w = int(max(_measure(mono_font, ln) for ln in text.split("\n"))) + 2 * pad if text else 2 * pad
    box_h = line_h * count + 2 * pad
    draw.rounded_rectangle([x, y, x + box_w, y + box_h], radius=6, fill=palette.code_bg)
    tx = x + pad
    ty = y + pad
    for ln in text.split("\n"):
        draw.text((tx, ty), ln, font=mono_font, fill=palette.code_text)
        ty += line_h


def _draw_table(draw, x, y, table_lines, col_w, aligns, row_h, palette) -> None:
    cx = x
    col_x = []
    for cw in col_w:
        col_x.append(cx)
        cx += cw

    def draw_row(row_lines, fill) -> None:
        ry = y
        for ci, lines in enumerate(row_lines):
            cw = col_w[ci]
            align = aligns[ci] if ci < len(aligns) else "left"
            tx = x + col_x[ci] + (6 if align == "right" else 8)
            if fill:
                draw.rectangle([x + (sum(col_w[:ci]) if ci else 0), y,
                                x + sum(col_w[:ci + 1]), y + row_h], fill=fill)
            for line in lines:
                ltx = tx
                for text, font in line:
                    if text == " ":
                        ltx += _measure(font, " ")
                        continue
                    if align == "right":
                        ltx = x + col_x[ci] + cw - 8 - _measure(font, text)
                    elif align == "center":
                        ltx = x + col_x[ci] + (cw - _measure(font, text)) / 2
                    draw.text((ltx, y + 5), text, font=font, fill=palette.text)
                    ltx += _measure(font, text)
            y += row_h

    draw_row(table_lines[0], palette.table_head_bg)
    for row in table_lines[1:]:
        draw_row(row, None)

    # grid lines
    gy = y
    x0 = x
    x1 = x + sum(col_w)
    draw.line([x0, y - row_h, x1, y - row_h], fill=palette.table_border, width=1)
    vertical = [x0]
    for cw in col_w:
        vertical.append(vertical[-1] + cw)
    for vx in vertical:
        draw.line([vx, y - row_h, vx, gy], fill=palette.table_border, width=1)
    for i in range(len(table_lines)):
        ly = y - row_h * i
        draw.line([x0, ly, x1, ly], fill=palette.table_border, width=1)


def draw(items, page_width, page_height, palette: Palette = DEFAULT_PALETTE) -> Image.Image:
    """Draw pre-computed ``items`` onto a fresh RGBA image."""
    img = Image.new("RGBA", (max(1, int(page_width)), max(1, int(page_height))), palette.bg)
    draw = ImageDraw.Draw(img)
    for y, prim in sorted(items, key=lambda it: it[0]):
        kind = prim[0]
        try:
            if kind == "text_block":
                _, x, ty, lines, size, color = prim
                _draw_text_block(draw, x, ty, lines, size, color, palette)
            elif kind == "list_marker":
                _, x, ty, prefix, font, color = prim
                draw.text((x, ty), prefix, font=font, fill=color)
            elif kind == "code_block":
                _draw_code_block(draw, *prim[1:], palette)
            elif kind == "hr":
                _, x, _, w, _ = prim
                draw.line([(x, y), (x + w, y)], fill=palette.hr, width=2)
            elif kind == "quote_bar":
                _, x, _, bar_h, color = prim
                draw.rectangle([x, y, x + 4, y + bar_h], fill=color)
            elif kind == "table":
                _draw_table(draw, *prim[1:], palette)
            elif kind == "image":
                _, x, ty, pil = prim
                img.paste(pil, (x, ty), pil)
        except Exception:
            continue
    return img


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def render_markdown(
    markdown_text: str,
    *,
    text_width: float = 760,
    margin: float = 36,
    fonts: Optional[FontSet] = None,
    palette: Optional[Palette] = None,
    image_dir: Optional[Path] = None,
) -> Image.Image:
    """Render a Markdown string to a PNG-ready RGBA :class:`~PIL.Image.Image`."""
    fonts = fonts or FontSet.system()
    palette = palette or DEFAULT_PALETTE
    blocks = parse_blocks(markdown_text or "")
    renderer = Renderer(fonts, palette, text_width, margin)
    renderer._image_dir = image_dir
    items, page_width, page_height = renderer.render(blocks)
    if page_width < 1:
        page_width = text_width + 2 * margin
    if page_height < 1:
        page_height = 60
    return draw(items, page_width, page_height, palette)
