"""Tool schema + dispatcher tests (all tool names, validation, section helpers)."""

from __future__ import annotations

import pytest

from app import model_registry as reg
from app.books_store import BookStore
from app.tools import (ToolContext, ToolError, append_text, dispatch,
                       format_tool_result, replace_section)


@pytest.fixture()
def ctx(tmp_path):
    store = BookStore(tmp_path / "books")
    events = []
    return ToolContext(store, on_event=events.append), events


def test_registry_has_expected_tools():
    names = {s["function"]["name"] for s in reg.tool_schemas()}
    assert names == {
        "create_book", "remove_book", "list_books", "get_book_info",
        "list_chapters", "read_chapter", "write_chapter", "edit_chapter", "delete_chapter",
        "list_pages", "read_page", "write_page", "edit_page", "delete_page", "search_in_book",
    }
    # required JSON-schema fields OpenAI expects
    for schema in reg.tool_schemas():
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"]
        assert fn["parameters"]["type"] == "object"
        assert fn["description"]


def test_unknown_tool(ctx):
    tc, _ = ctx
    with pytest.raises(ToolError, match="unknown tool"):
        dispatch(tc, "fly_to_moon", {})


def test_missing_required_argument(ctx):
    tc, _ = ctx
    with pytest.raises(ToolError, match="missing required"):
        dispatch(tc, reg.CREATE_BOOK, {})


def test_unexpected_argument(ctx):
    tc, _ = ctx
    with pytest.raises(ToolError, match="unexpected argument"):
        dispatch(tc, reg.CREATE_BOOK, {"name": "x", "nonsense": 1})


def test_create_and_author_via_dispatch(ctx):
    tc, events = ctx
    res = dispatch(tc, reg.CREATE_BOOK, {"name": "Dune Bugs", "author": "P. Muad"})
    assert res["ok"] is True and "Dune Bugs" in res["message"]
    res = dispatch(tc, reg.WRITE_PAGE,
                   {"name": "Dune Bugs", "page_name": "Chapter 1",
                    "content": "# Chapter 1\n\nSandworms."})
    assert res["ok"] is True
    # events fired for the GUI
    kinds = [e.get("kind") for e in events]
    assert "book_created" in kinds and "page_written" in kinds

    got = dispatch(tc, reg.READ_PAGE,
                   {"name": "Dune Bugs", "page_name": "chapter 1"})
    assert got["ok"] is True and "Sandworms" in got["content"]

    listing = dispatch(tc, reg.LIST_BOOKS, {})
    assert listing["books"][0]["name"] == "Dune Bugs"

    pages = dispatch(tc, reg.LIST_PAGES, {"name": "Dune Bugs"})
    assert pages["pages"] == ["Chapter 1"]


def test_edit_page_append_and_replace_section(ctx):
    tc, _ = ctx
    dispatch(tc, reg.CREATE_BOOK, {"name": "Cookbook"})
    dispatch(tc, reg.WRITE_PAGE, {"name": "Cookbook", "page_name": "Soups",
                                  "content": "# Intro\n\nSome soup talk."})
    res = dispatch(tc, reg.EDIT_PAGE,
                   {"name": "Cookbook", "page_name": "Soups", "operation": "append",
                    "content": "And more soup."})
    assert res["ok"] is True
    content = dispatch(tc, reg.READ_PAGE,
                       {"name": "Cookbook", "page_name": "Soups"})["content"]
    assert "And more soup." in content

    res = dispatch(tc, reg.EDIT_PAGE,
                   {"name": "Cookbook", "page_name": "Soups",
                    "operation": "replace_section", "section": "Intro",
                    "content": "A new intro paragraph."})
    assert res["ok"] is True
    content = dispatch(tc, reg.READ_PAGE,
                       {"name": "Cookbook", "page_name": "Soups"})["content"]
    assert content.startswith("# Intro\nA new intro paragraph.")
    # bad enum value is rejected by validation
    with pytest.raises(ToolError):
        dispatch(tc, reg.EDIT_PAGE,
                 {"name": "Cookbook", "page_name": "Soups",
                  "operation": "delete_everything"})


def test_search_and_delete_tools(ctx):
    tc, _ = ctx
    dispatch(tc, reg.CREATE_BOOK, {"name": "Notes"})
    dispatch(tc, reg.WRITE_PAGE, {"name": "Notes", "page_name": "One",
                                  "content": "The ziggurat stands."})
    hits = dispatch(tc, reg.SEARCH_IN_BOOK,
                    {"name": "Notes", "query": "ZIGGURAT"})
    assert hits["ok"] and len(hits["hits"]) == 1
    res = dispatch(tc, reg.DELETE_PAGE, {"name": "Notes", "page_name": "one"})
    assert res["ok"] is True
    res = dispatch(tc, reg.READ_PAGE, {"name": "Notes", "page_name": "one"})
    assert res["ok"] is False


def test_remove_book_tool(ctx):
    tc, _ = ctx
    dispatch(tc, reg.CREATE_BOOK, {"name": "Temp"})
    res = dispatch(tc, reg.REMOVE_BOOK, {"name": "temp"})
    assert res["ok"] is True
    res = dispatch(tc, reg.REMOVE_BOOK, {"name": "temp"})
    assert res["ok"] is False


def test_section_helpers():
    text = "# Title\n\nlead\n\n## Intro\nold\n## Other\nkeep"
    updated = replace_section(text, "Intro", "brand new")
    assert "brand new" in updated and "old" not in updated and "keep" in updated
    with pytest.raises(ToolError):
        replace_section(text, "Missing", "x")

    out = append_text("First.", [], "Second.")
    assert out.startswith("First.\n\nSecond")
    out = append_text("", [], "Only")
    assert out.strip() == "Only"


def test_format_tool_result():
    assert format_tool_result("x", {"ok": True, "message": "done"}) == "done"
    assert format_tool_result("x", {"ok": True}) == "x succeeded"


def test_tools_with_book_id(ctx):
    tc, _ = ctx
    create_res = dispatch(tc, reg.CREATE_BOOK, {"name": "UUID Book", "author": "Author"})
    assert create_res["ok"] is True
    book_id = create_res["book"]["id"]
    assert book_id

    # Write page using book_id
    write_res = dispatch(tc, reg.WRITE_PAGE, {
        "name": "UUID Book",
        "book_id": book_id,
        "page_name": "Chapter 1",
        "content": "# Ch 1\n\nMagic happens.",
    })
    assert write_res["ok"] is True
    assert write_res["book_id"] == book_id

    # Read page using book_id
    read_res = dispatch(tc, reg.READ_PAGE, {
        "name": "UUID Book",
        "book_id": book_id,
        "page_name": "Chapter 1",
    })
    assert read_res["ok"] is True
    assert "Magic" in read_res["content"]

    # List pages using book_id
    list_res = dispatch(tc, reg.LIST_PAGES, {
        "name": "UUID Book",
        "book_id": book_id,
    })
    assert list_res["ok"] is True
    assert list_res["book_id"] == book_id
    assert list_res["pages"] == ["Chapter 1"]

    # Search using book_id
    search_res = dispatch(tc, reg.SEARCH_IN_BOOK, {
        "name": "UUID Book",
        "book_id": book_id,
        "query": "magic",
    })
    assert search_res["ok"] is True
    assert len(search_res["hits"]) == 1

    # Remove book using book_id
    remove_res = dispatch(tc, reg.REMOVE_BOOK, {
        "name": "UUID Book",
        "book_id": book_id,
    })
    assert remove_res["ok"] is True


def test_chapter_tools_dispatch(ctx):
    tc, events = ctx
    # Create book
    res = dispatch(tc, reg.CREATE_BOOK, {"name": "SciFi Epic", "author": "Isaac"})
    book_id = res["book"]["id"]

    # Write chapters passing book_id and chapter_number
    w1 = dispatch(tc, reg.WRITE_CHAPTER, {
        "book_id": book_id,
        "chapter_number": 1,
        "title": "Prologue",
        "content": "# Prologue\n\nIn the beginning.",
    })
    assert w1["ok"] is True
    assert w1["chapter_number"] == 1

    w2 = dispatch(tc, reg.WRITE_CHAPTER, {
        "book_id": book_id,
        "chapter_number": 2,
        "title": "First Contact",
        "content": "# First Contact\n\nThey arrived quietly.",
    })
    assert w2["ok"] is True

    # get_book_info
    info = dispatch(tc, reg.GET_BOOK_INFO, {"book_id": book_id})
    assert info["ok"] is True
    assert info["name"] == "SciFi Epic"
    assert info["chapter_count"] == 2
    assert [c["title"] for c in info["chapters"]] == ["Prologue", "First Contact"]

    # list_chapters
    ch_list = dispatch(tc, reg.LIST_CHAPTERS, {"book_id": book_id})
    assert ch_list["ok"] is True
    assert len(ch_list["chapters"]) == 2

    # read_chapter
    r1 = dispatch(tc, reg.READ_CHAPTER, {"book_id": book_id, "chapter_number": 1})
    assert r1["ok"] is True
    assert "In the beginning" in r1["content"]

    # edit_chapter (append)
    e1 = dispatch(tc, reg.EDIT_CHAPTER, {
        "book_id": book_id,
        "chapter_number": 1,
        "operation": "append",
        "content": "A new dawn broke.",
    })
    assert e1["ok"] is True
    r1_after = dispatch(tc, reg.READ_CHAPTER, {"book_id": book_id, "chapter_number": 1})
    assert "A new dawn broke." in r1_after["content"]

    # delete_chapter
    d1 = dispatch(tc, reg.DELETE_CHAPTER, {"book_id": book_id, "chapter_number": 1})
    assert d1["ok"] is True
    info_after = dispatch(tc, reg.GET_BOOK_INFO, {"book_id": book_id})
    assert info_after["chapter_count"] == 1
    assert info_after["chapters"][0]["title"] == "First Contact"
    assert info_after["chapters"][0]["chapter_number"] == 1


