"""
Tests for Session 5: Callback Hardening + Delivery Polish.

Covers:
- Compact callback parsing (new q|code|id + legacy queue_action_id)
- Invalid callback data → graceful error
- Action dispatch for all 8 codes
- Status validation: only delivered items accept callbacks
- Error recording + DLQ (dead letter queue)
- Message truncation at 4000 chars
- Telegram rate limit constant
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from .test_utils import temp_db, sample_queue_item
from .bookmark_queue import (
    add_item, get_item, init_db,
    store_analyses, set_sending, mark_delivered_with_message,
    mark_acted_on, record_error, DLQ_MAX_ERRORS,
)
from .callbacks import (
    parse_callback, handle_callback,
    VALID_CODES, ACTION_CODES, CODE_TO_ACTION,
)
from .__main__ import (
    format_delivery_message, TELEGRAM_RATE_LIMIT, MAX_MESSAGE_LENGTH,
)


# ============================================================================
# Helpers
# ============================================================================

def _make_delivered_item(db_path, source_id="t_cb_001", category="AI/Agents",
                         title="Test bookmark", url="https://x.com/test"):
    """Create an item and advance it through pending → analyzed → sending → delivered."""
    item_id = add_item(db_path, sample_queue_item(
        source_id, title=title, canonical_url=url,
    ))
    # Advance to analyzed
    analyses = [{
        "item_id": item_id,
        "category": category,
        "analysis": "Interesting content about AI agents.",
        "why_bookmarked": "User follows AI tools",
        "buttons": ["dd", "im", "fc"],
        "content_type": "tweet",
        "relevance_score": 0.9,
        "needs_enrichment": False,
        "enrichment_urls": [],
    }]
    store_analyses(db_path, analyses)
    # Advance to sending
    set_sending(db_path, item_id)
    # Advance to delivered
    mark_delivered_with_message(db_path, item_id, "tg_msg_123", "batch_001")
    return item_id


# ============================================================================
# 1. Compact Callback Parser Tests
# ============================================================================

class TestParseCallback:
    """Tests for parse_callback function."""

    def test_compact_format_deep_dive(self):
        code, item_id = parse_callback("q|dd|bk_abc123")
        assert code == "dd"
        assert item_id == "bk_abc123"

    def test_compact_format_implement(self):
        code, item_id = parse_callback("q|im|bk_def456")
        assert code == "im"
        assert item_id == "bk_def456"

    def test_compact_format_fact_check(self):
        code, item_id = parse_callback("q|fc|bk_ghi789")
        assert code == "fc"
        assert item_id == "bk_ghi789"

    def test_compact_format_save_notes(self):
        code, item_id = parse_callback("q|sn|bk_jkl012")
        assert code == "sn"
        assert item_id == "bk_jkl012"

    def test_compact_format_remind(self):
        code, item_id = parse_callback("q|rm|bk_mno345")
        assert code == "rm"
        assert item_id == "bk_mno345"

    def test_compact_format_full_summary(self):
        code, item_id = parse_callback("q|fs|bk_pqr678")
        assert code == "fs"
        assert item_id == "bk_pqr678"

    def test_compact_format_read_source(self):
        code, item_id = parse_callback("q|rs|bk_stu901")
        assert code == "rs"
        assert item_id == "bk_stu901"

    def test_compact_format_next_batch(self):
        code, batch_id = parse_callback("q|nb|batch_abc123")
        assert code == "nb"
        assert batch_id == "batch_abc123"

    def test_legacy_format_deepdive(self):
        code, item_id = parse_callback("queue_deepdive_bk_abc123")
        assert code == "dd"
        assert item_id == "bk_abc123"

    def test_legacy_format_factcheck(self):
        code, item_id = parse_callback("queue_factcheck_bk_def456")
        assert code == "fc"
        assert item_id == "bk_def456"

    def test_legacy_format_savenotes(self):
        code, item_id = parse_callback("queue_savenotes_bk_ghi789")
        assert code == "sn"
        assert item_id == "bk_ghi789"

    def test_legacy_format_next_batch(self):
        code, batch_id = parse_callback("queue_next_batch_batch_abc123")
        assert code == "nb"
        assert batch_id == "batch_abc123"

    def test_returns_tuple(self):
        result = parse_callback("q|dd|bk_abc123")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_all_valid_codes_parseable(self):
        for code in VALID_CODES:
            result = parse_callback(f"q|{code}|bk_test")
            assert result[0] == code

    def test_invalid_empty_string(self):
        with pytest.raises(ValueError, match="Empty"):
            parse_callback("")

    def test_invalid_none(self):
        with pytest.raises(ValueError, match="Empty"):
            parse_callback(None)

    def test_invalid_unknown_code(self):
        with pytest.raises(ValueError, match="Unknown action code"):
            parse_callback("q|zz|bk_abc123")

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid callback format"):
            parse_callback("totally_invalid_data")

    def test_invalid_legacy_action(self):
        with pytest.raises(ValueError, match="Unknown legacy action"):
            parse_callback("queue_bogusaction_bk_abc123")


# ============================================================================
# 2. Action Dispatch Tests
# ============================================================================

class TestActionDispatch:
    """Tests for handle_callback action dispatch."""

    def test_deep_dive(self):
        with temp_db() as db_path:
            item_id = _make_delivered_item(db_path, "t_dd_001")
            result = handle_callback(db_path, f"q|dd|{item_id}")
            assert result["success"] is True
            assert result["action"] == "deep_dive"
            assert result["agent"] == "research"
            assert result["item_id"] == item_id

    def test_implement(self):
        with temp_db() as db_path:
            item_id = _make_delivered_item(db_path, "t_im_001")
            result = handle_callback(db_path, f"q|im|{item_id}")
            assert result["success"] is True
            assert result["action"] == "implement"
            assert result["agent"] == "coding"
            assert result["item_id"] == item_id
            assert "context" in result

    def test_fact_check(self):
        with temp_db() as db_path:
            item_id = _make_delivered_item(db_path, "t_fc_001")
            result = handle_callback(db_path, f"q|fc|{item_id}")
            assert result["success"] is True
            assert result["action"] == "fact_check"
            assert "web_search" in result["tools"]
            assert "llm-task" in result["tools"]
            assert result["query"]  # non-empty

    def test_save_notes(self):
        with temp_db() as db_path:
            item_id = _make_delivered_item(db_path, "t_sn_001")
            result = handle_callback(db_path, f"q|sn|{item_id}")
            assert result["success"] is True
            assert result["action"] == "save_notes"
            assert result["file"]  # file path returned
            # Verify note was actually written
            note_file = Path(result["file"])
            assert note_file.exists()
            content = note_file.read_text()
            assert item_id in content
            assert "Test bookmark" in content or "Test item" in content

    def test_save_notes_idempotent(self):
        """Second save_notes call for same item should not duplicate."""
        with temp_db() as db_path:
            item_id = _make_delivered_item(db_path, "t_sn_idem")
            # First call (dispatches normally)
            result1 = handle_callback(db_path, f"q|sn|{item_id}")
            assert result1["success"] is True
            # Item is now acted_on, so second call returns warning
            result2 = handle_callback(db_path, f"q|sn|{item_id}")
            assert result2["success"] is True
            assert "Already processed" in result2.get("warning", "")

    def test_remind(self):
        with temp_db() as db_path:
            item_id = _make_delivered_item(db_path, "t_rm_001")
            result = handle_callback(db_path, f"q|rm|{item_id}")
            assert result["success"] is True
            assert result["action"] == "remind"
            assert result["schedule"]  # has schedule

    def test_full_summary(self):
        with temp_db() as db_path:
            item_id = _make_delivered_item(db_path, "t_fs_001")
            result = handle_callback(db_path, f"q|fs|{item_id}")
            assert result["success"] is True
            assert result["action"] == "full_summary"
            assert result["tool"] == "fabric"

    def test_read_source(self):
        with temp_db() as db_path:
            item_id = _make_delivered_item(db_path, "t_rs_001")
            result = handle_callback(db_path, f"q|rs|{item_id}")
            assert result["success"] is True
            assert result["action"] == "read_source"
            assert result["tool"] == "web_fetch"
            assert result["url"]  # non-empty

    def test_next_batch_empty(self):
        """Next batch with no analyzed items returns empty."""
        with temp_db() as db_path:
            result = handle_callback(db_path, "q|nb|batch_old123")
            assert result["success"] is True
            assert result["action"] == "next_batch"
            assert result["delivered"] == 0
            assert result["messages"] == []

    def test_next_batch_with_items(self):
        """Next batch with analyzed items returns formatted messages."""
        with temp_db() as db_path:
            # Create analyzed items
            for i in range(3):
                item_id = add_item(db_path, sample_queue_item(f"t_nb_{i}"))
                store_analyses(db_path, [{
                    "item_id": item_id,
                    "category": "AI",
                    "analysis": f"Analysis {i}",
                    "buttons": ["dd"],
                    "content_type": "tweet",
                }])

            result = handle_callback(db_path, "q|nb|batch_trigger")
            assert result["success"] is True
            assert result["action"] == "next_batch"
            assert result["delivered"] == 3
            assert len(result["messages"]) == 3


# ============================================================================
# 3. Status Validation Tests
# ============================================================================

class TestStatusValidation:
    """Tests that callbacks are only accepted for items in 'delivered' state."""

    def test_reject_pending_item(self):
        with temp_db() as db_path:
            item_id = add_item(db_path, sample_queue_item("t_pending"))
            result = handle_callback(db_path, f"q|dd|{item_id}")
            assert result["success"] is False
            assert "not in delivered state" in result["error"]

    def test_reject_analyzed_item(self):
        with temp_db() as db_path:
            item_id = add_item(db_path, sample_queue_item("t_analyzed"))
            store_analyses(db_path, [{
                "item_id": item_id,
                "category": "AI",
                "analysis": "Test",
                "buttons": ["dd"],
                "content_type": "tweet",
            }])
            result = handle_callback(db_path, f"q|dd|{item_id}")
            assert result["success"] is False
            assert "not in delivered state" in result["error"]

    def test_reject_sending_item(self):
        with temp_db() as db_path:
            item_id = add_item(db_path, sample_queue_item("t_sending"))
            store_analyses(db_path, [{
                "item_id": item_id,
                "category": "AI",
                "analysis": "Test",
                "buttons": ["dd"],
                "content_type": "tweet",
            }])
            set_sending(db_path, item_id)
            result = handle_callback(db_path, f"q|dd|{item_id}")
            assert result["success"] is False
            assert "not in delivered state" in result["error"]

    def test_accept_delivered_item(self):
        with temp_db() as db_path:
            item_id = _make_delivered_item(db_path, "t_delivered")
            result = handle_callback(db_path, f"q|dd|{item_id}")
            assert result["success"] is True

    def test_idempotent_acted_on(self):
        """Second tap on already-acted-on item returns warning, not error."""
        with temp_db() as db_path:
            item_id = _make_delivered_item(db_path, "t_acted")
            handle_callback(db_path, f"q|dd|{item_id}")
            # Second tap
            result = handle_callback(db_path, f"q|dd|{item_id}")
            assert result["success"] is True
            assert "Already processed" in result.get("warning", "")

    def test_item_not_found(self):
        with temp_db() as db_path:
            result = handle_callback(db_path, "q|dd|bk_nonexistent")
            assert result["success"] is False
            assert "not found" in result["error"].lower()

    def test_marks_acted_on_after_dispatch(self):
        """Verify item transitions to acted_on after callback."""
        with temp_db() as db_path:
            item_id = _make_delivered_item(db_path, "t_transition")
            handle_callback(db_path, f"q|dd|{item_id}")
            item = get_item(db_path, item_id)
            assert item["status"] == "acted_on"


# ============================================================================
# 4. Error Recording + DLQ Tests
# ============================================================================

class TestErrorRecording:
    """Tests for record_error and dead letter queue."""

    def test_record_first_error(self):
        with temp_db() as db_path:
            item_id = add_item(db_path, sample_queue_item("t_err_001"))
            result = record_error(db_path, item_id, "Test error 1")
            assert result["recorded"] is True
            assert result["error_count"] == 1
            assert result["dlq"] is False

            item = get_item(db_path, item_id)
            assert item["error_count"] == 1
            assert item["last_error"] == "Test error 1"
            assert item["status"] == "pending"  # Still pending after 1 error

    def test_record_increments_count(self):
        with temp_db() as db_path:
            item_id = add_item(db_path, sample_queue_item("t_err_002"))
            record_error(db_path, item_id, "Error 1")
            result = record_error(db_path, item_id, "Error 2")
            assert result["error_count"] == 2
            assert result["dlq"] is False

            item = get_item(db_path, item_id)
            assert item["error_count"] == 2
            assert item["last_error"] == "Error 2"

    def test_dlq_at_three_errors(self):
        with temp_db() as db_path:
            item_id = add_item(db_path, sample_queue_item("t_err_dlq"))
            record_error(db_path, item_id, "Error 1")
            record_error(db_path, item_id, "Error 2")
            result = record_error(db_path, item_id, "Error 3 - fatal")
            assert result["error_count"] == 3
            assert result["dlq"] is True

            item = get_item(db_path, item_id)
            assert item["status"] == "failed"
            assert item["error_count"] == 3

    def test_dlq_log_written(self):
        with temp_db() as db_path:
            item_id = add_item(db_path, sample_queue_item("t_err_log"))
            for i in range(DLQ_MAX_ERRORS):
                record_error(db_path, item_id, f"Error {i+1}")

            # Check dlq.log exists and contains the item
            dlq_path = Path(db_path).parent / "dlq.log"
            assert dlq_path.exists()
            content = dlq_path.read_text()
            assert item_id in content
            assert f"errors={DLQ_MAX_ERRORS}" in content

    def test_record_error_nonexistent_item(self):
        with temp_db() as db_path:
            result = record_error(db_path, "bk_doesnotexist", "Error")
            assert result["recorded"] is False

    def test_dlq_max_errors_constant(self):
        assert DLQ_MAX_ERRORS == 3


# ============================================================================
# 5. Message Truncation Tests
# ============================================================================

class TestMessageTruncation:
    """Tests for Telegram message length handling."""

    def test_short_message_not_truncated(self):
        item = {
            "id": "bk_short",
            "category": "AI",
            "title": "Short title",
            "canonical_url": "https://x.com/short",
            "analysis": json.dumps({"analysis": "Brief."}),
            "buttons_json": json.dumps(["dd"]),
        }
        msg = format_delivery_message(item)
        assert len(msg["text"]) < MAX_MESSAGE_LENGTH
        assert "..." not in msg["text"]

    def test_long_message_truncated(self):
        long_analysis = "A" * 5000
        item = {
            "id": "bk_long",
            "category": "AI",
            "title": "Long content bookmark",
            "canonical_url": "https://x.com/long",
            "analysis": json.dumps({"analysis": long_analysis}),
            "buttons_json": json.dumps(["dd", "rs"]),
        }
        msg = format_delivery_message(item)
        assert len(msg["text"]) <= MAX_MESSAGE_LENGTH
        assert msg["text"].endswith("...")

    def test_max_message_length_constant(self):
        assert MAX_MESSAGE_LENGTH == 4000

    def test_telegram_rate_limit_constant(self):
        assert TELEGRAM_RATE_LIMIT == 1.0


# ============================================================================
# 6. Callback Code/Action Mapping Tests
# ============================================================================

class TestCodeMappings:
    """Tests for action code constants and mappings."""

    def test_valid_codes_has_all_eight(self):
        assert VALID_CODES == {"dd", "im", "fc", "sn", "rm", "fs", "rs", "nb"}

    def test_action_codes_map_to_valid_codes(self):
        for action, code in ACTION_CODES.items():
            assert code in VALID_CODES

    def test_code_to_action_is_inverse(self):
        for action, code in ACTION_CODES.items():
            assert CODE_TO_ACTION[code] == action

    def test_all_non_nb_codes_have_handlers(self):
        """Every button code except nb should have a dispatch handler."""
        from .callbacks import _ACTION_HANDLERS
        for code in VALID_CODES - {"nb"}:
            assert code in _ACTION_HANDLERS, f"Missing handler for code: {code}"
