"""Image loading, thumbnailing and (optional) Kivy texture helpers.

Everything here uses only Pillow. When the GUI runs under Kivy, the
:func:`image_to_texture` helper turns a PIL image into a Kivy :class:`Texture`.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Optional

from PIL import Image

# Supported raster extensions for files found inside books / documents.
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def is_base64_png(data: str) -> bool:
    """Return True if ``data`` looks like a ``data:...;base64,....`` PNG payload."""
    if not data.startswith("data:"):
        return False
    return "base64" in data.lower()


def base64_to_image(data: str) -> Image.Image:
    """Convert a ``data:...;base64,<blob>`` string into a PIL image."""
    if not is_base64_png(data):
        # Strip any leading metadata prefix defensively.
        if "," in data:
            data = data.split(",", 1)[1]
    raw = base64.b64decode(data)
    return Image.open(io.BytesIO(raw))


def encode_image_to_png(img: Image.Image) -> str:
    """Return a ``data:image/png;base64,...`` string for ``img``."""
    buffer = io.BytesIO()
    img.convert("RGBA").save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def encode_file_to_base64_png(path: str | Path) -> str:
    """Load an image file and return a base64 PNG data URI."""
    return encode_image_to_png(Image.open(path).convert("RGBA"))


def load_image(path: str | Path) -> Image.Image:
    """Load an image from a filesystem path (validated extension)."""
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"unsupported image extension: {path.suffix!r}")
    img = Image.open(path)
    img.load()
    return img


def make_thumbnail(img: Image.Image, size) -> Image.Image:
    """Return a scaled-down copy of ``img`` fitting inside ``size`` (square-ish)."""
    thumb = img.copy()
    thumb.thumbnail(size, Image.LANCZOS)
    return thumb


def fit_image_to_box(img: Image.Image, box) -> Image.Image:
    """Scale ``img`` to fit within ``box`` preserving aspect ratio."""
    ratio = min(box[0] / img.width, box[1] / img.height)
    if ratio >= 1.0:
        return img
    new_size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
    return img.resize(new_size, Image.LANCZOS)


def image_to_texture(img: Image.Image):
    """Wrap a PIL image in a Kivy :class:`~kivy.graphics.texture.Texture`.

    Imported lazily so this module can be used without a display (e.g. in tests) and
    without Kivy installed at all.

    ``blit_buffer`` stores the first buffer row at texture ``v=0`` (the bottom of a
    drawn rectangle), while PIL's ``tobytes()`` emits the *top* row first -- so without
    a vertical flip the image would be upside-down on screen.  ``flip_vertical()``
    corrects that so PIL's top row maps to the top of the drawn rectangle (which is
    also what the ``ScrollCanvas`` stripe layout math assumes).
    """
    from kivy.graphics.texture import Texture

    pil = img.convert("RGBA")
    pil.load()
    tex = Texture.create(size=(pil.width, pil.height), colorfmt="rgba")
    tex.blit_buffer(pil.tobytes(), colorfmt="rgba")
    tex.flip_vertical()
    tex.mag_filter = "linear"
    tex.min_filter = "nearest"
    return tex


def blank_image(size, color=(255, 255, 255, 0)) -> Image.Image:
    """Return a blank RGBA image of the given size."""
    return Image.new("RGBA", size, color)


def image_from_path_or_none(path: Optional[str | Path]) -> Optional[Image.Image]:
    try:
        return load_image(path) if path else None
    except (OSError, ValueError):
        return None
