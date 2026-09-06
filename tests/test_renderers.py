"""Headless Pillow renderer tests (markdown pages, chat log, app log)."""

from __future__ import annotations

from app import chat_render, log_render, logs, markdown_render
from app.theme import THEMES


def _distinct_text_rows(img) -> list[tuple[int, int]]:
    """Return the y-bands of the image that contain dark "text" pixels.

    Used to prove that consecutive lines of a bubble are drawn at *different* baselines
    (a past regression drew every line at the same y, turning messages into a smear).
    """
    g = img.convert("L")
    px = g.load()
    bands: list[tuple[int, int]] = []
    inband = False
    start = 0
    for y in range(g.height):
        dark = sum(1 for x in range(g.width) if px[x, y] < 120)
        if dark > 3 and not inband:
            start, inband = y, True
        elif dark <= 3 and inband:
            if y - start >= 3:  # ignore single-row noise
                bands.append((start, y - 1))
            inband = False
    if inband:
        bands.append((start, g.height - 1))
    return bands


def test_render_chat_multiline_does_not_overlap():
    # Three explicit newline-separated lines must be laid out on three different
    # baselines, not stacked on top of each other.
    msgs = [{"kind": "assistant",
             "text": "first line of text\nsecond line of text\nthird line of text"}]
    out = chat_render.render_chat(msgs, THEMES["light"].colors(), 900)
    bands = _distinct_text_rows(out.image)
    assert len(bands) >= 3, f"expected >=3 distinct text rows, got {len(bands)}: {bands}"
    # rows must be properly separated vertically
    for (a0, a1), (b0, b1) in zip(bands, bands[1:]):
        assert b0 - a1 >= 5, f"text rows overlap: {bands}"


def test_render_chat_wrapped_paragraph_rows():
    # A long single-paragraph message wraps into several rows; they must not overlap.
    long = "the morning light spilled across the land and whispered of adventure " * 8
    out = chat_render.render_chat([{"kind": "assistant", "text": long}],
                                  THEMES["light"].colors(), 300)
    bands = _distinct_text_rows(out.image)
    assert len(bands) >= 3, f"expected wrapped rows, got {len(bands)}: {bands}"
    for (a0, a1), (b0, b1) in zip(bands, bands[1:]):
        assert b0 - a1 >= 5, f"text rows overlap: {bands}"


def _left_right_dark_bounds(img) -> tuple[int, int]:
    """Return the (leftmost, rightmost) columns that contain dark text pixels."""
    g = img.convert("L")
    px = g.load()
    lo, hi = g.width, -1
    for y in range(g.height):
        for x in range(g.width):
            if px[x, y] < 120:
                lo = min(lo, x)
                hi = max(hi, x)
    return lo, hi


def test_render_chat_wraps_overlong_token_assistant():
    # A long unbreakable token (e.g. a URL) must be broken across lines, never
    # clipped at the right side of the bubble / chat box.
    url = ("https://en.wikipedia.org/wiki/Some_very_long_article_title_about_"
           "sailing_ships_and_ocean_currents_in_the_Atlantic_Ocean")
    out = chat_render.render_chat(
        [{"kind": "assistant", "text": f"see {url} then summarise it for me please"}],
        THEMES["light"].colors(), 320)
    lo, hi = _left_right_dark_bounds(out.image)
    assert hi < out.image.width - 1, f"text clipped at right edge: rightmost={hi}"


def test_render_chat_wraps_overlong_token_user():
    # Right-aligned user bubble: a huge single token must not be clipped on the left.
    out = chat_render.render_chat([{"kind": "user", "text": "x" * 500}],
                                  THEMES["light"].colors(), 320)
    lo, hi = _left_right_dark_bounds(out.image)
    assert lo > 1, f"text clipped at left edge: leftmost={lo}"


def test_render_markdown_basic():
    img = markdown_render.render_markdown("# Title\n\nSome *body* text with **bold**.\n\n"
                                          "- one\n- two\n\n> quote\n\n```python\nprint(1)\n```")
    assert img.mode == "RGBA"
    assert img.width > 40 and img.height > 80


def test_render_markdown_empty():
    img = markdown_render.render_markdown("")
    assert img.width > 0 and img.height > 0


def test_render_markdown_table_and_image_safe():
    img = markdown_render.render_markdown("| a | b |\n|---|---|\n| 1 | 2 |\n\n![missing](nope.png)")
    assert img.width > 40


def test_render_chat_shapes_and_theme():
    messages = [
        {"kind": "system", "text": "Connected"},
        {"kind": "user", "text": "Write a page please."},
        {"kind": "assistant", "text": "I called write_page."},
        {"kind": "tool_call", "text": "write_page(...)"},
        {"kind": "tool_result", "text": "ok"},
        {"kind": "error", "text": "boom"},
    ]
    for palette in (THEMES["light"].colors(), THEMES["dark"].colors()):
        out = chat_render.render_chat(messages, palette, 900)
        assert out.content_w == 900
        assert out.image.mode == "RGBA"
        assert out.content_h > 100


def test_logs_ring_and_render():
    logs.clear_log()
    logs.install()
    logs.get_logger("t").info("hello world")
    logs.get_logger("t").error("an error %s", "x")
    assert logs.take_dirty() is True
    entries = logs.snapshot()
    texts = [e["text"] for e in entries]
    assert any("hello world" in t for t in texts)
    out = log_render.render_log(entries, THEMES["light"].colors(), 900)
    assert out.content_h > 40
    # snapshot copy is independent
    logs.clear_log()
    assert logs.snapshot() == []


def test_log_render_empty():
    out = log_render.render_log([], THEMES["light"].colors(), 800)
    assert out.content_h >= 1
