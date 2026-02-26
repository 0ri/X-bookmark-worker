#!/usr/bin/env python3
"""Tests for delivery.py module."""

import json
import tempfile
from pathlib import Path

import pytest

from .bookmark_queue import add_item, init_db, update_analysis
from .delivery import (
    ACTION_CODES,
    build_button_rows,
    build_next_batch_button,
    format_batch_footer,
    format_item,
    get_category_emoji,
    get_next_batch,
)


@pytest.fixture
def db_path():
    """Create a temporary test database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = f.name
    
    init_db(db)
    yield db
    
    # Cleanup
    Path(db).unlink(missing_ok=True)


def test_get_category_emoji_direct_match():
    """Test direct category emoji matches."""
    assert get_category_emoji("ai") == "🤖"
    assert get_category_emoji("AI") == "🤖"
    assert get_category_emoji("health") == "🏥"
    assert get_category_emoji("tech") == "💻"


def test_get_category_emoji_fuzzy_match():
    """Test fuzzy category emoji matching."""
    assert get_category_emoji("AI Tools") == "🤖"
    assert get_category_emoji("Health & Fitness") == "🏥"
    assert get_category_emoji("Technology News") == "💻"


def test_get_category_emoji_no_match():
    """Test default emoji when no match."""
    assert get_category_emoji("unknown") == "📌"
    assert get_category_emoji("") == "📌"
    assert get_category_emoji(None) == "📌"


def test_format_item_basic(db_path):
    """Test basic item formatting."""
    # Add item with analysis
    item_id = add_item(db_path, {
        "source": "twitter",
        "source_id": "123456",
        "canonical_url": "https://twitter.com/user/status/123456",
        "title": "Test Tweet"
    })
    
    analysis = {
        "title": "@testuser — AI Tools",
        "summary": "This is a great AI tool that helps with productivity.",
        "category": "AI",
        "rationale": "User bookmarked for future reference",
        "content_type": "tweet",
        "buttons": [
            {"text": "🔬 Deep Dive", "action": "deepdive"}
        ],
        "confidence": 0.9,
        "sources": ["https://twitter.com/user/status/123456"]
    }
    
    buttons = [{"text": "🔬 Deep Dive", "action": "deepdive"}]
    
    update_analysis(db_path, item_id, json.dumps(analysis), json.dumps(buttons))
    
    from .bookmark_queue import get_item
    item = get_item(db_path, item_id)
    
    message = format_item(item)
    
    # Check structure
    assert "🤖 AI — @testuser — AI Tools" in message
    assert "This is a great AI tool" in message
    assert "🔗 https://twitter.com/user/status/123456" in message


def test_format_item_under_4000_chars(db_path):
    """Test that formatted items stay under 4000 chars."""
    # Add item with long summary
    item_id = add_item(db_path, {
        "source": "twitter",
        "source_id": "123456",
        "canonical_url": "https://twitter.com/user/status/123456",
        "title": "Long Tweet"
    })
    
    # Create a very long summary
    long_summary = "This is a very long summary. " * 200  # ~6000 chars
    
    analysis = {
        "title": "@testuser — Long Content",
        "summary": long_summary,
        "category": "Tech",
        "rationale": "Test",
        "content_type": "article",
        "buttons": [{"text": "🔬 Deep Dive", "action": "deepdive"}],
        "confidence": 0.8,
        "sources": []
    }
    
    update_analysis(db_path, item_id, json.dumps(analysis), json.dumps(analysis["buttons"]))
    
    from .bookmark_queue import get_item
    item = get_item(db_path, item_id)
    
    message = format_item(item, max_length=4000)
    
    # Must be under 4000 chars
    assert len(message) <= 4000
    
    # Should contain "..." if summary was truncated
    if len(long_summary) > 3800:  # Accounting for header/footer
        assert "..." in message


def test_format_item_truncates_long_analysis(db_path):
    """Test that long analysis is truncated with ellipsis."""
    item_id = add_item(db_path, {
        "source": "twitter",
        "source_id": "123",
        "canonical_url": "https://x.com/test",
        "title": "Test"
    })
    
    # Create summary that will exceed limit
    very_long = "A" * 5000
    
    analysis = {
        "title": "Test",
        "summary": very_long,
        "category": "Test",
        "rationale": "Test",
        "content_type": "tweet",
        "buttons": [],
        "confidence": 0.5,
        "sources": []
    }
    
    update_analysis(db_path, item_id, json.dumps(analysis), "[]")
    
    from .bookmark_queue import get_item
    item = get_item(db_path, item_id)
    
    message = format_item(item, max_length=4000)
    
    assert len(message) <= 4000
    # Message contains "..." from truncated summary, even though URL comes after
    assert "..." in message
    # Verify the summary was actually truncated
    assert "AAA..." in message


def test_build_button_rows_single_button():
    """Test building button rows with single button."""
    buttons_json = json.dumps([
        {"text": "🔬 Deep Dive", "action": "deepdive"}
    ])
    
    rows = build_button_rows(buttons_json, "bk_abc123")
    
    assert len(rows) == 1
    assert len(rows[0]) == 1
    assert rows[0][0]["text"] == "🔬 Deep Dive"
    assert rows[0][0]["callback_data"] == "q|dd|bk_abc123"


def test_build_button_rows_max_three_per_row():
    """Test that button rows have max 3 buttons per row."""
    buttons_json = json.dumps([
        {"text": "🔬 Deep Dive", "action": "deepdive"},
        {"text": "💾 Save Notes", "action": "savenotes"},
        {"text": "📊 Fact Check", "action": "factcheck"},
        {"text": "⚡ Implement", "action": "implement"},
    ])
    
    rows = build_button_rows(buttons_json, "bk_test")
    
    # Should have 2 rows: [3 buttons], [1 button]
    assert len(rows) == 2
    assert len(rows[0]) == 3
    assert len(rows[1]) == 1


def test_build_button_rows_handles_multiple_counts():
    """Test button row building with various counts."""
    # Test 1 button
    rows = build_button_rows(json.dumps([{"text": "A", "action": "deepdive"}]), "bk_1")
    assert len(rows) == 1
    assert len(rows[0]) == 1
    
    # Test 3 buttons (exactly one row)
    buttons_3 = [
        {"text": "A", "action": "deepdive"},
        {"text": "B", "action": "savenotes"},
        {"text": "C", "action": "factcheck"}
    ]
    rows = build_button_rows(json.dumps(buttons_3), "bk_3")
    assert len(rows) == 1
    assert len(rows[0]) == 3
    
    # Test 5 buttons (2 rows: 3 + 2)
    buttons_5 = buttons_3 + [
        {"text": "D", "action": "implement"},
        {"text": "E", "action": "remind"}
    ]
    rows = build_button_rows(json.dumps(buttons_5), "bk_5")
    assert len(rows) == 2
    assert len(rows[0]) == 3
    assert len(rows[1]) == 2
    
    # Test 7 buttons (3 rows: 3 + 3 + 1)
    buttons_7 = buttons_5 + [
        {"text": "F", "action": "fullsummary"},
        {"text": "G", "action": "readsource"}
    ]
    rows = build_button_rows(json.dumps(buttons_7), "bk_7")
    assert len(rows) == 3
    assert len(rows[0]) == 3
    assert len(rows[1]) == 3
    assert len(rows[2]) == 1


def test_build_button_rows_compact_format():
    """Test that button callback_data uses compact format."""
    buttons_json = json.dumps([
        {"text": "🔬 Deep Dive", "action": "deepdive"},
        {"text": "⚡ Implement", "action": "implement"},
    ])
    
    rows = build_button_rows(buttons_json, "bk_xyz789")
    
    # Check compact format: q|{code}|{item_id}
    assert rows[0][0]["callback_data"] == "q|dd|bk_xyz789"
    assert rows[0][1]["callback_data"] == "q|im|bk_xyz789"


def test_all_callback_data_under_64_bytes():
    """Test that all possible callback_data strings are under 64 bytes."""
    # Test all action codes
    test_item_id = "bk_abc12345"  # Reasonable max length
    
    for action, code in ACTION_CODES.items():
        callback_data = f"q|{code}|{test_item_id}"
        byte_length = len(callback_data.encode('utf-8'))
        assert byte_length < 64, f"Callback too long for {action}: {byte_length} bytes"


def test_format_batch_footer():
    """Test batch footer formatting."""
    footer = format_batch_footer(batch_num=1, delivered=5, total=23)
    
    assert "Batch 1" in footer
    assert "5 of 23" in footer
    assert "📋" in footer
    assert "next batch" in footer.lower()


def test_format_batch_footer_correct_counts():
    """Test batch footer with various counts."""
    # First batch
    footer = format_batch_footer(1, 5, 23)
    assert "Batch 1" in footer
    assert "(5 of 23)" in footer
    
    # Middle batch
    footer = format_batch_footer(3, 15, 23)
    assert "Batch 3" in footer
    assert "(15 of 23)" in footer
    
    # Last batch
    footer = format_batch_footer(5, 23, 23)
    assert "Batch 5" in footer
    assert "(23 of 23)" in footer


def test_build_next_batch_button():
    """Test next batch button construction."""
    batch_id = "abc123def456"
    rows = build_next_batch_button(batch_id)
    
    # Should be single row with single button
    assert len(rows) == 1
    assert len(rows[0]) == 1
    
    button = rows[0][0]
    assert button["text"] == "▶ Next 5"
    assert button["callback_data"] == "q|nb|abc123def456"


def test_build_next_batch_button_format():
    """Test next batch button callback format."""
    rows = build_next_batch_button("test_batch_123")
    
    callback = rows[0][0]["callback_data"]
    
    # Should use compact format: q|nb|{batch_id}
    assert callback.startswith("q|nb|")
    assert "test_batch_123" in callback


def test_get_next_batch_empty_queue(db_path):
    """Test get_next_batch with no items."""
    batch = get_next_batch(db_path, batch_size=5)
    assert batch == []


def test_get_next_batch_assigns_batch_id(db_path):
    """Test that get_next_batch assigns batch_id."""
    # Add and analyze 3 items
    for i in range(3):
        item_id = add_item(db_path, {
            "source": "twitter",
            "source_id": f"12345{i}",
            "canonical_url": f"https://x.com/test/{i}",
        })
        
        analysis = {
            "title": f"Test {i}",
            "summary": "Test",
            "category": "Test",
            "rationale": "Test",
            "content_type": "tweet",
            "buttons": [],
            "confidence": 0.5,
            "sources": []
        }
        update_analysis(db_path, item_id, json.dumps(analysis), "[]")
    
    # Get batch
    batch = get_next_batch(db_path, batch_size=5)
    
    assert len(batch) == 3
    
    # All should have same batch_id
    batch_ids = {item["batch_id"] for item in batch}
    assert len(batch_ids) == 1
    assert batch[0]["batch_id"] is not None


def test_format_item_missing_analysis_graceful(db_path):
    """Test that format_item handles missing analysis gracefully."""
    # Add item without analysis
    item_id = add_item(db_path, {
        "source": "twitter",
        "source_id": "123",
        "canonical_url": "https://x.com/test",
        "title": "Test Tweet"
    })
    
    from .bookmark_queue import get_item
    item = get_item(db_path, item_id)
    
    # Should not crash
    message = format_item(item)
    
    assert "Uncategorized" in message or "Test Tweet" in message
    assert "🔗" in message


def test_build_button_rows_invalid_json():
    """Test that invalid buttons_json is handled gracefully."""
    rows = build_button_rows("not valid json", "bk_test")
    assert rows == []


def test_build_button_rows_empty_list():
    """Test that empty button list returns empty rows."""
    rows = build_button_rows("[]", "bk_test")
    assert rows == []
