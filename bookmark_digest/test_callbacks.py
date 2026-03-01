#!/usr/bin/env python3
"""Tests for callbacks.py module.

Updated for Session 5 API:
- parse_callback returns tuple[str, str] and raises ValueError
- handle_callback requires delivered status
- Response format is action-specific dict
"""

import json
import tempfile
from pathlib import Path

import pytest

from .bookmark_queue import (
    add_item, init_db, update_analysis, get_item,
    store_analyses, set_sending, mark_delivered_with_message,
    mark_acted_on, update_status,
)
from .callbacks import ACTION_CODES, handle_callback, parse_callback


@pytest.fixture
def db_path():
    """Create a temporary test database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = f.name

    init_db(db)
    yield db

    # Cleanup
    Path(db).unlink(missing_ok=True)


def _make_delivered(db_path, source_id="123456"):
    """Create item and advance to delivered state."""
    item_id = add_item(db_path, {
        "source": "twitter",
        "source_id": source_id,
        "canonical_url": "https://x.com/test",
        "title": "Test"
    })
    store_analyses(db_path, [{
        "item_id": item_id,
        "category": "Test",
        "analysis": "Test analysis",
        "buttons": ["dd"],
        "content_type": "tweet",
    }])
    set_sending(db_path, item_id)
    mark_delivered_with_message(db_path, item_id, "tg_123", "batch_1")
    return item_id


def test_parse_callback_compact_format():
    """Test parsing compact callback format."""
    code, item_id = parse_callback("q|dd|bk_abc123")

    assert code == "dd"
    assert item_id == "bk_abc123"


def test_parse_callback_compact_all_actions():
    """Test parsing compact format for all action codes."""
    test_cases = [
        ("q|dd|bk_1", "dd"),
        ("q|im|bk_2", "im"),
        ("q|fc|bk_3", "fc"),
        ("q|sn|bk_4", "sn"),
        ("q|rm|bk_5", "rm"),
        ("q|fs|bk_6", "fs"),
        ("q|rs|bk_7", "rs"),
    ]

    for callback_data, expected_code in test_cases:
        code, item_id = parse_callback(callback_data)
        assert code == expected_code
        assert item_id  # non-empty


def test_parse_callback_compact_next_batch():
    """Test parsing compact format for next_batch action."""
    code, batch_id = parse_callback("q|nb|batch_abc123")

    assert code == "nb"
    assert batch_id == "batch_abc123"


def test_parse_callback_legacy_format():
    """Test parsing legacy callback format."""
    code, item_id = parse_callback("queue_deepdive_bk_abc123")

    assert code == "dd"
    assert item_id == "bk_abc123"


def test_parse_callback_legacy_all_actions():
    """Test parsing legacy format for all actions."""
    test_cases = [
        ("queue_deepdive_bk_1", "dd"),
        ("queue_implement_bk_2", "im"),
        ("queue_factcheck_bk_3", "fc"),
        ("queue_savenotes_bk_4", "sn"),
        ("queue_remind_bk_5", "rm"),
        ("queue_fullsummary_bk_6", "fs"),
        ("queue_readsource_bk_7", "rs"),
    ]

    for callback_data, expected_code in test_cases:
        code, item_id = parse_callback(callback_data)
        assert code == expected_code


def test_parse_callback_invalid_format():
    """Test that invalid callback formats raise ValueError."""
    invalid_cases = [
        "",
        "invalid",
        "q|",
        "q|xx|bk_1",  # Unknown code
        "queue_",
        "queue_unknown_bk_1",  # Unknown action
        "random_string_bk_1",
        None,
        123,  # Not a string
    ]

    for invalid in invalid_cases:
        with pytest.raises(ValueError):
            parse_callback(invalid)


def test_handle_callback_compact_format_success(db_path):
    """Test handling compact format callback successfully."""
    item_id = _make_delivered(db_path)

    result = handle_callback(db_path, f"q|dd|{item_id}")

    assert result["success"] is True
    assert result["action"] == "deep_dive"
    assert result["item_id"] == item_id


def test_handle_callback_legacy_format_success(db_path):
    """Test handling legacy format callback successfully."""
    item_id = _make_delivered(db_path, "legacy_test")

    result = handle_callback(db_path, f"queue_deepdive_{item_id}")

    assert result["success"] is True
    assert result["action"] == "deep_dive"


def test_handle_callback_item_not_found(db_path):
    """Test handling callback for non-existent item."""
    result = handle_callback(db_path, "q|dd|bk_nonexistent")

    assert result["success"] is False
    assert "error" in result
    assert "not found" in result["error"].lower()


def test_handle_callback_already_acted_on(db_path):
    """Test handling callback for already-acted item."""
    item_id = _make_delivered(db_path)

    # First callback - should succeed
    result1 = handle_callback(db_path, f"q|dd|{item_id}")
    assert result1["success"] is True

    # Second callback - should return warning (item is now acted_on)
    result2 = handle_callback(db_path, f"q|dd|{item_id}")
    assert result2["success"] is True
    assert "warning" in result2
    assert "already processed" in result2["warning"].lower()


def test_handle_callback_next_batch(db_path):
    """Test handling next_batch callback."""
    batch_id = "test_batch_123"
    result = handle_callback(db_path, f"q|nb|{batch_id}")

    assert result["success"] is True
    assert result["action"] == "next_batch"
    assert result["batch_id"] == batch_id


def test_handle_callback_invalid_format(db_path):
    """Test handling invalid callback format."""
    result = handle_callback(db_path, "invalid_callback_format")

    assert result["success"] is False
    assert "error" in result
    assert "invalid" in result["error"].lower()


def test_handle_callback_unknown_action(db_path):
    """Test handling unknown action code."""
    result = handle_callback(db_path, "q|xx|bk_test")

    assert result["success"] is False
    assert "error" in result


def test_action_codes_mapping_complete():
    """Test that all action codes map correctly."""
    expected_codes = {
        "deepdive": "dd",
        "implement": "im",
        "factcheck": "fc",
        "savenotes": "sn",
        "remind": "rm",
        "fullsummary": "fs",
        "readsource": "rs",
        "next_batch": "nb",
    }

    assert ACTION_CODES == expected_codes


def test_action_codes_all_two_chars():
    """Test that all action codes are exactly 2 characters."""
    for action, code in ACTION_CODES.items():
        assert len(code) == 2, f"Action code for '{action}' must be 2 chars, got: '{code}'"


def test_handle_callback_marks_acted_on(db_path):
    """Test that callback marks item as acted_on."""
    item_id = _make_delivered(db_path)

    # Check initial status
    item_before = get_item(db_path, item_id)
    assert item_before["status"] == "delivered"

    # Handle callback
    handle_callback(db_path, f"q|dd|{item_id}")

    # Check status changed
    item_after = get_item(db_path, item_id)
    assert item_after["status"] == "acted_on"


def test_handle_callback_all_actions_mark_acted_on(db_path):
    """Test that all action types mark item as acted_on."""
    actions_to_test = ["dd", "im", "fc", "sn", "rm", "fs", "rs"]

    for code in actions_to_test:
        item_id = _make_delivered(db_path, f"test_{code}")

        result = handle_callback(db_path, f"q|{code}|{item_id}")

        assert result["success"] is True, f"Failed for code: {code}"

        item = get_item(db_path, item_id)
        assert item["status"] == "acted_on", f"Status not updated for code: {code}"


def test_parse_callback_preserves_item_id_format():
    """Test that various item_id formats are preserved correctly."""
    test_ids = [
        "bk_abc123",
        "bk_123abc",
        "rd_xyz789",
        "yt_video123",
    ]

    for item_id in test_ids:
        code, parsed_id = parse_callback(f"q|dd|{item_id}")
        assert parsed_id == item_id


def test_handle_callback_completed_status_warning(db_path):
    """Test that completed items are rejected (not in delivered state)."""
    item_id = _make_delivered(db_path, "completed_test")

    # Move through acted_on → completed
    mark_acted_on(db_path, item_id)
    update_status(db_path, item_id, "completed")

    # Callback on completed item should fail (not in delivered state)
    result = handle_callback(db_path, f"q|dd|{item_id}")

    # completed is not delivered or acted_on, so it's rejected
    assert result["success"] is False
    assert "not in delivered state" in result["error"]
