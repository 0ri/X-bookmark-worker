#!/usr/bin/env python3
"""Tests for council v3 critical fixes.

Covers:
1. Shadowed update_status removal (only FSM-validated version exists)
2. recover_sending() crash recovery
3. HTML escaping in delivery messages
4. Unknown action rejection in build_button_rows
"""

import json
import tempfile
from pathlib import Path

import pytest

from .test_utils import temp_db, sample_queue_item
from .bookmark_queue import (
    add_item, get_item, update_status, update_analysis,
    set_sending, recover_sending,
)
from .delivery import format_item, build_button_rows
from .__main__ import format_delivery_message


# ============================================================================
# Fix 1: update_status has FSM validation (shadowed version removed)
# ============================================================================

class TestUpdateStatusFSM:
    """Verify update_status enforces transition rules."""

    def test_invalid_transition_raises(self):
        with temp_db() as db:
            item_id = add_item(db, sample_queue_item("t001"))
            # pending → delivered is invalid (must go through analyzed first)
            with pytest.raises(ValueError, match="Invalid status transition"):
                update_status(db, item_id, "delivered")

    def test_valid_transition_works(self):
        with temp_db() as db:
            item_id = add_item(db, sample_queue_item("t001"))
            assert update_status(db, item_id, "analyzed")
            item = get_item(db, item_id)
            assert item["status"] == "analyzed"

    def test_no_attempt_count_increment(self):
        """FSM update_status should NOT increment attempt_count."""
        with temp_db() as db:
            item_id = add_item(db, sample_queue_item("t001"))
            update_status(db, item_id, "analyzed")
            item = get_item(db, item_id)
            assert item["attempt_count"] == 0


# ============================================================================
# Fix 2: recover_sending()
# ============================================================================

class TestRecoverSending:
    """Test crash recovery for items stuck in 'sending' state."""

    def test_recover_sending_resets_to_analyzed(self):
        with temp_db() as db:
            item_id = add_item(db, sample_queue_item("t001"))
            update_analysis(db, item_id, '{"test": true}', '["dd"]')
            assert set_sending(db, item_id)
            assert get_item(db, item_id)["status"] == "sending"

            count = recover_sending(db)
            assert count == 1
            assert get_item(db, item_id)["status"] == "analyzed"

    def test_recover_sending_no_items(self):
        with temp_db() as db:
            add_item(db, sample_queue_item("t001"))
            count = recover_sending(db)
            assert count == 0

    def test_recover_sending_multiple_items(self):
        with temp_db() as db:
            ids = []
            for i in range(3):
                item_id = add_item(db, sample_queue_item(f"t{i:03d}"))
                update_analysis(db, item_id, '{}', '["dd"]')
                set_sending(db, item_id)
                ids.append(item_id)

            count = recover_sending(db)
            assert count == 3
            for item_id in ids:
                assert get_item(db, item_id)["status"] == "analyzed"

    def test_recover_sending_leaves_other_statuses(self):
        with temp_db() as db:
            # One pending, one analyzed, one sending
            id_pending = add_item(db, sample_queue_item("t001"))
            id_analyzed = add_item(db, sample_queue_item("t002"))
            id_sending = add_item(db, sample_queue_item("t003"))

            update_analysis(db, id_analyzed, '{}', '["dd"]')
            update_analysis(db, id_sending, '{}', '["dd"]')
            set_sending(db, id_sending)

            count = recover_sending(db)
            assert count == 1

            assert get_item(db, id_pending)["status"] == "pending"
            assert get_item(db, id_analyzed)["status"] == "analyzed"
            assert get_item(db, id_sending)["status"] == "analyzed"


# ============================================================================
# Fix 3: HTML escaping in delivery messages
# ============================================================================

class TestHTMLEscaping:
    """Test that user-generated content is escaped for Telegram HTML mode."""

    def test_format_item_escapes_html(self):
        """format_item in delivery.py escapes <, >, & in content."""
        with temp_db() as db:
            item_id = add_item(db, sample_queue_item("t001",
                canonical_url="https://x.com/test"))

            analysis = {
                "title": "Test <script>alert('xss')</script>",
                "summary": "Use a < b && c > d for comparison & more",
                "category": "AI & ML",
            }
            update_analysis(db, item_id, json.dumps(analysis), '["dd"]')

            item = get_item(db, item_id)
            msg = format_item(item)

            # HTML entities must be escaped
            assert "&lt;script&gt;" in msg
            assert "&amp; ML" in msg
            assert "&lt; b &amp;&amp; c &gt; d" in msg
            # Raw HTML must NOT appear
            assert "<script>" not in msg

    def test_format_delivery_message_escapes_html(self):
        """format_delivery_message in __main__.py escapes HTML in content."""
        item = {
            "id": "bk_test",
            "category": "AI & ML",
            "title": "Title with <tags> & \"quotes\"",
            "canonical_url": "https://x.com/test",
            "analysis": json.dumps({
                "analysis": "Result: a < b & c > d",
                "why_bookmarked": "User likes <code> & *markdown*",
            }),
            "buttons_json": '["dd"]',
        }

        result = format_delivery_message(item)
        text = result["text"]

        # HTML entities must be escaped
        assert "&lt;tags&gt;" in text
        assert "&amp;" in text
        assert "&lt; b" in text
        # Uses <b> for bold (HTML mode)
        assert "<b>" in text
        # Raw angle brackets from user content must NOT appear
        assert "<tags>" not in text
        assert "<code>" not in text

    def test_format_item_problematic_chars(self):
        """Test all problematic Telegram characters: < > & _ * [ `"""
        with temp_db() as db:
            item_id = add_item(db, sample_queue_item("t001",
                canonical_url="https://x.com/test"))

            analysis = {
                "title": "Chars: < > & _ * [ ` test",
                "summary": "More chars: <b>bold</b> &amp; [link](url) `code`",
                "category": "Test",
            }
            update_analysis(db, item_id, json.dumps(analysis), '["dd"]')

            item = get_item(db, item_id)
            msg = format_item(item)

            # < and > and & must be escaped
            assert "&lt;" in msg
            assert "&gt;" in msg
            assert "&amp;" in msg


# ============================================================================
# Fix 4: Unknown action rejection in build_button_rows
# ============================================================================

class TestUnknownActionRejection:
    """Test that unknown actions are skipped instead of silently truncated."""

    def test_unknown_action_skipped(self):
        """Unknown actions should be skipped, not truncated to 2 chars."""
        buttons_json = json.dumps([
            {"text": "Valid", "action": "deepdive"},
            {"text": "Garbage", "action": "totallyinvalid"},
            {"text": "Also Valid", "action": "savenotes"},
        ])

        rows = build_button_rows(buttons_json, "bk_test")

        # Should have 2 buttons (the garbage one was skipped)
        all_buttons = [btn for row in rows for btn in row]
        assert len(all_buttons) == 2
        assert all_buttons[0]["callback_data"] == "q|dd|bk_test"
        assert all_buttons[1]["callback_data"] == "q|sn|bk_test"

    def test_all_unknown_actions_returns_empty(self):
        """If all actions are unknown, return empty rows."""
        buttons_json = json.dumps([
            {"text": "Bad1", "action": "notreal"},
            {"text": "Bad2", "action": "alsobad"},
        ])

        rows = build_button_rows(buttons_json, "bk_test")
        assert rows == []

    def test_valid_actions_still_work(self):
        """Verify all known actions still produce correct codes."""
        from .delivery import ACTION_CODES
        for action, code in ACTION_CODES.items():
            buttons_json = json.dumps([{"text": "Test", "action": action}])
            rows = build_button_rows(buttons_json, "bk_test")
            assert len(rows) == 1
            assert rows[0][0]["callback_data"] == f"q|{code}|bk_test"
