"""Bridge the hex-string themes from :mod:`app.theme` to Kivy RGBA float colours."""

from __future__ import annotations

from app.theme import Theme, to_rgb


def rgba(hex_color: str, alpha: float = 1.0) -> tuple:
    """Turn a ``#rrggbb`` / ``#rgb`` hex string into a Kivy ``(r, g, b, a)`` tuple.

    Components are floats in ``[0, 1]``; ``alpha`` is a float in ``[0, 1]``.
    """
    r, g, b = to_rgb(hex_color)
    return (r / 255.0, g / 255.0, b / 255.0, max(0.0, min(1.0, alpha)))


def theme_colors(theme: Theme) -> dict:
    """All theme hex colours as a plain dict (used by the Pillow renderers)."""
    return theme.colors()


def lighten(color: tuple, amount: float = 0.12) -> tuple:
    """Blend an RGBA float colour towards white."""
    r, g, b, a = color
    return (r + (1.0 - r) * amount, g + (1.0 - g) * amount,
            b + (1.0 - b) * amount, a)


def darken(color: tuple, amount: float = 0.12) -> tuple:
    """Blend an RGBA float colour towards black."""
    r, g, b, a = color
    return (r * (1.0 - amount), g * (1.0 - amount), b * (1.0 - amount), a)
