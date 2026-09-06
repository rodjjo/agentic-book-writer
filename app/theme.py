"""Visual themes for the Book Writer GUI (light and dark).

Colours are stored in plain dictionaries so a theme can be swapped at runtime and the
widgets re-read them.  Both the Pillow renderers and the Kivy widgets consume these hex
values (the Kivy layer converts them to RGBA floats).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class Theme:
    name: str
    bg: str = "#f7f7f8"
    fg: str = "#1c1c22"
    muted: str = "#8a8a95"
    panel: str = "#ececf0"
    panel_2: str = "#e4e4ea"
    border: str = "#d5d5dd"
    accent: str = "#2563eb"
    accent_fg: str = "#ffffff"
    input_bg: str = "#ffffff"
    chat_bg: str = "#fafafe"
    user_bubble: str = "#dbeafe"
    user_fg: str = "#1e3a8a"
    assistant_bubble: str = "#ffffff"
    assistant_fg: str = "#1c1c22"
    tool_bubble: str = "#fef9c3"
    tool_fg: str = "#713f12"
    error_bubble: str = "#fee2e2"
    error_fg: str = "#991b1b"
    page_bg: str = "#ffffff"
    scrollbar: str = "#c9c9d2"
    heading: str = "#0f1428"

    def colors(self) -> Dict[str, str]:
        return {k: v for k, v in field_values(self).items() if isinstance(v, str)}

    def for_widget(self, **overrides) -> Dict[str, str]:
        c = self.colors()
        c.update(overrides)
        return c


def field_values(obj) -> Dict[str, object]:
    return {k: v for k, v in vars(obj).items()}


def to_rgb(hexstr: str) -> tuple[int, int, int]:
    """Convert a ``#rrggbb`` / ``#rgb`` hex string into an ``(r, g, b)`` tuple."""
    h = (hexstr or "#000000").lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def to_rgba(hexstr: str, alpha: int = 255) -> tuple[int, int, int, int]:
    """Convert a ``#rrggbb`` / ``#rgb`` hex string into an ``(r, g, b, a)`` tuple."""
    r, g, b = to_rgb(hexstr)
    return (r, g, b, alpha)


LIGHT = Theme("light")
DARK = Theme(
    "dark",
    bg="#14141a",
    fg="#e8e8ee",
    muted="#8b8b98",
    panel="#1e1e26",
    panel_2="#26262f",
    border="#33333e",
    accent="#3b82f6",
    accent_fg="#ffffff",
    input_bg="#1a1a22",
    chat_bg="#101015",
    user_bubble="#1e3a5f",
    user_fg="#dbeafe",
    assistant_bubble="#202029",
    assistant_fg="#e8e8ee",
    tool_bubble="#3b3318",
    tool_fg="#fde68a",
    error_bubble="#3a1616",
    error_fg="#fecaca",
    page_bg="#ffffff",
    scrollbar="#3a3a46",
    heading="#e8e8ee",
)

THEMES = {"light": LIGHT, "dark": DARK}
