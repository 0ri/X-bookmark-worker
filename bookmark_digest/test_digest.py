#!/usr/bin/env python3
"""Tests for callback parsing and handling."""

from bookmark_digest.test_utils import temp_db, sample_queue_item
from bookmark_digest.bookmark_queue import add_item, get_item
from bookmark_digest.callbacks import parse_callback, handle_callback


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
    test_parse_callback()
    test_handle_callback_skip()
    test_handle_callback_deepdive()
    test_handle_callback_invalid()
    print("\n✅ CALLBACK TESTS PASSED")
