#!/usr/bin/env python3
"""Tests for digest formatting and callbacks."""

from bookmark_digest.test_utils import temp_db, sample_queue_item
from bookmark_digest.bookmark_queue import add_item, get_item
from bookmark_digest.__main__ import format_digest, format_item_detail
from bookmark_digest.callbacks import parse_callback, handle_callback


def test_format_digest():
    """Test digest formatting with items — plain text, no MarkdownV2."""
    items = [
        {"id": "bk_001", "title": "@alice — AI breakthrough paper",
         "category": "AI", "summary": "New SOTA model beats GPT-4 on all benchmarks.",
         "engagement": "❤️590 | 🔁59 | 💬26",
         "canonical_url": "https://x.com/1"},
        {"id": "bk_002", "title": "@bob — Rust ecosystem update",
         "category": "TECH", "summary": "Systems programming renaissance in 2024.",
         "engagement": "❤️200 | 🔁30 | 💬10",
         "canonical_url": "https://x.com/2"},
    ]
    result = format_digest(items)

    assert result["item_count"] == 2
    assert result["total_count"] == 2
    msg = result["message"]
    assert "Twitter Bookmarks" in msg
    # No MarkdownV2 escaping characters
    assert "\\(" not in msg
    assert "\\." not in msg
    assert "\\—" not in msg
    # Has plain markdown bold
    assert "**" in msg
    # Contains titles and summaries
    assert "@alice" in msg
    assert "SOTA model" in msg
    assert "❤️590" in msg
    assert "🔗 https://x.com/1" in msg
    # Buttons
    assert len(result["buttons"]) == 2
    assert result["buttons"][0][0]["callback_data"] == "queue_deepdive_bk_001"
    print("✓ format_digest: plain text format, no duplication")


def test_format_digest_no_duplication():
    """Test that summary is NOT shown if it equals the title."""
    items = [
        {"id": "bk_003", "title": "Same text",
         "category": "GENERAL", "summary": "Same text",
         "canonical_url": "https://x.com/3"},
    ]
    result = format_digest(items)
    msg = result["message"]
    # "Same text" should appear once for title, NOT repeated as summary
    count = msg.count("Same text")
    assert count == 1, f"Expected 1 occurrence, got {count}"
    print("✓ format_digest: no duplication when summary == title")


def test_format_digest_truncation():
    """Test digest truncation with max_items."""
    items = [{"id": f"bk_{i:03d}", "title": f"@user — Item {i}", "category": "GENERAL",
              "summary": f"Summary of item {i}"} for i in range(15)]
    result = format_digest(items, max_items=5)

    assert result["item_count"] == 5
    assert result["total_count"] == 15
    assert "10 more" in result["message"]
    print("✓ format_digest: truncation")


def test_format_digest_empty():
    """Test digest with no items."""
    result = format_digest([])
    assert result["item_count"] == 0
    assert "No new bookmarks" in result["message"]
    print("✓ format_digest: empty")


def test_format_item_detail():
    """Test single item detail formatting — plain text."""
    item = {
        "title": "@alice — Test Item",
        "category": "AI",
        "summary": "Great stuff about AI models and their capabilities.",
        "engagement": "❤️500 | 🔁50 | 💬20",
        "canonical_url": "https://example.com",
        "raw_content": "x" * 500,
    }
    detail = format_item_detail(item)

    assert "**@alice — Test Item**" in detail
    assert "❤️500" in detail
    assert "Great stuff" in detail
    assert "🔗 https://example.com" in detail
    assert "500 chars" in detail
    # No MarkdownV2 escaping
    assert "\\(" not in detail
    print("✓ format_item_detail: plain text format")


def test_format_item_detail_minimal():
    """Test detail view with minimal fields."""
    item = {"title": "Minimal", "category": "GENERAL"}
    detail = format_item_detail(item)
    assert "Minimal" in detail
    # Should not crash with missing fields
    print("✓ format_item_detail: minimal fields")


def test_parse_callback():
    """Test callback parsing — v2 compact + legacy formats."""
    # Compact v2 format: returns tuple (code, item_id)
    assert parse_callback("q|dd|bk_001") == ("dd", "bk_001")
    # Legacy format
    assert parse_callback("queue_deepdive_bk_001") == ("dd", "bk_001")
    # Invalid: raises ValueError
    import pytest
    for invalid in ["invalid", "", None]:
        with pytest.raises(ValueError):
            parse_callback(invalid)
    print("✓ parse_callback")


def test_handle_callback_skip():
    """Test save_notes callback — requires delivered status."""
    from .bookmark_queue import store_analyses, set_sending, mark_delivered_with_message
    with temp_db() as db:
        item_id = add_item(db, sample_queue_item("t1"))
        # Advance to delivered
        store_analyses(db, [{"item_id": item_id, "category": "Test", "analysis": "T", "buttons": ["sn"], "content_type": "tweet"}])
        set_sending(db, item_id)
        mark_delivered_with_message(db, item_id, "tg_1", "batch_1")
        result = handle_callback(db, f"q|sn|{item_id}")

        assert result["success"] is True
        assert result["action"] == "save_notes"
        assert result["item_id"] == item_id
        print("✓ handle_callback: save_notes (v2 format)")


def test_handle_callback_deepdive():
    """Test deepdive callback — requires delivered status."""
    from .bookmark_queue import store_analyses, set_sending, mark_delivered_with_message
    with temp_db() as db:
        item_id = add_item(db, sample_queue_item("t2"))
        # Advance to delivered
        store_analyses(db, [{"item_id": item_id, "category": "Test", "analysis": "T", "buttons": ["dd"], "content_type": "tweet"}])
        set_sending(db, item_id)
        mark_delivered_with_message(db, item_id, "tg_2", "batch_2")
        result = handle_callback(db, f"q|dd|{item_id}")

        assert result["success"] is True
        assert result["action"] == "deep_dive"
        assert result["item_id"] == item_id
        print("✓ handle_callback: deepdive (v2 format)")


def test_handle_callback_invalid():
    """Test invalid callbacks — v2 error format."""
    with temp_db() as db:
        r1 = handle_callback(db, "q|dd|nonexistent")
        assert r1["success"] is False
        assert "not found" in r1["error"].lower()

        r2 = handle_callback(db, "garbage")
        assert r2["success"] is False
        print("✓ handle_callback: invalid (v2 format)")


if __name__ == "__main__":
    test_format_digest()
    test_format_digest_no_duplication()
    test_format_digest_truncation()
    test_format_digest_empty()
    test_format_item_detail()
    test_format_item_detail_minimal()
    test_parse_callback()
    test_handle_callback_skip()
    test_handle_callback_deepdive()
    test_handle_callback_invalid()
    print("\n✅ DIGEST TESTS PASSED")
