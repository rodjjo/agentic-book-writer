r"""A powerful, dependency-light Markdown renderer that rasterises pages with Pillow.

The goal is *perfect, predictable rendering* of books and rich documents on screen without
pulling in a full HTML engine or Cairo. Markdown is parsed into blocks, each block is laid
out and drawn onto a raster image, and the image is handed to a scrollable :class:`Canvas`
by the GUI.

Supported features:
- Inline formatting:
  * Bold (``**text**``, ``__text__``, ``<b>``, ``<strong>``)
  * Italic (``*text*``, ``_text_``, ``<i>``, ``<em>``)
  * Bold-italic (``***text***``, ``___text___``)
  * Strikethrough (``~~text~~``, ``<del>``, ``<s>``)
  * Inline code pills with border (`` `code` ``, ``<code>``)
  * Keyboard badges (``<kbd>key</kbd>``)
  * Highlight / Mark (``==text==``, ``<mark>``)
  * Underline (``++text++``, ``<u>``, ``<ins>``)
  * Superscript (``^text^``, ``<sup>``) & Subscript (``~text~``, ``<sub>``)
  * Links with accent color & underline (``[label](url)``, ``<https://...>``)
  * Arbitrary nesting of styles (e.g. bold italic links, highlighted bold code)
  * Smart typography: en-dashes, em-dashes, ellipses, copyright, arrows, math symbols
  * Escaped characters (``\*``, ``\_``, ``\[``, etc.)
  * Inline math (``$E=mc^2$``)

- Block-level features:
  * ATX Headings (``#`` through ``######``) and Setext Headings (``===``, ``---``)
  * GitHub-style callouts / alerts (``> [!NOTE]``, ``> [!TIP]``, ``> [!IMPORTANT]``,
    ``> [!WARNING]``, ``> [!CAUTION]``)
  * Fenced code blocks with language badge & syntax highlighting (Python, JS, TS,
    JSON, Bash, SQL, HTML/XML, C/Rust/Go)
  * Math blocks (``$$...$$``)
  * GFM Tables with cell inline markdown, alignments (:---:, :---, ---:), and zebra striping
  * GFM Task list checkboxes (``- [ ]``, ``- [x]``)
  * Ordered and unordered nested lists
  * Definition lists (``Term\\n: Definition``)
  * Blockquotes with accent bar and nested formatting
  * Horizontal rules (``---``, ``***``, ``___``)
  * Images (local file, data URI, or missing placeholder)
  * Standard ISO 216 A4 aspect ratio framing without text clipping
"""

from __future__ import annotations

import html
import io
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from PIL import Image, ImageDraw

# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------

_NON_PY_FONTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "non_py", "fonts")
)

_FONT_CANDIDATES = [
    Path(_NON_PY_FONTS_DIR),
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
                return f.font_variant(size=int(round(size)))  # type: ignore[attr-defined]
            except Exception:
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
    # Rich styling additions
    mark_bg: tuple = (255, 243, 191)
    code_pill_bg: tuple = (241, 243, 247)
    code_pill_border: tuple = (225, 228, 234)
    strikethrough: tuple = (120, 120, 130)
    table_zebra_bg: tuple = (250, 251, 253)
    checkbox_border: tuple = (156, 163, 175)
    checkbox_checked_bg: tuple = (37, 99, 235)
    checkbox_check: tuple = (255, 255, 255)
    code_badge_bg: tuple = (228, 232, 240)
    code_badge_text: tuple = (71, 85, 105)
    code_kw: tuple = (207, 34, 46)
    code_str: tuple = (10, 48, 105)
    code_comment: tuple = (106, 115, 125)
    code_num: tuple = (0, 92, 197)
    code_fn: tuple = (111, 66, 193)
    code_type: tuple = (149, 56, 0)


DEFAULT_PALETTE = Palette()


# --------------------------------------------------------------------------
# Inline tokens & formatting
# --------------------------------------------------------------------------

@dataclass
class Span:
    text: str
    styles: tuple = ()  # subset of: bold, italic, code, strikethrough, mark, underline, sup, sub, kbd, link, image
    link_url: Optional[str] = None


class Token:
    """A single measured piece of text preserving font, styling, and link data.

    Implements tuple-unpacking (``text, font = token``) for backwards compatibility.
    """

    __slots__ = ("text", "font", "styles", "link_url", "width")

    def __init__(self, text: str, font: object, styles: tuple = (), link_url: Optional[str] = None, width: Optional[float] = None):
        self.text = text
        self.font = font
        self.styles = tuple(styles)
        self.link_url = link_url
        self.width = _measure(font, text) if width is None else float(width)

    def __iter__(self):
        yield self.text
        yield self.font

    def __getitem__(self, idx: int) -> Any:
        if idx == 0:
            return self.text
        if idx == 1:
            return self.font
        if idx == 2:
            return self.styles
        if idx == 3:
            return self.link_url
        raise IndexError(f"Token index out of range: {idx}")

    def __len__(self) -> int:
        return 2

    def __repr__(self) -> str:
        return f"Token({self.text!r}, styles={self.styles})"


def _measure(font: object, text: str) -> float:
    if not text:
        return 0.0
    try:
        return float(font.getlength(text))  # type: ignore[attr-defined]
    except Exception:
        try:
            return float(font.getbbox(text)[2])  # type: ignore[attr-defined]
        except Exception:
            return float(len(text) * 9.0)


def _font_for(styles: tuple, fonts: FontSet) -> object:
    s = set(styles)
    if "code" in s or "kbd" in s:
        base = fonts.mono
    elif "bold" in s and "italic" in s:
        base = fonts.bold_italic
    elif "bold" in s:
        base = fonts.bold
    elif "italic" in s:
        base = fonts.italic
    else:
        base = fonts.body

    if "sup" in s or "sub" in s:
        try:
            sz = getattr(base, "size", 16)
            return base.font_variant(size=max(8, int(sz * 0.72)))  # type: ignore[attr-defined]
        except Exception:
            pass
    return base


def _smart_typography(text: str) -> str:
    """Convert common typography conventions into professional publisher glyphs."""
    t = text
    t = t.replace("---", "—")  # em-dash
    t = t.replace("--", "–")   # en-dash
    t = t.replace("...", "…")  # ellipsis
    t = re.sub(r"\([cC]\)", "©", t)
    t = re.sub(r"\([rR]\)", "®", t)
    t = re.sub(r"\([tT][mM]\)", "™", t)
    t = t.replace("<->", "↔").replace("->", "→").replace("<-", "←")
    t = t.replace("<=", "≤").replace(">=", "≥").replace("!=", "≠")
    t = t.replace("+-", "±")
    return t


# Escape map for backslash-escaped characters
_ESCAPE_MAP = {
    r"\*": "\uE001",
    r"\_": "\uE002",
    r"\`": "\uE003",
    r"\~": "\uE004",
    r"\=": "\uE005",
    r"\+": "\uE006",
    r"\^": "\uE007",
    r"\[": "\uE008",
    r"\]": "\uE009",
    r"\(": "\uE00A",
    r"\)": "\uE00B",
    r"\#": "\uE00C",
    r"\!": "\uE00D",
    r"\\": "\uE00E",
    r"\$": "\uE00F",
}
_UNESCAPE_MAP = {v: k[1:] for k, v in _ESCAPE_MAP.items()}


def _mask_escapes(text: str) -> str:
    for pat, rep in _ESCAPE_MAP.items():
        text = text.replace(pat, rep)
    return text


def _unmask_escapes(text: str) -> str:
    for rep, orig in _UNESCAPE_MAP.items():
        text = text.replace(rep, orig)
    return text


# Inline parser rules in order of precedence
_INLINE_PATTERNS = [
    # 1. Double/single backtick code
    ("code", re.compile(r"``([\s\S]*?)``|`([^`]+)`")),
    # 2. Images
    ("image", re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)")),
    # 3. Links
    ("link", re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)")),
    # 4. Autolinks
    ("autolink", re.compile(r"<((?:https?://|mailto:)[^>]+)>")),
    # 5. Paired HTML tags
    ("html_tag", re.compile(r"<(b|strong|i|em|del|s|strike|u|ins|mark|sup|sub|code|kbd)>(.*?)</\1>", re.IGNORECASE)),
    # 6. Bold + Italic (*** or ___)
    ("bold_italic", re.compile(r"(?:\*\*\*([^*]+?)\*\*\*|___([^_]+?)___)")),
    # 7. Bold (** or __)
    ("bold", re.compile(r"(?:(?<!\*)\*\*(?!\*)(.+?)(?<!\*)\*\*(?!\*)|(?<!_)__(?!_)(.+?)(?<!_)__(?!_))")),
    # 8. Italic (* or _)
    ("italic", re.compile(r"(?:(?<!\*)\*(?!\*)(?!\s)(.+?)(?<!\s)(?<!\*)\*(?!\*)|(?<!\w)(?<!_)_(?!_)(?![\s_])(.+?)(?<![\s_])(?<!_)_(?!_)(?!\w))")),
    # 9. Strikethrough (~~)
    ("strike", re.compile(r"~~([^~]+?)~~")),
    # 10. Highlight / Mark (==)
    ("mark", re.compile(r"==([^=]+?)==")),
    # 11. Underline (++)
    ("underline", re.compile(r"\+\+([^+]+)\+\+")),
    # 12. Superscript (^)
    ("sup", re.compile(r"\^([^^]+)\^")),
    # 13. Subscript (~)
    ("sub", re.compile(r"(?<![~])~([^~\s]+)~(?!~)")),
    # 14. Inline Math ($)
    ("math", re.compile(r"\$([^$\n]+)\$")),
]


def _recursive_parse(text: str, active_styles: tuple[str, ...], active_link: Optional[str]) -> list[Span]:
    """Recursively parse inline text into a list of styled Spans."""
    if not text:
        return []

    best_match = None
    best_rule = None
    best_start = len(text)

    for rule_name, pat in _INLINE_PATTERNS:
        m = pat.search(text)
        if m and m.start() < best_start:
            best_match = m
            best_rule = rule_name
            best_start = m.start()
            if best_start == 0:
                break

    if best_match is None:
        # Plain text chunk: unmask escapes, smart typography, unescape HTML entities
        plain = _unmask_escapes(html.unescape(text))
        if "code" not in active_styles:
            plain = _smart_typography(plain)
        return [Span(plain, active_styles, active_link)] if plain else []

    spans: list[Span] = []
    # Process text before the match
    pre = text[:best_match.start()]
    if pre:
        spans.extend(_recursive_parse(pre, active_styles, active_link))

    # Process the matched token
    m = best_match
    rule = best_rule

    if rule == "code":
        code_text = m.group(1) if m.group(1) is not None else m.group(2)
        code_text = _unmask_escapes(code_text)
        spans.append(Span(code_text, tuple(dict.fromkeys(active_styles + ("code",))), active_link))

    elif rule == "image":
        alt = _unmask_escapes(m.group(1))
        url = _unmask_escapes(m.group(2))
        spans.append(Span(alt, ("image",)))
        spans.append(Span(url, ("image_path",)))

    elif rule == "link":
        label = m.group(1)
        url = _unmask_escapes(m.group(2))
        inner_styles = tuple(dict.fromkeys(active_styles + ("link",)))
        spans.extend(_recursive_parse(label, inner_styles, url))

    elif rule == "autolink":
        url = _unmask_escapes(m.group(1))
        inner_styles = tuple(dict.fromkeys(active_styles + ("link",)))
        spans.append(Span(url, inner_styles, url))

    elif rule == "html_tag":
        tag = m.group(1).lower()
        content = m.group(2)
        tag_map = {
            "b": "bold", "strong": "bold",
            "i": "italic", "em": "italic",
            "del": "strikethrough", "s": "strikethrough", "strike": "strikethrough",
            "u": "underline", "ins": "underline",
            "mark": "mark", "sup": "sup", "sub": "sub",
            "code": "code", "kbd": "kbd",
        }
        style = tag_map.get(tag, "bold")
        if style in ("code", "kbd"):
            spans.append(Span(_unmask_escapes(content), tuple(dict.fromkeys(active_styles + (style,))), active_link))
        else:
            inner_styles = tuple(dict.fromkeys(active_styles + (style,)))
            spans.extend(_recursive_parse(content, inner_styles, active_link))

    elif rule == "bold_italic":
        content = m.group(1) if m.group(1) is not None else m.group(2)
        inner_styles = tuple(dict.fromkeys(active_styles + ("bold", "italic")))
        spans.extend(_recursive_parse(content, inner_styles, active_link))

    elif rule == "bold":
        content = m.group(1) if m.group(1) is not None else m.group(2)
        inner_styles = tuple(dict.fromkeys(active_styles + ("bold",)))
        spans.extend(_recursive_parse(content, inner_styles, active_link))

    elif rule == "italic":
        content = m.group(1) if m.group(1) is not None else m.group(2)
        inner_styles = tuple(dict.fromkeys(active_styles + ("italic",)))
        spans.extend(_recursive_parse(content, inner_styles, active_link))

    elif rule == "strike":
        content = m.group(1)
        inner_styles = tuple(dict.fromkeys(active_styles + ("strikethrough",)))
        spans.extend(_recursive_parse(content, inner_styles, active_link))

    elif rule == "mark":
        content = m.group(1)
        inner_styles = tuple(dict.fromkeys(active_styles + ("mark",)))
        spans.extend(_recursive_parse(content, inner_styles, active_link))

    elif rule == "underline":
        content = m.group(1)
        inner_styles = tuple(dict.fromkeys(active_styles + ("underline",)))
        spans.extend(_recursive_parse(content, inner_styles, active_link))

    elif rule == "sup":
        content = m.group(1)
        inner_styles = tuple(dict.fromkeys(active_styles + ("sup",)))
        spans.extend(_recursive_parse(content, inner_styles, active_link))

    elif rule == "sub":
        content = m.group(1)
        inner_styles = tuple(dict.fromkeys(active_styles + ("sub",)))
        spans.extend(_recursive_parse(content, inner_styles, active_link))

    elif rule == "math":
        content = m.group(1)
        inner_styles = tuple(dict.fromkeys(active_styles + ("italic",)))
        spans.extend(_recursive_parse(content, inner_styles, active_link))

    # Process remainder of text after the match
    post = text[best_match.end():]
    if post:
        spans.extend(_recursive_parse(post, active_styles, active_link))

    return spans


def parse_inline(text: str) -> list[Span]:
    """Parse Markdown inline formatting recursively with support for nested styles."""
    if not text:
        return []
    masked = _mask_escapes(text)
    raw_spans = _recursive_parse(masked, (), None)

    # Merge adjacent spans that share the exact same styles and link_url
    merged: list[Span] = []
    for s in raw_spans:
        if not s.text:
            continue
        if merged and merged[-1].styles == s.styles and merged[-1].link_url == s.link_url:
            merged[-1] = Span(merged[-1].text + s.text, s.styles, s.link_url)
        else:
            merged.append(s)
    return merged


def _to_spans(x: Union[str, list[Span]]) -> list[Span]:
    return x if isinstance(x, list) else parse_inline(x)


def _leading(size: float, multiline: bool) -> float:
    return size * 1.55 if multiline else size * 1.35


def wrap_inline(spans: Union[str, list[Span]], fonts: FontSet, max_w: float):
    """Wrap inline spans into lines while preserving rich style metadata.

    Returns ``(lines, width)`` where ``lines`` is a list of token lists.
    Each token supports unpacking ``text, font = token``, as well as
    rich metadata attributes: ``token.styles``, ``token.link_url``, ``token.width``.
    """
    span_list = _to_spans(spans)
    tokens: list[Token] = []

    for span in span_list:
        font = _font_for(span.styles, fonts)
        words = span.text.split(" ")
        for idx, w in enumerate(words):
            # Break unbreakable overlong words that exceed max_w
            w_meas = _measure(font, w)
            if w_meas > max_w and len(w) > 1 and max_w > 20:
                chunk = ""
                for ch in w:
                    if _measure(font, chunk + ch) > max_w and chunk:
                        tokens.append(Token(chunk, font, span.styles, span.link_url))
                        chunk = ch
                    else:
                        chunk += ch
                if chunk:
                    tokens.append(Token(chunk, font, span.styles, span.link_url))
            else:
                tokens.append(Token(w, font, span.styles, span.link_url, width=w_meas))

            if idx < len(words) - 1:
                tokens.append(Token(" ", font, span.styles, span.link_url))

    lines: list[list[Token]] = []
    line_widths: list[float] = []
    cur: list[Token] = []
    cur_w = 0.0

    def flush():
        nonlocal cur, cur_w
        if cur:
            w_line = sum(t.width for t in cur)
            lines.append(cur)
            line_widths.append(w_line)
        cur = []
        cur_w = 0.0

    for tok in tokens:
        w = tok.width
        if cur and (cur_w + w) > max_w:
            flush()
            if tok.text == " ":
                continue
        cur.append(tok)
        cur_w += w
    flush()

    if not lines:
        lines.append([])
        line_widths.append(0.0)
    max_line_w = max(line_widths) if line_widths else 0.0
    return lines, max_line_w


# --------------------------------------------------------------------------
# Syntax Highlighting for Code Blocks
# --------------------------------------------------------------------------

_PY_KEYWORDS = {
    "def", "class", "return", "if", "elif", "else", "while", "for", "in", "try",
    "except", "finally", "with", "as", "lambda", "yield", "raise", "pass", "break",
    "continue", "async", "await", "assert", "import", "from", "is", "not", "and",
    "or", "global", "nonlocal"
}
_PY_BUILTINS = {
    "True", "False", "None", "self", "cls", "print", "len", "range", "enumerate",
    "zip", "map", "filter", "int", "str", "float", "bool", "list", "dict", "set",
    "tuple", "type", "isinstance", "issubclass", "open", "super", "sum", "min", "max", "abs"
}

_JS_KEYWORDS = {
    "function", "const", "let", "var", "return", "if", "else", "for", "while", "do",
    "switch", "case", "break", "continue", "class", "extends", "import", "export",
    "default", "from", "new", "this", "super", "async", "await", "try", "catch",
    "finally", "throw", "typeof", "instanceof", "in", "of", "yield"
}
_JS_BUILTINS = {
    "true", "false", "null", "undefined", "NaN", "Infinity", "console", "window",
    "document", "Promise", "Array", "Object", "String", "Number", "Boolean", "Map", "Set", "JSON"
}

_SQL_KEYWORDS = {
    "select", "from", "where", "insert", "into", "update", "set", "delete", "create",
    "table", "drop", "alter", "add", "join", "inner", "left", "right", "full", "on",
    "group", "by", "order", "asc", "desc", "having", "limit", "offset", "union",
    "all", "and", "or", "not", "in", "exists", "between", "like", "is", "null", "as",
    "primary", "key", "foreign", "references", "default", "count", "sum", "avg", "min", "max"
}


def _tokenize_code_line(line: str, lang: str, palette: Palette) -> list[tuple[str, tuple]]:
    """Tokenize a single code line into (text, color) tuples."""
    if not line:
        return [("", palette.code_text)]
    l = lang.lower()

    if l in ("python", "py"):
        # Match comments, strings, identifiers, numbers, operators
        pattern = re.compile(
            r"(?P<comment>#.*$)|"
            r"(?P<string>f?\"\"\"[\s\S]*?\"\"\"|f?'''[\s\S]*?'''|f?\"[^\"]*\"|f?'[^']*')|"
            r"(?P<number>\b\d+\.?\d*\b)|"
            r"(?P<word>\b[a-zA-Z_]\w*\b)|"
            r"(?P<other>[^\s\w]+|\s+)"
        )
        tokens = []
        for m in pattern.finditer(line):
            kind = m.lastgroup
            val = m.group(0)
            if kind == "comment":
                tokens.append((val, palette.code_comment))
            elif kind == "string":
                tokens.append((val, palette.code_str))
            elif kind == "number":
                tokens.append((val, palette.code_num))
            elif kind == "word":
                if val in _PY_KEYWORDS:
                    tokens.append((val, palette.code_kw))
                elif val in _PY_BUILTINS:
                    tokens.append((val, palette.code_type))
                else:
                    tokens.append((val, palette.code_text))
            else:
                tokens.append((val, palette.code_text))
        return tokens or [(line, palette.code_text)]

    if l in ("javascript", "js", "typescript", "ts", "jsx", "tsx"):
        pattern = re.compile(
            r"(?P<comment>//.*$)|"
            r"(?P<string>\"[^\"]*\"|'[^']*'|`[^`]*`)|"
            r"(?P<number>\b\d+\.?\d*\b)|"
            r"(?P<word>\b[a-zA-Z_]\w*\b)|"
            r"(?P<other>[^\s\w]+|\s+)"
        )
        tokens = []
        for m in pattern.finditer(line):
            kind = m.lastgroup
            val = m.group(0)
            if kind == "comment":
                tokens.append((val, palette.code_comment))
            elif kind == "string":
                tokens.append((val, palette.code_str))
            elif kind == "number":
                tokens.append((val, palette.code_num))
            elif kind == "word":
                if val in _JS_KEYWORDS:
                    tokens.append((val, palette.code_kw))
                elif val in _JS_BUILTINS:
                    tokens.append((val, palette.code_type))
                else:
                    tokens.append((val, palette.code_text))
            else:
                tokens.append((val, palette.code_text))
        return tokens or [(line, palette.code_text)]

    if l in ("json",):
        pattern = re.compile(
            r"(?P<key>\"[^\"]*\"\s*(?=:))|"
            r"(?P<string>\"[^\"]*\")|"
            r"(?P<number>-?\b\d+\.?\d*\b)|"
            r"(?P<const>\b(?:true|false|null)\b)|"
            r"(?P<other>[^\s\w]+|\s+)"
        )
        tokens = []
        for m in pattern.finditer(line):
            kind = m.lastgroup
            val = m.group(0)
            if kind == "key":
                tokens.append((val, palette.code_fn))
            elif kind == "string":
                tokens.append((val, palette.code_str))
            elif kind == "number":
                tokens.append((val, palette.code_num))
            elif kind == "const":
                tokens.append((val, palette.code_type))
            else:
                tokens.append((val, palette.code_text))
        return tokens or [(line, palette.code_text)]

    if l in ("sql",):
        pattern = re.compile(
            r"(?P<comment>--.*$)|"
            r"(?P<string>'[^']*')|"
            r"(?P<number>\b\d+\.?\d*\b)|"
            r"(?P<word>\b[a-zA-Z_]\w*\b)|"
            r"(?P<other>[^\s\w]+|\s+)"
        )
        tokens = []
        for m in pattern.finditer(line):
            kind = m.lastgroup
            val = m.group(0)
            if kind == "comment":
                tokens.append((val, palette.code_comment))
            elif kind == "string":
                tokens.append((val, palette.code_str))
            elif kind == "number":
                tokens.append((val, palette.code_num))
            elif kind == "word":
                if val.lower() in _SQL_KEYWORDS:
                    tokens.append((val, palette.code_kw))
                else:
                    tokens.append((val, palette.code_text))
            else:
                tokens.append((val, palette.code_text))
        return tokens or [(line, palette.code_text)]

    if l in ("bash", "sh", "zsh", "shell"):
        pattern = re.compile(
            r"(?P<comment>#.*$)|"
            r"(?P<string>\"[^\"]*\"|'[^']*')|"
            r"(?P<var>\$[a-zA-Z_]\w*|\$\{[^}]+\})|"
            r"(?P<word>\b[a-zA-Z_]\w*\b)|"
            r"(?P<other>[^\s\w]+|\s+)"
        )
        tokens = []
        for m in pattern.finditer(line):
            kind = m.lastgroup
            val = m.group(0)
            if kind == "comment":
                tokens.append((val, palette.code_comment))
            elif kind == "string":
                tokens.append((val, palette.code_str))
            elif kind == "var":
                tokens.append((val, palette.code_type))
            elif kind == "word" and val in ("echo", "cd", "ls", "mkdir", "rm", "cp", "mv", "cat", "grep", "git", "chmod", "sudo", "export"):
                tokens.append((val, palette.code_fn))
            else:
                tokens.append((val, palette.code_text))
        return tokens or [(line, palette.code_text)]

    # Fallback plain code
    return [(line, palette.code_text)]


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
_SETEXT_H1_RE = re.compile(r"^={3,}\s*$")
_SETEXT_H2_RE = re.compile(r"^-{3,}\s*$")
_FENCE_RE = re.compile(r"^\s*```(.*)$")
_MATH_BLOCK_RE = re.compile(r"^\s*\$\$\s*$")
_HR_RE = re.compile(r"^\s*([-*_])( *\1){2,} *$")
_UL_RE = re.compile(r"^\s*([-*+])\s+(.*)$")
_OL_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_QUOTE_RE = re.compile(r"^\s*(>)\s*(.*)$")
_ALERT_RE = re.compile(r"^\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(.*)$", re.IGNORECASE)
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_TASK_RE = re.compile(r"^\s*\[([ xX])\]\s+(.*)$")
_DEF_RE = re.compile(r"^\s*:\s+(.*)$")
_PAGEBREAK_RE = re.compile(
    r"^\s*(?:<!--\s*page-?break\s*-->|\\pagebreak\b|---pagebreak---|<!--\s*break\s*-->|<\s*div\s+[^>]*class=[\"']page-?break[\"'][^>]*>\s*</\s*div\s*>)\s*$",
    re.IGNORECASE,
)


def _is_blank(line: str) -> bool:
    return line.strip() == ""


def _is_block_start(line: str) -> bool:
    if _is_blank(line):
        return True
    if _PAGEBREAK_RE.match(line):
        return True
    if _HEADING_RE.match(line) or _FENCE_RE.match(line) or _HR_RE.match(line) or _MATH_BLOCK_RE.match(line):
        return True
    if _QUOTE_RE.match(line) or _UL_RE.match(line) or _OL_RE.match(line) or _DEF_RE.match(line):
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


def _parse_list(lines: list[str], i: int, ordered: bool, start: int = 1):
    """Parse a (possibly nested) list starting at index ``i``. Returns (items, new_i)."""
    items: list[dict] = []
    n = len(lines)
    base_indent = None

    while i < n:
        line = lines[i]
        if _is_blank(line):
            # Check if the list continues after a single blank line
            if i + 1 < n and not _is_blank(lines[i + 1]):
                next_l = lines[i + 1]
                next_m = _UL_RE.match(next_l) or _OL_RE.match(next_l)
                next_ind = len(next_l) - len(next_l.lstrip())
                if next_m and base_indent is not None and next_ind == base_indent:
                    i += 1
                    line = lines[i]
                else:
                    break
            else:
                break

        m = _UL_RE.match(line) or _OL_RE.match(line)
        if not m:
            break
        indent = len(line) - len(line.lstrip())
        if base_indent is None:
            base_indent = indent
        elif indent < base_indent:
            break
        elif indent > base_indent:
            break

        content = m.group(2)
        number = int(_OL_RE.match(line).group(1)) if _OL_RE.match(line) else None

        # Check for task checkbox: - [ ] or - [x]
        task_match = _TASK_RE.match(content)
        if task_match:
            is_task = True
            checked = task_match.group(1).lower() == "x"
            content = task_match.group(2)
        else:
            is_task = False
            checked = False

        item = {
            "text": content,
            "number": number,
            "is_task": is_task,
            "checked": checked,
            "children": [],
        }
        i += 1

        # Check for nested sub-list or continuation lines
        while i < n and not _is_blank(lines[i]):
            sub_line = lines[i]
            sub_indent = len(sub_line) - len(sub_line.lstrip())
            if sub_indent > base_indent:
                if _UL_RE.match(sub_line) or _OL_RE.match(sub_line):
                    child_ordered = bool(_OL_RE.match(sub_line))
                    child_items, i = _parse_list(lines, i, child_ordered)
                    item["children"].extend(child_items)
                else:
                    item["text"] += " " + sub_line.strip()
                    i += 1
            else:
                break

        items.append(item)

    return items, i


def parse_blocks(raw: str) -> list[Block]:
    """Parse Markdown text into structured blocks."""
    lines = _split_lines(raw)
    blocks: list[Block] = []
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]
        if _is_blank(line):
            i += 1
            continue

        # Page break instruction
        if _PAGEBREAK_RE.match(line):
            blocks.append(Block("pagebreak", {}))
            i += 1
            continue

        # ATX Heading (# Title)
        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            blocks.append(Block("heading", {"level": level, "spans": parse_inline(m.group(2))}))
            i += 1
            continue

        # Setext Headings (Title \n === or Title \n ---)
        if i + 1 < n and not _is_block_start(line) and not _is_blank(line):
            if _SETEXT_H1_RE.match(lines[i + 1]):
                blocks.append(Block("heading", {"level": 1, "spans": parse_inline(line.strip())}))
                i += 2
                continue
            if _SETEXT_H2_RE.match(lines[i + 1]):
                blocks.append(Block("heading", {"level": 2, "spans": parse_inline(line.strip())}))
                i += 2
                continue

        # Math Block ($$...$$)
        if _MATH_BLOCK_RE.match(line):
            math_lines: list[str] = []
            i += 1
            while i < n and not _MATH_BLOCK_RE.match(lines[i]):
                math_lines.append(lines[i])
                i += 1
            if i < n:
                i += 1
            blocks.append(Block("math", {"text": "\n".join(math_lines)}))
            continue

        # Fenced code block (```lang ... ```)
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

        # Horizontal rule (---, ***, ___)
        if _HR_RE.match(line):
            blocks.append(Block("hr", {}))
            i += 1
            continue

        # Blockquote / Alert callout (> [!NOTE] ...)
        if _QUOTE_RE.match(line):
            q_lines: list[str] = []
            while i < n and (_QUOTE_RE.match(lines[i]) or (not _is_blank(lines[i]) and not _is_block_start(lines[i]))):
                qm = _QUOTE_RE.match(lines[i])
                q_lines.append(qm.group(2) if qm else lines[i])
                i += 1

            first_line = q_lines[0] if q_lines else ""
            alert_m = _ALERT_RE.match(first_line)
            if alert_m:
                alert_type = alert_m.group(1).lower()
                extra_title = alert_m.group(2).strip()
                inner_raw = "\n".join(q_lines[1:])
                blocks.append(Block("alert", {
                    "alert_type": alert_type,
                    "title": f"{alert_type.upper()}" + (f": {extra_title}" if extra_title else ""),
                    "blocks": parse_blocks(inner_raw),
                }))
            else:
                blocks.append(Block("blockquote", {"blocks": parse_blocks("\n".join(q_lines))}))
            continue

        # GFM Table
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

        # Unordered list
        if _UL_RE.match(line):
            items, i = _parse_list(lines, i, ordered=False)
            blocks.append(Block("ul", {"items": items}))
            continue

        # Ordered list
        if _OL_RE.match(line):
            start = int(_OL_RE.match(line).group(1))
            items, i = _parse_list(lines, i, ordered=True, start=start)
            blocks.append(Block("ol", {"items": items, "start": start}))
            continue

        # Definition list (Term \n : Definition)
        if i + 1 < n and _DEF_RE.match(lines[i + 1]) and not _is_block_start(line):
            term = line.strip()
            defs = []
            j = i + 1
            while j < n and _DEF_RE.match(lines[j]):
                defs.append(_DEF_RE.match(lines[j]).group(1).strip())
                j += 1
            blocks.append(Block("deflist", {"term": term, "definitions": defs}))
            i = j
            continue

        # Regular paragraph
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

Item = tuple


def _shift_prim(prim: Item, dx: int, dy: int) -> Item:
    """Offset coordinates inside an emitted primitive."""
    kind = prim[0]
    if kind in ("text_block", "list_marker", "hr", "quote_bar", "table", "image", "checkbox", "math_block"):
        return (kind, prim[1] + dx, prim[2] + dy, *prim[3:])
    if kind == "code_block":
        return (kind, prim[1] + dx, prim[2] + dy, *prim[3:])
    if kind == "alert_box":
        return (kind, prim[1] + dx, prim[2] + dy, *prim[3:])
    return prim


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

    def render(self, blocks: list[Block]) -> tuple[list[Item], int, int]:
        for block in blocks:
            self._render_block(block)
        page_width = self._measured_width
        page_height = self.y + self.margin
        return self.items, page_width, page_height

    def _emit(self, y: int, prim: Item) -> None:
        self.items.append((y, prim))

    def _render_block(self, block: Block) -> None:
        t = block.type
        if t == "pagebreak":
            return
        if t == "heading":
            self._heading(block)
        elif t == "paragraph":
            self._paragraph(block)
        elif t == "code":
            self._code(block)
        elif t == "math":
            self._math(block)
        elif t == "hr":
            self._hr(block)
        elif t == "blockquote":
            self._blockquote(block)
        elif t == "alert":
            self._alert(block)
        elif t == "table":
            self._table(block)
        elif t == "deflist":
            self._deflist(block)
        elif t in ("ul", "ol"):
            ordered = t == "ol"
            self._render_items(block.data["items"], ordered, block.data.get("start", 1), 0)

    def _heading(self, block: Block) -> None:
        level = max(1, min(6, block.data["level"]))
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
        src = block.data.get("text", "")
        lang = block.data.get("lang", "").strip()
        code_lines = src.split("\n") if src else [""]
        line_h = int(_leading(13, True))
        pad = 12
        code_w = max(_measure(fset.mono, ln) for ln in code_lines) if code_lines else 0
        total_w = max(int(code_w) + 2 * pad, 220)
        total_w = min(total_w, int(self.max_text_width))
        self._measured_width = max(self._measured_width, total_w + 2 * self.margin)
        img_h = line_h * len(code_lines) + 2 * pad + (18 if lang else 0)
        self._emit(self.y, ("code_block", self.margin, self.y, src, lang, fset.mono, line_h,
                             len(code_lines), pad, total_w))
        self.y += img_h + 14

    def _math(self, block: Block) -> None:
        fset = self.fonts.scaled(16)
        src = block.data.get("text", "").strip()
        lines = src.split("\n")
        line_h = 24
        pad = 14
        math_w = max(_measure(fset.italic, ln) for ln in lines) if lines else 0
        box_w = min(int(self.max_text_width), max(int(math_w) + 2 * pad + 40, 200))
        box_h = len(lines) * line_h + 2 * pad
        self._emit(self.y, ("math_block", self.margin, self.y, lines, fset.italic, box_w, box_h, line_h, pad))
        self.y += box_h + 14

    def _hr(self, block: Block) -> None:
        self._emit(self.y, ("hr", self.margin, self.y, self.max_text_width, self.palette))
        self.y += 18

    def _blockquote(self, block: Block) -> None:
        inner_w = self.max_text_width - 24
        sub = Renderer(self.fonts, self.palette, inner_w, 0)
        sub._image_dir = getattr(self, "_image_dir", None)
        sub.render(block.data["blocks"])
        bar_h = sub.y + 8
        self._emit(self.y, ("quote_bar", self.margin, self.y, bar_h, self.palette.quote_bar))
        for iy, prim in sub.items:
            shifted = _shift_prim(prim, self.margin + 16, self.y)
            self._emit(self.y + iy, shifted)
        self.y += bar_h + 8

    def _alert(self, block: Block) -> None:
        alert_type = block.data.get("alert_type", "note")
        title = block.data.get("title", alert_type.upper())

        styles = {
            "note": {"bar": (37, 99, 235), "bg": (239, 246, 255), "title": (29, 78, 216)},
            "tip": {"bar": (16, 185, 129), "bg": (236, 253, 245), "title": (4, 120, 87)},
            "important": {"bar": (139, 92, 246), "bg": (245, 243, 255), "title": (109, 40, 217)},
            "warning": {"bar": (245, 158, 11), "bg": (255, 251, 235), "title": (180, 83, 9)},
            "caution": {"bar": (239, 68, 68), "bg": (254, 242, 242), "title": (185, 28, 28)},
        }
        st = styles.get(alert_type, styles["note"])

        pad = 12
        box_w = int(self.max_text_width)
        inner_w = box_w - 2 * pad - 10

        sub = Renderer(self.fonts, self.palette, inner_w, 0)
        sub._image_dir = getattr(self, "_image_dir", None)
        sub.render(block.data["blocks"])

        title_font = self.fonts.scaled(14).bold
        title_text = f"•  {title}"
        title_h = 22

        inner_content_h = sub.y if sub.y > 0 else 16
        total_h = title_h + inner_content_h + 2 * pad

        self._emit(self.y, ("alert_box", self.margin, self.y, box_w, total_h, st["bar"], st["bg"], st["title"], title_text, title_font))
        for iy, prim in sub.items:
            shifted = _shift_prim(prim, self.margin + pad + 8, self.y + pad + title_h)
            self._emit(self.y + pad + title_h + iy, shifted)

        self.y += total_h + 14

    def _table(self, block: Block) -> None:
        header = block.data.get("header", [])
        rows = block.data.get("rows", [])
        ncols = max(len(header), *[len(r) for r in rows], 1)
        aligns = block.data.get("aligns", ["left"] * ncols)
        aligns = (aligns + ["left"] * ncols)[:ncols]
        fset = self.fonts.scaled(14)

        all_rows = [header] + rows
        col_w = [0.0] * ncols

        # First pass: measure unwrapped natural width of each column
        for rowdata in all_rows:
            for c in range(ncols):
                cell_text = rowdata[c] if c < len(rowdata) else ""
                _, w = wrap_inline(cell_text, fset, 10000.0)
                col_w[c] = max(col_w[c], w + 24)

        total_w = sum(col_w)
        budget = self.max_text_width
        if total_w > budget:
            scale = budget / max(total_w, 1.0)
            col_w = [max(40.0, c * scale) for c in col_w]
            total_w = sum(col_w)

        # Second pass: wrap each cell within its allocated column width
        table_lines: list[list] = []
        row_heights: list[int] = []
        for rowdata in all_rows:
            row_lines = []
            max_lines_in_row = 1
            for c in range(ncols):
                cell_text = rowdata[c] if c < len(rowdata) else ""
                cell_budget = max(20.0, col_w[c] - 16)
                lines, _ = wrap_inline(cell_text, fset, cell_budget)
                max_lines_in_row = max(max_lines_in_row, len(lines))
                row_lines.append(lines)
            table_lines.append(row_lines)
            row_h = max(30, max_lines_in_row * 19 + 10)
            row_heights.append(row_h)

        self._measured_width = max(self._measured_width, total_w + 2 * self.margin)
        total_h = sum(row_heights)
        self._emit(self.y, ("table", self.margin, self.y, table_lines, col_w, aligns, row_heights))
        self.y += total_h + 14

    def _deflist(self, block: Block) -> None:
        fset = self.fonts.scaled(15)
        term = block.data.get("term", "")
        defs = block.data.get("definitions", [])
        term_spans = parse_inline(f"**{term}**")
        lines, w = wrap_inline(term_spans, fset, self.max_text_width)
        h = int(_leading(15, len(lines) > 1) * len(lines))
        self._emit(self.y, ("text_block", self.margin, self.y, lines, 15, self.palette.heading))
        self.y += h + 6

        for d in defs:
            d_spans = parse_inline(d)
            d_lines, dw = wrap_inline(d_spans, fset, self.max_text_width - 24)
            dh = int(_leading(15, len(d_lines) > 1) * len(d_lines))
            self._emit(self.y, ("text_block", self.margin + 20, self.y, d_lines, 15, self.palette.text))
            self.y += dh + 6
        self.y += 8

    def _render_items(self, items: list[dict], ordered: bool, start: int, level: int) -> None:
        fset = self.fonts.scaled(15)
        pad = level * 22
        indent = self.margin + pad
        self.indent = indent

        for idx, item in enumerate(items):
            is_task = item.get("is_task", False)
            checked = item.get("checked", False)
            lines, w = wrap_inline(item["text"], fset, self.max_text_width - pad - 36)
            lh = _leading(15, len(lines) > 1)
            item_h = int(lh * max(1, len(lines)))

            if is_task:
                self._emit(self.y, ("checkbox", indent, self.y + 3, checked, 14, self.palette))
                self._emit(self.y, ("text_block", indent + 24, self.y, lines, 15, self.palette.text))
            else:
                if ordered:
                    num = item.get("number") if item.get("number") is not None else (start + idx)
                    prefix = f"{num}. "
                else:
                    prefix = "• "
                marker_w = _measure(fset.body, prefix) + 6
                self._emit(self.y, ("list_marker", indent, self.y, prefix, fset.body, self.palette.text))
                self._emit(self.y, ("text_block", indent + marker_w, self.y, lines, 15, self.palette.text))

            self.y += item_h + 8
            if item.get("children"):
                self._render_items(item["children"], ordered, 1, level + 1)

        self._measured_width = max(self._measured_width, indent + w)


class PagedRenderer:
    """Lays out parsed blocks across multiple A4 pages.

    Pages break on:
    1. Explicit pagebreak markers (e.g. <!-- pagebreak -->, \\pagebreak, ---pagebreak---)
    2. A4 page height overflow (when content exceeds page height)
    """

    def __init__(
        self,
        fonts: FontSet,
        palette: Palette,
        max_text_width: float,
        margin: float,
        a4_ratio: bool = True,
        image_dir: Optional[Path] = None,
    ):
        self.fonts = fonts
        self.palette = palette
        self.max_text_width = max(260.0, max_text_width)
        self.margin = margin
        self.image_dir = image_dir
        self.a4_ratio = a4_ratio

        self.page_width = int(self.max_text_width + 2 * self.margin)
        if self.a4_ratio:
            self.page_height = int(round(self.page_width * (297.0 / 210.0)))
        else:
            self.page_height = 1100

        self.top_margin = int(self.margin)
        self.bottom_limit = int(self.page_height - self.margin)

        self.pages: list[list[Item]] = []
        self.current_page_items: list[Item] = []
        self.y = self.top_margin

    def _new_page(self) -> None:
        """Finish the current page and advance to the next page."""
        self.pages.append(self.current_page_items)
        self.current_page_items = []
        self.y = self.top_margin

    def _emit(self, y: int, prim: Item) -> None:
        self.current_page_items.append((y, prim))

    def render(self, blocks: list[Block]) -> list[list[Item]]:
        for block in blocks:
            self._render_block(block)
        if self.current_page_items or not self.pages:
            self.pages.append(self.current_page_items)
        return self.pages

    def _render_block(self, block: Block) -> None:
        t = block.type
        if t == "pagebreak":
            if self.current_page_items:
                self._new_page()
            return
        if t == "heading":
            self._heading(block)
        elif t == "paragraph":
            self._paragraph(block)
        elif t == "code":
            self._code(block)
        elif t == "math":
            self._math(block)
        elif t == "hr":
            self._hr(block)
        elif t == "blockquote":
            self._blockquote(block)
        elif t == "alert":
            self._alert(block)
        elif t == "table":
            self._table(block)
        elif t == "deflist":
            self._deflist(block)
        elif t in ("ul", "ol"):
            ordered = t == "ol"
            self._render_items(block.data["items"], ordered, block.data.get("start", 1), 0)

    def _heading(self, block: Block) -> None:
        level = max(1, min(6, block.data["level"]))
        size = [32, 25, 20, 17, 15, 14][level - 1]
        fset = self.fonts.scaled(size)
        lines, w = wrap_inline(block.data["spans"], fset, self.max_text_width)
        lh = _leading(size, len(lines) > 1)
        h = int(lh * len(lines))
        total_h = h + 14
        if self.current_page_items and (self.y + total_h > self.bottom_limit - 35):
            self._new_page()
        self._emit(self.y, ("text_block", self.margin, self.y, lines, size, self.palette.heading))
        self.y += total_h

    def _paragraph(self, block: Block) -> None:
        spans = block.data["spans"]
        if len(spans) == 2 and spans[0].styles == ("image",) and spans[1].styles == ("image_path",):
            img = _resolve_image(spans[1].text, self.image_dir)
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
            total_h = new_size[1] + 12
            if self.current_page_items and (self.y + total_h > self.bottom_limit):
                self._new_page()
            self._emit(self.y, ("image", self.margin, self.y, img))
            self.y += total_h
            return

        fset = self.fonts.scaled(16)
        lines, w = wrap_inline(spans, fset, self.max_text_width)
        lh = _leading(16, len(lines) > 1)

        idx = 0
        while idx < len(lines):
            avail = self.bottom_limit - self.y
            lines_fit = int(avail // lh)
            if lines_fit < 1:
                if self.current_page_items:
                    self._new_page()
                    continue
                lines_fit = 1
            chunk = lines[idx:idx + lines_fit]
            chunk_h = int(lh * len(chunk))
            self._emit(self.y, ("text_block", self.margin, self.y, chunk, 16, self.palette.text))
            self.y += chunk_h
            idx += len(chunk)
            if idx < len(lines):
                self._new_page()
            else:
                self.y += 12

    def _code(self, block: Block) -> None:
        fset = self.fonts.scaled(13)
        src = block.data.get("text", "")
        lang = block.data.get("lang", "").strip()
        code_lines = src.split("\n") if src else [""]
        line_h = int(_leading(13, True))
        pad = 12
        code_w = max(_measure(fset.mono, ln) for ln in code_lines) if code_lines else 0
        total_w = max(int(code_w) + 2 * pad, 220)
        total_w = min(total_w, int(self.max_text_width))
        total_h = line_h * len(code_lines) + 2 * pad + (18 if lang else 0)

        if self.y + total_h <= self.bottom_limit:
            self._emit(self.y, ("code_block", self.margin, self.y, src, lang, fset.mono, line_h,
                                len(code_lines), pad, total_w))
            self.y += total_h + 14
        else:
            if self.current_page_items and (self.bottom_limit - self.y < line_h * 2 + 2 * pad):
                self._new_page()
            cl_idx = 0
            while cl_idx < len(code_lines):
                badge_h = (18 if (cl_idx == 0 and lang) else 0)
                avail = self.bottom_limit - self.y - 2 * pad - badge_h
                lines_fit = int(avail // line_h)
                if lines_fit < 1:
                    if self.current_page_items:
                        self._new_page()
                        continue
                    lines_fit = 1
                chunk = code_lines[cl_idx:cl_idx + lines_fit]
                chunk_src = "\n".join(chunk)
                chunk_lang = lang if cl_idx == 0 else ""
                chunk_h = line_h * len(chunk) + 2 * pad + badge_h
                self._emit(self.y, ("code_block", self.margin, self.y, chunk_src, chunk_lang,
                                    fset.mono, line_h, len(chunk), pad, total_w))
                self.y += chunk_h + 14
                cl_idx += len(chunk)
                if cl_idx < len(code_lines):
                    self._new_page()

    def _math(self, block: Block) -> None:
        fset = self.fonts.scaled(16)
        src = block.data.get("text", "").strip()
        lines = src.split("\n")
        line_h = 24
        pad = 14
        math_w = max(_measure(fset.italic, ln) for ln in lines) if lines else 0
        box_w = min(int(self.max_text_width), max(int(math_w) + 2 * pad + 40, 200))
        box_h = len(lines) * line_h + 2 * pad
        total_h = box_h + 14
        if self.current_page_items and self.y + total_h > self.bottom_limit:
            self._new_page()
        self._emit(self.y, ("math_block", self.margin, self.y, lines, fset.italic, box_w, box_h, line_h, pad))
        self.y += total_h

    def _hr(self, block: Block) -> None:
        if self.current_page_items and self.y + 18 > self.bottom_limit:
            self._new_page()
        self._emit(self.y, ("hr", self.margin, self.y, self.max_text_width, self.palette))
        self.y += 18

    def _blockquote(self, block: Block) -> None:
        inner_w = self.max_text_width - 24
        sub = Renderer(self.fonts, self.palette, inner_w, 0)
        sub._image_dir = self.image_dir
        sub.render(block.data["blocks"])
        bar_h = sub.y + 8
        total_h = bar_h + 8
        if self.current_page_items and self.y + total_h > self.bottom_limit:
            self._new_page()
        self._emit(self.y, ("quote_bar", self.margin, self.y, bar_h, self.palette.quote_bar))
        for iy, prim in sub.items:
            shifted = _shift_prim(prim, self.margin + 16, self.y)
            self._emit(self.y + iy, shifted)
        self.y += total_h

    def _alert(self, block: Block) -> None:
        alert_type = block.data.get("alert_type", "note")
        title = block.data.get("title", alert_type.upper())

        styles = {
            "note": {"bar": (37, 99, 235), "bg": (239, 246, 255), "title": (29, 78, 216)},
            "tip": {"bar": (16, 185, 129), "bg": (236, 253, 245), "title": (4, 120, 87)},
            "important": {"bar": (139, 92, 246), "bg": (245, 243, 255), "title": (109, 40, 217)},
            "warning": {"bar": (245, 158, 11), "bg": (255, 251, 235), "title": (180, 83, 9)},
            "caution": {"bar": (239, 68, 68), "bg": (254, 242, 242), "title": (185, 28, 28)},
        }
        st = styles.get(alert_type, styles["note"])

        inner_w = self.max_text_width - 28
        sub = Renderer(self.fonts, self.palette, inner_w, 0)
        sub._image_dir = self.image_dir
        sub.render(block.data.get("blocks", []))

        total_h = sub.y + 46
        if self.current_page_items and self.y + total_h > self.bottom_limit:
            self._new_page()
        self._emit(self.y, ("alert_box", self.margin, self.y, self.max_text_width, total_h,
                            st["bar"], st["bg"], title, st["title"], self.fonts.scaled(13).bold,
                            sub.items, self.fonts))
        self.y += total_h + 12

    def _table(self, block: Block) -> None:
        headers = block.data.get("headers", [])
        rows = block.data.get("rows", [])
        aligns = block.data.get("aligns", [])
        fset = self.fonts.scaled(14)
        ncols = max(len(headers), max((len(r) for r in rows), default=0))
        if ncols == 0:
            return

        col_w = [0.0] * ncols
        all_rows = [headers] + rows
        for r in all_rows:
            for c_idx, cell in enumerate(r):
                if c_idx < ncols:
                    spans = parse_inline(cell)
                    lines, w = wrap_inline(spans, fset, self.max_text_width)
                    col_w[c_idx] = max(col_w[c_idx], w + 16)

        total_w = sum(col_w)
        if total_w > self.max_text_width and total_w > 0:
            scale = self.max_text_width / total_w
            col_w = [max(40.0, w * scale) for w in col_w]
            total_w = sum(col_w)

        table_lines = []
        row_heights = []
        for r in all_rows:
            row_lines = []
            max_lines_in_row = 1
            for c_idx in range(ncols):
                cell = r[c_idx] if c_idx < len(r) else ""
                spans = parse_inline(cell)
                cw = col_w[c_idx] - 16
                lines, w = wrap_inline(spans, fset, cw)
                row_lines.append(lines)
                max_lines_in_row = max(max_lines_in_row, len(lines))
            table_lines.append(row_lines)
            row_h = max(30, max_lines_in_row * 19 + 10)
            row_heights.append(row_h)

        total_h = sum(row_heights)
        if self.current_page_items and self.y + total_h + 14 > self.bottom_limit:
            self._new_page()
        self._emit(self.y, ("table", self.margin, self.y, table_lines, col_w, aligns, row_heights))
        self.y += total_h + 14

    def _deflist(self, block: Block) -> None:
        fset = self.fonts.scaled(15)
        term = block.data.get("term", "")
        defs = block.data.get("definitions", [])
        term_spans = parse_inline(f"**{term}**")
        lines, w = wrap_inline(term_spans, fset, self.max_text_width)
        h = int(_leading(15, len(lines) > 1) * len(lines))
        if self.current_page_items and self.y + h + 20 > self.bottom_limit:
            self._new_page()
        self._emit(self.y, ("text_block", self.margin, self.y, lines, 15, self.palette.heading))
        self.y += h + 6

        for d in defs:
            d_spans = parse_inline(d)
            d_lines, dw = wrap_inline(d_spans, fset, self.max_text_width - 24)
            dh = int(_leading(15, len(d_lines) > 1) * len(d_lines))
            if self.current_page_items and self.y + dh + 6 > self.bottom_limit:
                self._new_page()
            self._emit(self.y, ("text_block", self.margin + 20, self.y, d_lines, 15, self.palette.text))
            self.y += dh + 6
        self.y += 8

    def _render_items(self, items: list[dict], ordered: bool, start: int, level: int) -> None:
        fset = self.fonts.scaled(15)
        pad = level * 22
        indent = self.margin + pad

        for idx, item in enumerate(items):
            is_task = item.get("is_task", False)
            checked = item.get("checked", False)
            lines, w = wrap_inline(item["text"], fset, self.max_text_width - pad - 36)
            lh = _leading(15, len(lines) > 1)
            item_h = int(lh * max(1, len(lines)))

            if self.current_page_items and self.y + item_h + 8 > self.bottom_limit:
                self._new_page()

            if is_task:
                self._emit(self.y, ("checkbox", indent, self.y + 3, checked, 14, self.palette))
                self._emit(self.y, ("text_block", indent + 24, self.y, lines, 15, self.palette.text))
            else:
                if ordered:
                    num = item.get("number") if item.get("number") is not None else (start + idx)
                    prefix = f"{num}. "
                else:
                    prefix = "• "
                marker_w = _measure(fset.body, prefix) + 6
                self._emit(self.y, ("list_marker", indent, self.y, prefix, fset.body, self.palette.text))
                self._emit(self.y, ("text_block", indent + marker_w, self.y, lines, 15, self.palette.text))

            self.y += item_h + 8
            if item.get("children"):
                self._render_items(item["children"], ordered, 1, level + 1)


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------

def _draw_text_line(draw: ImageDraw.ImageDraw, x: float, y: float, line: list[Token], size: int, default_color: tuple, palette: Palette) -> None:
    """Draw a single line of tokens with rich inline styling."""
    # Pass 1: Backgrounds (Highlights and Code Pills)
    tx = x
    for tok in line:
        w = tok.width
        st = tok.styles
        if "mark" in st:
            m_top = y + 1
            m_bot = y + int(size * 1.35)
            draw.rectangle([tx, m_top, tx + w, m_bot], fill=palette.mark_bg)
        if "code" in st or "kbd" in st:
            if tok.text.strip():
                p_top = y + int(size * 0.1)
                p_bot = y + int(size * 1.3)
                bg = palette.code_pill_bg
                border = palette.code_pill_border if "code" in st else palette.muted
                draw.rounded_rectangle([tx - 2, p_top, tx + w + 2, p_bot], radius=3, fill=bg, outline=border, width=1)
        tx += w

    # Pass 2: Foreground text and decorations (Links, Underlines, Strikethrough)
    tx = x
    for tok in line:
        w = tok.width
        st = tok.styles

        draw_y = y
        if "sup" in st:
            draw_y = y - int(size * 0.3)
        elif "sub" in st:
            draw_y = y + int(size * 0.22)

        if "link" in st:
            tok_color = palette.accent
        elif "code" in st or "kbd" in st:
            tok_color = palette.code_text
        else:
            tok_color = default_color

        if tok.text != " ":
            draw.text((tx, draw_y), tok.text, font=tok.font, fill=tok_color)

        if "link" in st or "underline" in st:
            u_y = y + int(size * 1.25)
            u_col = palette.accent if "link" in st else tok_color
            draw.line([(tx, u_y), (tx + w, u_y)], fill=u_col, width=1)

        if "strikethrough" in st:
            s_y = y + int(size * 0.68)
            draw.line([(tx, s_y), (tx + w, s_y)], fill=palette.strikethrough, width=max(1, int(size * 0.08)))

        tx += w


def _draw_text_block(draw: ImageDraw.ImageDraw, x: float, y: float, lines: list[list[Token]], size: int, color: tuple, palette: Palette) -> None:
    line_h = int(_leading(size, len(lines) > 1))
    for line in lines:
        _draw_text_line(draw, x, y, line, size, color, palette)
        y += line_h


def _draw_code_block(draw: ImageDraw.ImageDraw, x: float, y: float, text: str, lang: str, mono_font: object, line_h: int, count: int, pad: int, total_w: int, palette: Palette) -> None:
    box_w = total_w
    badge_h = 20 if lang else 0
    box_h = line_h * count + 2 * pad + badge_h
    # Code card background and subtle border
    draw.rounded_rectangle([x, y, x + box_w, y + box_h], radius=6, fill=palette.code_bg, outline=palette.table_border, width=1)

    # Optional language badge
    if lang:
        badge_text = lang.upper()
        bw = float(len(badge_text) * 8 + 14)
        bx = x + box_w - bw - 10
        by = y + 8
        draw.rounded_rectangle([bx, by, bx + bw, by + 18], radius=3, fill=palette.code_badge_bg)
        draw.text((bx + 6, by + 2), badge_text, font=mono_font, fill=palette.code_badge_text)

    tx = x + pad
    ty = y + pad + badge_h
    for ln in text.split("\n"):
        toks = _tokenize_code_line(ln, lang, palette)
        cur_tx = tx
        for tok_text, tok_col in toks:
            draw.text((cur_tx, ty), tok_text, font=mono_font, fill=tok_col)
            cur_tx += _measure(mono_font, tok_text)
        ty += line_h


def _draw_math_block(draw: ImageDraw.ImageDraw, x: float, y: float, lines: list[str], italic_font: object, box_w: int, box_h: int, line_h: int, pad: int, palette: Palette) -> None:
    draw.rounded_rectangle([x, y, x + box_w, y + box_h], radius=6, fill=palette.code_bg, outline=palette.table_border, width=1)
    ty = y + pad
    for ln in lines:
        w = _measure(italic_font, ln)
        tx = x + (box_w - w) / 2
        draw.text((tx, ty), ln, font=italic_font, fill=palette.heading)
        ty += line_h


def _draw_alert_box(draw: ImageDraw.ImageDraw, x: float, y: float, box_w: int, total_h: int, bar_col: tuple, bg_col: tuple, title_col: tuple, title_text: str, title_font: object) -> None:
    # Tinted callout container
    draw.rounded_rectangle([x, y, x + box_w, y + total_h], radius=6, fill=bg_col, outline=bar_col, width=1)
    # Bold accent bar on the left
    draw.rectangle([x, y, x + 5, y + total_h], fill=bar_col)
    # Title badge / header
    draw.text((x + 14, y + 10), title_text, font=title_font, fill=title_col)


def _draw_checkbox(draw: ImageDraw.ImageDraw, x: float, y: float, checked: bool, box_size: int, palette: Palette) -> None:
    cx, cy = x, y
    if checked:
        draw.rounded_rectangle([cx, cy, cx + box_size, cy + box_size], radius=3, fill=palette.checkbox_checked_bg, outline=palette.checkbox_checked_bg)
        # Crisp checkmark
        p1 = (cx + 3, cy + box_size * 0.5)
        p2 = (cx + box_size * 0.42, cy + box_size * 0.78)
        p3 = (cx + box_size * 0.82, cy + box_size * 0.25)
        draw.line([p1, p2, p3], fill=palette.checkbox_check, width=2)
    else:
        draw.rounded_rectangle([cx, cy, cx + box_size, cy + box_size], radius=3, fill=palette.bg, outline=palette.checkbox_border, width=1)


def _draw_table(draw: ImageDraw.ImageDraw, x: float, y: float, table_lines: list[list], col_w: list[float], aligns: list[str], row_h_or_heights: Union[int, list[int]], palette: Palette) -> None:
    cx = x
    col_x = []
    for cw in col_w:
        col_x.append(cx)
        cx += cw
    total_w = sum(col_w)

    if isinstance(row_h_or_heights, (int, float)):
        row_heights = [int(row_h_or_heights)] * len(table_lines)
    else:
        row_heights = list(row_h_or_heights)

    current_y = y
    for row_idx, row_lines in enumerate(table_lines):
        is_header = (row_idx == 0)
        row_h = row_heights[row_idx] if row_idx < len(row_heights) else 30
        bg = palette.table_head_bg if is_header else (palette.table_zebra_bg if (row_idx % 2 == 1) else palette.bg)
        if bg:
            draw.rectangle([x, current_y, x + total_w, current_y + row_h], fill=bg)

        for ci, lines in enumerate(row_lines):
            cw = col_w[ci]
            align = aligns[ci] if ci < len(aligns) else "left"
            cell_x = col_x[ci]
            line_y = current_y + 6
            for line in lines:
                line_w = sum(t.width for t in line)
                if align == "right":
                    ltx = cell_x + cw - line_w - 8
                elif align == "center":
                    ltx = cell_x + (cw - line_w) / 2
                else:
                    ltx = cell_x + 8
                _draw_text_line(draw, ltx, line_y, line, 14, palette.heading if is_header else palette.text, palette)
                line_y += 19

        current_y += row_h

    # Table grid borders
    x0 = x
    x1 = x + total_w
    total_table_h = sum(row_heights)
    # Horizontal grid lines
    line_y = y
    draw.line([x0, line_y, x1, line_y], fill=palette.table_border, width=1)
    for rh in row_heights:
        line_y += rh
        draw.line([x0, line_y, x1, line_y], fill=palette.table_border, width=1)

    # Vertical grid lines
    for vx in col_x + [x1]:
        draw.line([vx, y, vx, y + total_table_h], fill=palette.table_border, width=1)


def draw(items: list[Item], page_width: float, page_height: float, palette: Palette = DEFAULT_PALETTE) -> Image.Image:
    """Draw pre-computed layout ``items`` onto a fresh RGBA image with A4 sheet definition."""
    w = max(1, int(page_width))
    h = max(1, int(page_height))
    img = Image.new("RGBA", (w, h), palette.bg)
    draw_ctx = ImageDraw.Draw(img)

    # Clean page boundary defining the A4 paper sheet
    draw_ctx.rectangle([0, 0, w - 1, h - 1], outline=palette.table_border, width=1)

    for y, prim in sorted(items, key=lambda it: it[0]):
        kind = prim[0]
        try:
            if kind == "text_block":
                _, x, ty, lines, size, color = prim
                _draw_text_block(draw_ctx, x, ty, lines, size, color, palette)
            elif kind == "list_marker":
                _, x, ty, prefix, font, color = prim
                draw_ctx.text((x, ty), prefix, font=font, fill=color)
            elif kind == "checkbox":
                _, cx, cy, checked, box_size, pal = prim
                _draw_checkbox(draw_ctx, cx, cy, checked, box_size, pal)
            elif kind == "code_block":
                _draw_code_block(draw_ctx, *prim[1:], palette)
            elif kind == "math_block":
                _draw_math_block(draw_ctx, *prim[1:], palette)
            elif kind == "alert_box":
                _draw_alert_box(draw_ctx, *prim[1:])
            elif kind == "hr":
                _, x, _, line_w, _ = prim
                draw_ctx.line([(x, y), (x + line_w, y)], fill=palette.hr, width=2)
            elif kind == "quote_bar":
                _, x, _, bar_h, color = prim
                draw_ctx.rectangle([x, y, x + 4, y + bar_h], fill=color)
            elif kind == "table":
                _draw_table(draw_ctx, *prim[1:], palette)
            elif kind == "image":
                _, x, ty, pil = prim
                img.paste(pil, (x, ty), pil)
        except Exception:
            continue
    return img


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------

def render_markdown_pages(
    markdown_text: str,
    *,
    text_width: float = 760,
    margin: float = 36,
    a4_ratio: bool = True,
    fonts: Optional[FontSet] = None,
    palette: Optional[Palette] = None,
    image_dir: Optional[Path] = None,
) -> list[Image.Image]:
    """Render a Markdown string into a list of A4-sized PNG-ready :class:`~PIL.Image.Image` pages.

    Pages break on:
    1. Explicit pagebreak markers (e.g. <!-- pagebreak -->, \\pagebreak, ---pagebreak---)
    2. A4 page height overflow (when content height exceeds standard A4 paper height)
    """
    fonts = fonts or FontSet.system()
    palette = palette or DEFAULT_PALETTE
    blocks = parse_blocks(markdown_text or "")
    paged = PagedRenderer(
        fonts, palette, text_width, margin, a4_ratio=a4_ratio, image_dir=image_dir
    )
    pages_items = paged.render(blocks)

    images: list[Image.Image] = []
    for items in pages_items:
        img = draw(items, paged.page_width, paged.page_height, palette)
        images.append(img)
    return images


def render_markdown(
    markdown_text: str,
    *,
    text_width: float = 760,
    margin: float = 36,
    a4_ratio: bool = True,
    fonts: Optional[FontSet] = None,
    palette: Optional[Palette] = None,
    image_dir: Optional[Path] = None,
) -> Image.Image:
    """Render a Markdown string to a PNG-ready RGBA :class:`~PIL.Image.Image`.

    Preserves the standard ISO 216 A4 aspect ratio (297mm / 210mm) and guarantees
    that text will never be clipped at boundaries.
    """
    fonts = fonts or FontSet.system()
    palette = palette or DEFAULT_PALETTE
    blocks = parse_blocks(markdown_text or "")
    renderer = Renderer(fonts, palette, text_width, margin)
    renderer._image_dir = image_dir
    items, page_width, page_height = renderer.render(blocks)

    target_width = text_width + 2 * margin
    page_width = max(target_width, page_width)

    if a4_ratio:
        # Standard ISO 216 A4 aspect ratio (297mm / 210mm ≈ 1.4142)
        a4_min_height = int(round(page_width * (297.0 / 210.0)))
        page_height = max(a4_min_height, page_height)
    elif page_height < 1:
        page_height = 60

    return draw(items, page_width, page_height, palette)
