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


def test_render_markdown_no_text_cropping():
    # Long paragraph that wraps at 760px text width
    md = ("Once upon a time in a world of books and code, the GUI was taking form. "
          "Every single line of text flowed gracefully from margin to margin without "
          "any clipping or truncated sentences whatsoever.")
    img = markdown_render.render_markdown(md, text_width=760, margin=36)
    assert img.width >= 760 + 2 * 36
    lo, hi = _left_right_dark_bounds(img)
    # Dark text must start after margin and end before right edge
    assert lo >= 30, f"text starts too far left: {lo}"
    assert hi < img.width - 20, f"text clipped at right edge: {hi} vs {img.width}"


def test_render_markdown_a4_paper_shape_single_line():
    # Even with a single line of text, the page maintains the A4 paper aspect ratio
    md = "Single line of text."
    img = markdown_render.render_markdown(md, text_width=760, margin=36)
    assert img.width >= 760 + 2 * 36
    ratio = img.height / img.width
    # A4 ratio: 297 / 210 ≈ 1.4142
    assert 1.41 <= ratio <= 1.42
    expected_min_height = int(round(img.width * (297.0 / 210.0)))
    assert img.height >= expected_min_height


def test_markdown_inline_parser_nesting():
    # Test nested bold, italic, code, strikethrough, mark, underline, links
    text = "Plain **bold with *italic* and `code`** and ~~**bold strike**~~ and ==highlighted== and ++underlined++ and [**bold link**](https://example.com)."
    spans = markdown_render.parse_inline(text)
    styles = [s.styles for s in spans]
    # Check that nested styles are captured accurately
    assert () in styles  # plain text
    assert ("bold",) in styles  # bold
    assert ("bold", "italic") in styles  # nested bold-italic
    assert ("bold", "code") in styles  # nested code in bold
    assert ("bold", "strikethrough") in styles or ("strikethrough", "bold") in styles
    assert any("mark" in s.styles for s in spans)
    assert any("underline" in s.styles for s in spans)
    assert any("link" in s.styles and "bold" in s.styles and s.link_url == "https://example.com" for s in spans)


def test_markdown_html_tags_inline():
    text = "<b>bold</b> <i>italic</i> <del>strike</del> <mark>mark</mark> <u>underline</u> <code>code</code> <kbd>Ctrl+C</kbd> <sup>2</sup> <sub>i</sub>"
    spans = markdown_render.parse_inline(text)
    styles_by_text = {s.text: s.styles for s in spans}
    assert "bold" in styles_by_text["bold"]
    assert "italic" in styles_by_text["italic"]
    assert "strikethrough" in styles_by_text["strike"]
    assert "mark" in styles_by_text["mark"]
    assert "underline" in styles_by_text["underline"]
    assert "code" in styles_by_text["code"]
    assert "kbd" in styles_by_text["Ctrl+C"]
    assert "sup" in styles_by_text["2"]
    assert "sub" in styles_by_text["i"]


def test_markdown_escaped_characters():
    text = r"\*not italic\* and \_not italic\_ and \`not code\` and \[not link\]"
    spans = markdown_render.parse_inline(text)
    combined = "".join(s.text for s in spans)
    assert "*not italic*" in combined
    assert "_not italic_" in combined
    assert "`not code`" in combined
    assert "[not link]" in combined
    assert all(s.styles == () for s in spans)


def test_markdown_smart_typography():
    text = "A -- B --- C ... (c) (r) (tm) -> <- != <="
    spans = markdown_render.parse_inline(text)
    combined = "".join(s.text for s in spans)
    assert "–" in combined  # en-dash
    assert "—" in combined  # em-dash
    assert "…" in combined  # ellipsis
    assert "©" in combined
    assert "®" in combined
    assert "™" in combined
    assert "→" in combined
    assert "←" in combined
    assert "≠" in combined
    assert "≤" in combined


def test_render_markdown_task_list_checkboxes():
    md = "- [ ] Unfinished task\n- [x] Completed task\n1. [ ] Numbered task\n1. [x] Numbered done"
    img = markdown_render.render_markdown(md, text_width=600, margin=30)
    assert img.mode == "RGBA"
    assert img.width >= 660
    assert img.height > 100


def test_render_markdown_alerts():
    md = (
        "> [!NOTE]\n"
        "> This is a note with **bold** text and `code`.\n\n"
        "> [!TIP]\n"
        "> Here is an actionable tip.\n\n"
        "> [!WARNING]\n"
        "> Proceed with caution.\n\n"
        "> [!IMPORTANT]\n"
        "> Key requirement.\n\n"
        "> [!CAUTION]\n"
        "> Dangerous operation.\n"
    )
    img = markdown_render.render_markdown(md, text_width=600, margin=30)
    assert img.mode == "RGBA"
    assert img.height > 200


def test_render_markdown_syntax_highlighted_code():
    md = (
        "```python\n"
        "# Calculate fibonacci\n"
        "def fib(n: int) -> int:\n"
        "    if n <= 1:\n"
        "        return n\n"
        "    return fib(n - 1) + fib(n - 2)\n"
        "```\n\n"
        "```javascript\n"
        "// A JS function\n"
        "function greet(name) {\n"
        "    const message = `Hello, ${name}!`;\n"
        "    console.log(message);\n"
        "    return 42;\n"
        "}\n"
        "```\n"
    )
    img = markdown_render.render_markdown(md, text_width=700, margin=30)
    assert img.mode == "RGBA"
    assert img.height > 200


def test_render_markdown_math_and_deflist():
    md = (
        "$$\n"
        "E = mc^2\n"
        "\\int_{0}^{\\infty} e^{-x^2} dx = \\frac{\\sqrt{\\pi}}{2}\n"
        "$$\n\n"
        "Euler's formula: $e^{i\\pi} + 1 = 0$\n\n"
        "Python\n"
        ": A high-level, interpreted programming language.\n\n"
        "Pillow\n"
        ": The friendly Python Imaging Library fork.\n"
    )
    img = markdown_render.render_markdown(md, text_width=700, margin=30)
    assert img.mode == "RGBA"
    assert img.height > 200


def test_render_markdown_rich_table():
    md = (
        "| Feature | Status | Notes |\n"
        "| :--- | :---: | ---: |\n"
        "| **Bold** & *Italic* | `Supported` | ~~Deprecated~~ |\n"
        "| [Link](https://example.com) | ==Active== | 100% |\n"
        "| `Inline Code` | `Done` | Sub<sub>2</sub> and Sup<sup>2</sup> |\n"
    )
    img = markdown_render.render_markdown(md, text_width=700, margin=30)
    assert img.mode == "RGBA"
    assert img.height > 150


def test_bundled_fonts_location():
    import os
    from pathlib import Path
    from app import chat_render, markdown_render
    from app.gui import widgets

    # Ensure dynamic path to non_py/fonts is used
    non_py_fonts = Path(markdown_render._NON_PY_FONTS_DIR)
    assert non_py_fonts.exists()
    assert (non_py_fonts / "DejaVuSerif.ttf").exists()
    assert (non_py_fonts / "DejaVuSans.ttf").exists()
    assert (non_py_fonts / "DejaVuSansMono.ttf").exists()

    # Verify markdown_render candidate priority
    assert markdown_render._FONT_CANDIDATES[0] == non_py_fonts

    # Verify chat_render uses bundled fonts directory
    assert chat_render._FONT_DIR == non_py_fonts

    # Verify widgets uses bundled fonts directory
    assert widgets._DEJAVU_DIR == non_py_fonts
    assert "non_py/fonts" in widgets.font_file()


def test_render_markdown_pages_explicit_break():
    md = (
        "# Page 1 Title\n\nContent for the first page.\n\n"
        "<!-- pagebreak -->\n\n"
        "# Page 2 Title\n\nContent for the second page.\n\n"
        "\\pagebreak\n\n"
        "# Page 3 Title\n\nContent for the third page."
    )
    pages = markdown_render.render_markdown_pages(md, text_width=760, margin=36)
    assert len(pages) == 3
    for p in pages:
        assert p.mode == "RGBA"
        assert p.width == 760 + 2 * 36
        ratio = p.height / p.width
        assert 1.41 <= ratio <= 1.42


def test_render_markdown_pages_height_overflow():
    # Long content that exceeds a single A4 page height
    long_para = "This is a substantial paragraph of text written for our novel. " * 30
    paragraphs = [f"## Section {i}\n\n{long_para}\n" for i in range(1, 10)]
    md = "\n".join(paragraphs)

    pages = markdown_render.render_markdown_pages(md, text_width=760, margin=36)
    assert len(pages) >= 2, f"expected at least 2 pages from overflow, got {len(pages)}"
    for p in pages:
        ratio = p.height / p.width
        assert 1.41 <= ratio <= 1.42


def test_render_markdown_list_no_cumulative_indentation():
    md = "- First item\n- Second item\n- Third item\n- Fourth item\n- Fifth item"
    blocks = markdown_render.parse_blocks(md)
    assert len(blocks) == 1
    assert blocks[0].type == "ul"
    items = blocks[0].data["items"]
    assert len(items) == 5
    for it in items:
        assert it["children"] == []

    fonts = markdown_render.FontSet.system()
    palette = markdown_render.DEFAULT_PALETTE
    renderer = markdown_render.Renderer(fonts, palette, max_text_width=700, margin=30)
    rendered_items, _, _ = renderer.render(blocks)
    markers = [prim for y, prim in rendered_items if prim[0] == "list_marker"]
    assert len(markers) == 5
    x_positions = [m[1] for m in markers]
    assert len(set(x_positions)) == 1, f"Expected all markers at same x, got {x_positions}"
    assert x_positions[0] == 30

    paged = markdown_render.PagedRenderer(fonts, palette, max_text_width=700, margin=30)
    pages = paged.render(blocks)
    assert len(pages) >= 1
    paged_markers = [prim for y, prim in pages[0] if prim[0] == "list_marker"]
    assert len(paged_markers) == 5
    paged_x = [m[1] for m in paged_markers]
    assert len(set(paged_x)) == 1
    assert paged_x[0] == 30


def test_render_markdown_nested_list_indentation():
    md = "- Parent 1\n  - Child 1.1\n  - Child 1.2\n- Parent 2"
    blocks = markdown_render.parse_blocks(md)
    assert len(blocks) == 1
    assert blocks[0].type == "ul"
    items = blocks[0].data["items"]
    assert len(items) == 2
    assert len(items[0]["children"]) == 2
    assert len(items[1]["children"]) == 0

    fonts = markdown_render.FontSet.system()
    palette = markdown_render.DEFAULT_PALETTE
    renderer = markdown_render.Renderer(fonts, palette, max_text_width=700, margin=30)
    rendered_items, _, _ = renderer.render(blocks)
    markers = [prim for y, prim in rendered_items if prim[0] == "list_marker"]
    assert len(markers) == 4
    # Parent 1: 30, Child 1.1: 52 (30 + 22), Child 1.2: 52, Parent 2: 30
    assert markers[0][1] == 30
    assert markers[1][1] == 52
    assert markers[2][1] == 52
    assert markers[3][1] == 30


def test_render_markdown_ordered_list_numbers():
    md = "1. First\n2. Second\n3. Third"
    blocks = markdown_render.parse_blocks(md)
    assert len(blocks) == 1
    assert blocks[0].type == "ol"
    items = blocks[0].data["items"]
    assert len(items) == 3
    assert [it["number"] for it in items] == [1, 2, 3]

    fonts = markdown_render.FontSet.system()
    palette = markdown_render.DEFAULT_PALETTE
    renderer = markdown_render.Renderer(fonts, palette, max_text_width=700, margin=30)
    rendered_items, _, _ = renderer.render(blocks)
    markers = [prim for y, prim in rendered_items if prim[0] == "list_marker"]
    assert [m[3] for m in markers] == ["1. ", "2. ", "3. "]






