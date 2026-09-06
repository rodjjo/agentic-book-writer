#!/usr/bin/env python3
"""Rasterise the SVG icons to PNGs (Pillow only — no external SVG engine).

This is a development/validate tool. It implements just enough of the SVG subset we
author (``svg``, ``g``, ``rect``, ``circle``, ``ellipse``, ``polygon``, ``polyline``,
``line`` and ``path``) to rasterise the icons deterministically into PNGs of several
sizes. It renders once at a high base resolution and downscales with Lanczos for smooth
edges, so the output is anti-aliased without any stroke/scanline rasteriser.

Usage::

    python tools/make_icons.py            # generate every icon
    python tools/make_icons.py book-icon  # generate a single icon
"""

from __future__ import annotations

import argparse
import math
import re
import shutil
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SVG_DIR = ROOT / "non_py" / "svg"
PNG_DIR = ROOT / "non_py" / "pngs"
ICONS_DIR = ROOT / "icons"
PACKAGE_ICONS_DIR = ROOT / "app" / "icons"

BASE = 256  # internal render resolution before downscaling

_SIZES = {
    "book-icon": [16, 24, 32, 48, 64, 128, 256],
    "send": [32, 48, 64],
    "upload": [32, 48, 64],
    "close": [32, 48, 64],
    "connect": [32, 48, 64],
}

_NAMED = {
    "white": (255, 255, 255), "black": (0, 0, 0), "red": (220, 38, 38),
    "blue": (37, 99, 235), "gray": (128, 128, 128), "grey": (128, 128, 128),
    "transparent": None, "none": None,
}


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _num(value: str) -> float:
    return float(value)


def parse_color(value: str | None):
    """Return an opaque RGB triple or ``None`` for no/transparent fill."""
    if not value:
        return None
    value = value.strip()
    if value.lower() in ("none", "transparent"):
        return None
    if value.startswith("#"):
        h = value[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) == 6:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        if len(h) == 8:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16))
    if value in _NAMED:
        return _NAMED[value]
    return None


def _with_alpha(rgb, opacity: float):
    if rgb is None or opacity <= 0:
        return None
    r, g, b = rgb[0], rgb[1], rgb[2]
    a = int(rgb[3] * opacity) if len(rgb) == 4 else 255
    return (r, g, b, max(0, min(255, int(a * opacity))))


# --------------------------------------------------------------------------
# Path parsing (M/L/C/H/V/Z, absolute + relative)
# --------------------------------------------------------------------------

def _bezier(p0, p1, p2, p3, steps: int = 16):
    pts = []
    for k in range(1, steps + 1):
        t = k / steps
        u = 1 - t
        x = u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0]
        y = u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]
        pts.append((x, y))
    return pts


def parse_path(data: str) -> list[list[tuple[float, float]]]:
    """Parse a path string into a list of closed/open subpaths (screen points)."""
    tokens = re.findall(r"[-+]?(?:\d*\.\d+|\d+(?:\.\d+)?)|[A-Za-z]", data)
    nums = [float(t) for t in tokens if not t.isalpha()]
    cmds = [t for t in tokens if t.isalpha()]
    subpaths: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    start = None
    pen = (0.0, 0.0)
    ci = 0  # command index

    def nargs(count: int) -> list[float]:
        nonlocal ci
        vals = nums[ci:ci + count]
        ci += count
        return vals

    def add(x: float, y: float):
        nonlocal cur, start, pen
        if cur is None:
            return
        if start is None:
            start = (x, y)
        cur.append((x, y))
        pen = (x, y)

    def close():
        if cur and start is not None:
            cur.append(start)

    for c in cmds:
        if c in ("M", "m"):
            close()
            x, y = nargs(2)
            pen_rel = (x, y) if c.islower() else pen
            cur = []
            add(x, y)
            cmd = "L" if c.islower() else "l"
        elif c in ("L", "l"):
            x, y = nargs(2)
            add(x, y)
        elif c in ("H", "h"):
            x = nargs(1)[0]
            add(x, pen[1])
        elif c in ("V", "v"):
            y = nargs(1)[0]
            add(pen[0], y)
        elif c in ("C", "c"):
            x1, y1, x2, y2, x, y = nargs(6)
            p0, p1, p2, p3 = pen, (x1, y1), (x2, y2), (x, y)
            cur.extend(_bezier(p0, p1, p2, p3))
            add(x, y)
        elif c == "Z":
            close()
            cur = None
    if cur:
        subpaths.append(cur)
    return subpaths


# --------------------------------------------------------------------------
# Rasteriser
# --------------------------------------------------------------------------

class _Ctx:
    def __init__(self, size: int):
        self.size = size
        self.buf = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.buf)


def _view_box_size(el) -> float:
    vb = el.get("viewBox") or el.get("viewbox")
    if vb:
        parts = [_num(v) for v in vb.split() + [0, 0, 1, 1]]
        return max(parts[2] or 1, parts[3] or 1)
    return 100.0


def _transform_point(tx, ty, s, pt):
    x, y = pt
    return (x * s + tx, y * s + ty)


def walk(el, ctx: _Ctx, style: dict, scale: float, tx: float, ty: float):
    tag = _localname(el.tag)
    child_style = _inherit(style, el)

    # Compose this element's transform on top of the inherited one.
    local_tx, local_ty, local_s = _transform_matrix(el)
    new_tx = tx + local_tx
    new_ty = ty + local_ty
    new_s = scale * local_s

    if tag in ("svg", "g", "defs", "symbol"):
        for child in el:
            walk(child, ctx, child_style, new_s, new_tx, new_ty)
        return

    opacity = child_style["opacity"]
    fill = _with_alpha(parse_color(child_style["fill"]), opacity * child_style["fill_opacity"])
    stroke = _with_alpha(parse_color(child_style["stroke"]), opacity * child_style["stroke_opacity"])
    sw = max(1, float(child_style["stroke_width"]))

    def draw_line(points):
        screen = [_transform_point(new_tx, new_ty, new_s, p) for p in points]
        ctx.draw.line(screen, fill=stroke, width=int(sw), joint="curve")

    if tag == "rect":
        ox, oy = _num(el.get("x") or 0), _num(el.get("y") or 0)
        w = _num(el.get("width") or 0)
        h = _num(el.get("height") or 0)
        x, y = _transform_point(new_tx, new_ty, new_s, (ox, oy))
        x2, y2 = _transform_point(new_tx, new_ty, new_s, (ox + w, oy + h))
        radius = _num(el.get("rx") or el.get("ry") or 0)
        if radius > 0 and fill is not None:
            ctx.draw.rounded_rectangle([x, y, x2, y2], radius=int(radius), fill=fill)
        elif fill is not None:
            ctx.draw.rectangle([x, y, x2, y2], fill=fill)
        if stroke is not None:
            ctx.draw.rectangle([x, y, x2, y2], outline=stroke, width=int(sw))

    elif tag in ("circle", "ellipse"):
        cx = _num(el.get("cx") or 0)
        cy = _num(el.get("cy") or 0)
        rx = _num(el.get("rx") or el.get("r") or 0)
        ry = _num(el.get("ry") or el.get("r") or 0)
        p1 = _transform_point(new_tx, new_ty, new_s, (cx - rx, cy - ry))
        p2 = _transform_point(new_tx, new_ty, new_s, (cx + rx, cy + ry))
        if fill is not None:
            ctx.draw.ellipse([p1[0], p1[1], p2[0], p2[1]], fill=fill)
        if stroke is not None:
            ctx.draw.ellipse([p1[0], p1[1], p2[0], p2[1]], outline=stroke, width=int(sw))

    elif tag in ("polygon", "polyline"):
        pts = _parse_points(el.get("points", ""))
        screen = [_transform_point(new_tx, new_ty, new_s, p) for p in pts]
        if tag == "polygon" and fill is not None and screen:
            ctx.draw.polygon(screen, fill=fill)
        if stroke is not None and len(screen) >= 2:
            ctx.draw.line(screen, fill=stroke, width=int(sw), joint="curve")

    elif tag == "line":
        p1 = _transform_point(new_tx, new_ty, new_s, (_num(el.get("x1") or 0), _num(el.get("y1") or 0)))
        p2 = _transform_point(new_tx, new_ty, new_s, (_num(el.get("x2") or 0), _num(el.get("y2") or 0)))
        if stroke is not None:
            ctx.draw.line([p1, p2], fill=stroke, width=int(sw), joint="curve")

    elif tag == "path":
        subpaths = parse_path(el.get("d", ""))
        for sub in subpaths:
            if not sub:
                continue
            screen = [_transform_point(new_tx, new_ty, new_s, p) for p in sub]
            if stroke is not None and len(screen) >= 2:
                ctx.draw.line(screen, fill=stroke, width=int(sw), joint="curve")
            if fill is not None:
                ctx.draw.polygon(screen, fill=fill)


def _parse_points(text: str) -> list[tuple[float, float]]:
    nums = [float(v) for v in re.split(r"[,\s]+", text.strip()) if v]
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]


def _transform_matrix(el):
    """Return the *local* (tx, ty, s) for an optional transform attribute.

    Defaults to the identity transform; only explicit ``translate``/``scale``
    components change it. Callers compose this with the already-accumulated scale.
    """
    tr = el.get("transform", "")
    tx, ty, s = 0.0, 0.0, 1.0
    for match in re.finditer(r"translate\(([^)]*)\)|scale\(([^)]*)\)", tr):
        if match.group(1) is not None:
            parts = [_num(v) for v in re.split(r"[,\s]+", match.group(1).strip()) if v]
            tx += parts[0] if len(parts) else 0.0
            ty += parts[1] if len(parts) > 1 else 0.0
        elif match.group(2) is not None:
            parts = [_num(v) for v in re.split(r"[,\s]+", match.group(2).strip()) if v]
            s *= parts[0] if len(parts) else 1.0
    return tx, ty, s


def _inherit(parent: dict, el) -> dict:
    style = dict(parent)
    for attr in ("fill", "stroke", "transform"):
        if el.get(attr) is not None:
            style[attr] = el.get(attr)
    if el.get("stroke-width") is not None:
        style["stroke_width"] = el.get("stroke-width")
    if el.get("opacity") is not None:
        style["opacity"] = float(el.get("opacity"))
    if el.get("fill-opacity") is not None:
        style["fill_opacity"] = float(el.get("fill-opacity"))
    if el.get("stroke-opacity") is not None:
        style["stroke_opacity"] = float(el.get("stroke-opacity"))
    return style


def rasterize_svg(svg_text: str, size: int) -> Image.Image:
    root = ET.fromstring(svg_text)
    ctx = _Ctx(BASE)
    style = {
        "fill": None, "stroke": None, "stroke_width": 1, "opacity": 1.0,
        "fill_opacity": 1.0, "stroke_opacity": 1.0,
    }
    vb = _view_box_size(root)
    walk(root, ctx, style, BASE / vb, 0.0, 0.0)
    if ctx.buf.width != size or ctx.buf.height != size:
        ctx.buf = ctx.buf.resize((size, size), Image.LANCZOS)
    return ctx.buf


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def generate(icon: str | None) -> list[Path]:
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    PACKAGE_ICONS_DIR.mkdir(parents=True, exist_ok=True)

    names = [icon] if icon else list(_SIZES)
    written: list[Path] = []
    for name in names:
        svg_path = SVG_DIR / f"{name}.svg"
        if not svg_path.exists():
            print(f"skip: {svg_path} not found", file=sys.stderr)
            continue
        svg_text = svg_path.read_text(encoding="utf-8")
        for size in _SIZES[name]:
            img = rasterize_svg(svg_text, size)
            out = PNG_DIR / f"{name}-{size}.png"
            img.save(out, format="PNG")
            written.append(out)
            print(f"wrote {out}")
        # Keep an up-to-date copy of the primary icon in the dedicated folders.
        if name == "book-icon":
            master = PNG_DIR / "book-icon.png"
            rasterize_svg(svg_text, 256).save(master, format="PNG")
            shutil.copyfile(master, ICONS_DIR / "book-writer.png")
            shutil.copyfile(master, PACKAGE_ICONS_DIR / "book-writer.png")
            written.append(master)
            print(f"wrote {master}")
            print(f"wrote {ICONS_DIR / 'book-writer.png'}")
            print(f"wrote {PACKAGE_ICONS_DIR / 'book-writer.png'}")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rasterise the Book Writer SVG icons to PNGs.")
    parser.add_argument("icon", nargs="?", help="Only generate this icon (e.g. 'book-icon').")
    args = parser.parse_args(argv)
    written = generate(args.icon)
    print(f"\nGenerated {len(written)} PNG file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
