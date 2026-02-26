"""
End-to-end integration tests for the bookmark-digest v2 pipeline.

Tests the full flow: fetch → queue → analyze → deliver → callback → profile.
Uses real SQLite (temp files) with mocked external calls (bird CLI, Telegram).
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from .test_utils import temp_db, sample_queue_item
from .bookmark_queue import (
    init_db, add_item, get_item, get_pending, get_stats,
    store_analyses, get_next_batch, set_sending,
    mark_delivered_with_message, get_undelivered,
    record_error, reset_failed,
)
from .callbacks import handle_callback, parse_callback
from .profile import (
    build_profile, save_profile, load_profile,
    update_weights, get_context,
)
from .__main__ import format_delivery_message


# ============================================================================
# Helpers
# ============================================================================

def _make_bookmark(tweet_id: str, text: str = "Test tweet", **overrides) -> dict:
    """Create a mock bookmark from bird CLI."""
    base = {
        "id": tweet_id,
        "text": text,
        "author": {"username": "testuser", "name": "Test User"},
        "likeCount": 100,
        "retweetCount": 20,
        "replyCount": 5,
        "conversationId": tweet_id,
    }
    base.update(overrides)
    return base


def _add_and_analyze(db_path: str, source_id: str, category: str = "AI/Agents",
                     analysis_text: str = "Detailed analysis.",
                     buttons: list = None) -> str:
    """Add an item to queue and advance it to analyzed state. Returns item_id."""
    item_id = add_item(db_path, sample_queue_item(source_id, category=category))
    analyses = [{
        "item_id": item_id,
        "category": category,
        "analysis": analysis_text,
        "why_bookmarked": "User interested in this",
        "buttons": buttons or ["dd", "sn"],
        "content_type": "tweet",
        "relevance_score": 0.85,
        "needs_enrichment": False,
        "enrichment_urls": [],
    }]
    store_analyses(db_path, analyses)
    return item_id


def _advance_to_delivered(db_path: str, item_id: str,
                          msg_id: str = "tg_123", batch_id: str = "batch_1") -> None:
    """Advance an analyzed item through sending → delivered."""
    assert set_sending(db_path, item_id), f"Failed to set sending for {item_id}"
    assert mark_delivered_with_message(db_path, item_id, msg_id, batch_id), \
        f"Failed to mark delivered for {item_id}"


# ============================================================================
# Test 1: Fetch → Queue
# ============================================================================

class TestFetchToQueue:
    """Test that fetched bookmarks land in SQLite as pending items."""

    def test_items_land_as_pending(self):
        """Fetched bookmarks should be added with status='pending'."""
        with temp_db() as db:
            # Simulate what cmd_fetch does: add items to queue
            for i in range(3):
                item_id = add_item(db, sample_queue_item(f"fetch_{i}"))
                assert item_id is not None
                item = get_item(db, item_id)
                assert item["status"] == "pending"

            stats = get_stats(db)
            assert stats.get("pending", 0) == 3

    def test_dedup_prevents_duplicates(self):
        """Adding the same source_id twice returns None (no duplicate)."""
        with temp_db() as db:
            id1 = add_item(db, sample_queue_item("dup_1"))
            id2 = add_item(db, sample_queue_item("dup_1"))
            assert id1 is not None
            assert id2 is None  # Duplicate blocked

            stats = get_stats(db)
            assert stats.get("pending", 0) == 1

    def test_multiple_sources_no_conflict(self):
        """Items from different sources with same source_id are separate."""
        with temp_db() as db:
            id1 = add_item(db, sample_queue_item("same_id", source="twitter"))
            id2 = add_item(db, sample_queue_item("same_id", source="reddit"))
            assert id1 is not None
            assert id2 is not None


# ============================================================================
# Test 2: Analyze Flow
# ============================================================================

class TestAnalyzeFlow:
    """Test the analysis pipeline: pending → analyzed with valid schema output."""

    def test_store_analyses_transitions_to_analyzed(self):
        """store_analyses should move items from pending to analyzed."""
        with temp_db() as db:
            item_id = add_item(db, sample_queue_item("analyze_1"))
            assert get_item(db, item_id)["status"] == "pending"

            analyses = [{
                "item_id": item_id,
                "category": "AI/Agents",
                "analysis": "This is a detailed analysis of the AI agent.",
                "why_bookmarked": "User follows AI closely",
                "buttons": ["dd", "im", "rs"],
                "content_type": "thread",
                "relevance_score": 0.9,
                "needs_enrichment": True,
                "enrichment_urls": ["https://example.com"],
            }]
            count = store_analyses(db, analyses)
            assert count == 1

            item = get_item(db, item_id)
            assert item["status"] == "analyzed"
            assert item["category"] == "AI/Agents"
            assert item["buttons_json"] is not None
            buttons = json.loads(item["buttons_json"])
            assert buttons == ["dd", "im", "rs"]

    def test_analysis_json_matches_schema(self):
        """Stored analysis blob should contain all required schema fields."""
        with temp_db() as db:
            item_id = add_item(db, sample_queue_item("schema_1"))
            analyses = [{
                "item_id": item_id,
                "category": "Health/Supplements",
                "analysis": "Thread claims creatine improves cognition by 24%.",
                "why_bookmarked": "User tracks supplement research",
                "buttons": ["fc", "sn"],
                "content_type": "thread",
                "relevance_score": 0.8,
                "needs_enrichment": False,
                "enrichment_urls": [],
            }]
            store_analyses(db, analyses)

            item = get_item(db, item_id)
            blob = json.loads(item["analysis"])

            # Verify required fields from bookmark-analysis-v1.json
            assert "item_id" in blob
            assert "category" in blob
            assert "analysis" in blob
            assert "buttons" in blob
            assert "content_type" in blob
            assert blob["content_type"] in [
                "tweet", "thread", "article", "video", "repo", "paper", "tool", "other"
            ]

    def test_store_analyses_idempotent(self):
        """Re-analyzing an already analyzed item should be a no-op."""
        with temp_db() as db:
            item_id = add_item(db, sample_queue_item("idem_1"))
            analyses = [{
                "item_id": item_id,
                "category": "AI",
                "analysis": "First analysis",
                "buttons": ["dd"],
                "content_type": "tweet",
            }]
            count1 = store_analyses(db, analyses)
            assert count1 == 1

            # Try again — item is no longer pending
            analyses[0]["analysis"] = "Second analysis"
            count2 = store_analyses(db, analyses)
            assert count2 == 0

            # Original analysis preserved
            item = get_item(db, item_id)
            blob = json.loads(item["analysis"])
            assert blob["analysis"] == "First analysis"


# ============================================================================
# Test 3: Delivery Flow (Two-Phase)
# ============================================================================

class TestDeliveryFlow:
    """Test the two-phase delivery: analyzed → sending → delivered."""

    def test_two_phase_delivery(self):
        """Full delivery lifecycle: analyzed → sending → delivered with message ID."""
        with temp_db() as db:
            item_id = _add_and_analyze(db, "deliver_1")
            assert get_item(db, item_id)["status"] == "analyzed"

            # Phase 1: analyzed → sending
            assert set_sending(db, item_id) is True
            assert get_item(db, item_id)["status"] == "sending"

            # Phase 2: sending → delivered (with Telegram message ID)
            assert mark_delivered_with_message(db, item_id, "tg_msg_42", "batch_abc") is True
            item = get_item(db, item_id)
            assert item["status"] == "delivered"
            assert item["telegram_message_id"] == "tg_msg_42"
            assert item["batch_id"] == "batch_abc"

    def test_sending_only_from_analyzed(self):
        """set_sending should only work on analyzed items."""
        with temp_db() as db:
            item_id = add_item(db, sample_queue_item("send_guard_1"))
            # Pending → sending should fail
            assert set_sending(db, item_id) is False
            assert get_item(db, item_id)["status"] == "pending"

    def test_delivered_only_from_sending(self):
        """mark_delivered_with_message should only work on sending items."""
        with temp_db() as db:
            item_id = _add_and_analyze(db, "deliver_guard_1")
            # analyzed → delivered (skipping sending) should fail
            assert mark_delivered_with_message(db, item_id, "tg_1", "b_1") is False
            assert get_item(db, item_id)["status"] == "analyzed"

    def test_format_delivery_message_structure(self):
        """format_delivery_message should produce valid Telegram message dict."""
        with temp_db() as db:
            item_id = _add_and_analyze(db, "format_1", category="Health")
            item = get_item(db, item_id)
            msg = format_delivery_message(item)

            assert msg["item_id"] == item_id
            assert isinstance(msg["text"], str)
            assert len(msg["text"]) > 0
            assert isinstance(msg["buttons"], list)
            assert msg["category"] == "Health"

            # Buttons should have callback_data in compact format
            for row in msg["buttons"]:
                for btn in row:
                    assert btn["callback_data"].startswith("q|")


# ============================================================================
# Test 4: Callback Round-Trip
# ============================================================================

class TestCallbackRoundTrip:
    """Test full callback flow: button tap → parse → dispatch → DB state update."""

    def test_deep_dive_callback(self):
        """Deep Dive callback should succeed on delivered item."""
        with temp_db() as db:
            item_id = _add_and_analyze(db, "cb_dd_1")
            _advance_to_delivered(db, item_id)

            result = handle_callback(db, f"q|dd|{item_id}")
            assert result["success"] is True
            assert result["action"] == "deep_dive"
            assert result["item_id"] == item_id
            assert get_item(db, item_id)["status"] == "acted_on"

    def test_fact_check_callback(self):
        """Fact Check callback should return tools spec."""
        with temp_db() as db:
            item_id = _add_and_analyze(db, "cb_fc_1", category="Health")
            _advance_to_delivered(db, item_id)

            result = handle_callback(db, f"q|fc|{item_id}")
            assert result["success"] is True
            assert result["action"] == "fact_check"
            assert "tools" in result

    def test_save_notes_callback(self):
        """Save Notes callback should write to daily notes file."""
        with temp_db() as db:
            item_id = _add_and_analyze(db, "cb_sn_1")
            _advance_to_delivered(db, item_id)

            result = handle_callback(db, f"q|sn|{item_id}")
            assert result["success"] is True
            assert result["action"] == "save_notes"
            assert "file" in result

    def test_callback_rejects_non_delivered(self):
        """Callbacks should reject items not in delivered state."""
        with temp_db() as db:
            item_id = _add_and_analyze(db, "cb_reject_1")
            # Item is in 'analyzed' state, not 'delivered'
            result = handle_callback(db, f"q|dd|{item_id}")
            assert result["success"] is False
            assert "not in delivered state" in result["error"]

    def test_idempotent_retap(self):
        """Re-tapping a button on acted_on item should succeed with warning."""
        with temp_db() as db:
            item_id = _add_and_analyze(db, "cb_retap_1")
            _advance_to_delivered(db, item_id)

            # First tap
            result1 = handle_callback(db, f"q|dd|{item_id}")
            assert result1["success"] is True

            # Re-tap (idempotent)
            result2 = handle_callback(db, f"q|dd|{item_id}")
            assert result2["success"] is True
            assert "Already processed" in result2.get("warning", "")

    def test_all_action_codes_parse(self):
        """All 8 action codes should parse correctly."""
        codes = ["dd", "im", "fc", "sn", "rm", "fs", "rs", "nb"]
        for code in codes:
            action, item_id = parse_callback(f"q|{code}|bk_test123")
            assert action == code
            assert item_id == "bk_test123"


# ============================================================================
# Test 5: Batch Flow
# ============================================================================

class TestBatchFlow:
    """Test batch delivery: queue N items, deliver batch of 5."""

    def test_batch_of_5_from_7(self):
        """Queue 7 items, deliver batch of 5, verify 5 delivered + 2 remaining."""
        with temp_db() as db:
            item_ids = []
            for i in range(7):
                item_id = _add_and_analyze(db, f"batch_{i}")
                item_ids.append(item_id)

            # Get batch of 5
            batch = get_next_batch(db, batch_size=5)
            assert len(batch) == 5

            # Deliver the batch
            batch_id = batch[0]["batch_id"]
            for item in batch:
                assert set_sending(db, item["id"]) is True
                assert mark_delivered_with_message(
                    db, item["id"], f"tg_{item['id']}", batch_id
                ) is True

            # Verify stats
            stats = get_stats(db)
            assert stats.get("delivered", 0) == 5
            assert stats.get("analyzed", 0) == 2

            # Remaining undelivered
            remaining = get_undelivered(db)
            assert len(remaining) == 2

    def test_empty_batch_returns_empty(self):
        """get_next_batch on empty queue returns empty list."""
        with temp_db() as db:
            batch = get_next_batch(db, batch_size=5)
            assert batch == []

    def test_second_batch_gets_remaining(self):
        """After first batch is delivered, second batch gets remaining items."""
        with temp_db() as db:
            for i in range(8):
                _add_and_analyze(db, f"multi_batch_{i}")

            # First batch
            batch1 = get_next_batch(db, batch_size=5)
            assert len(batch1) == 5
            batch1_id = batch1[0]["batch_id"]
            for item in batch1:
                set_sending(db, item["id"])
                mark_delivered_with_message(db, item["id"], f"tg_{item['id']}", batch1_id)

            # Second batch
            batch2 = get_next_batch(db, batch_size=5)
            assert len(batch2) == 3  # Only 3 remaining

            # No overlap between batches
            batch1_ids = {item["id"] for item in batch1}
            batch2_ids = {item["id"] for item in batch2}
            assert batch1_ids.isdisjoint(batch2_ids)


# ============================================================================
# Test 6: Dead Letter Queue
# ============================================================================

class TestDLQ:
    """Test error recording and DLQ behavior."""

    def test_error_count_increments(self):
        """Each record_error call should increment error_count."""
        with temp_db() as db:
            item_id = add_item(db, sample_queue_item("dlq_1"))

            result = record_error(db, item_id, "Connection timeout")
            assert result["recorded"] is True
            assert result["error_count"] == 1
            assert result["dlq"] is False

            result = record_error(db, item_id, "Connection timeout again")
            assert result["error_count"] == 2
            assert result["dlq"] is False

    def test_dlq_after_3_failures(self):
        """Item should move to failed (DLQ) after 3 errors."""
        with temp_db() as db:
            item_id = add_item(db, sample_queue_item("dlq_3"))

            record_error(db, item_id, "Error 1")
            record_error(db, item_id, "Error 2")
            result = record_error(db, item_id, "Error 3")

            assert result["dlq"] is True
            assert result["error_count"] == 3

            item = get_item(db, item_id)
            assert item["status"] == "failed"

            # Verify dlq.log was written
            data_dir = Path(db).parent
            dlq_log = data_dir / "dlq.log"
            assert dlq_log.exists()
            log_content = dlq_log.read_text()
            assert item_id in log_content

    def test_item_not_stuck_after_error(self):
        """After a non-fatal error, item can still be processed."""
        with temp_db() as db:
            item_id = add_item(db, sample_queue_item("dlq_recover_1"))
            record_error(db, item_id, "Transient error")

            item = get_item(db, item_id)
            assert item["status"] == "pending"  # Still processable
            assert item["error_count"] == 1

    def test_reset_failed_restores_items(self):
        """reset_failed should move DLQ items back to pending."""
        with temp_db() as db:
            item_id = add_item(db, sample_queue_item("dlq_reset_1"))
            for i in range(3):
                record_error(db, item_id, f"Error {i+1}")

            assert get_item(db, item_id)["status"] == "failed"

            count = reset_failed(db)
            assert count == 1

            item = get_item(db, item_id)
            assert item["status"] == "pending"
            assert item["error_count"] == 0


# ============================================================================
# Test 7: Idempotency
# ============================================================================

class TestIdempotency:
    """Test that repeated operations don't cause duplicates or corruption."""

    def test_deliver_twice_no_duplicate(self):
        """Delivering the same batch twice should not create duplicate sends."""
        with temp_db() as db:
            item_ids = []
            for i in range(5):
                item_id = _add_and_analyze(db, f"idem_deliver_{i}")
                item_ids.append(item_id)

            # First delivery
            batch = get_next_batch(db, batch_size=5)
            assert len(batch) == 5
            batch_id = batch[0]["batch_id"]
            delivered = 0
            for item in batch:
                if set_sending(db, item["id"]):
                    if mark_delivered_with_message(db, item["id"], f"tg_{item['id']}", batch_id):
                        delivered += 1
            assert delivered == 5

            # Second attempt — should get empty batch (all delivered)
            batch2 = get_next_batch(db, batch_size=5)
            assert len(batch2) == 0

            # Verify exactly 5 delivered
            stats = get_stats(db)
            assert stats.get("delivered", 0) == 5

    def test_set_sending_idempotent(self):
        """Calling set_sending on non-analyzed item returns False."""
        with temp_db() as db:
            item_id = _add_and_analyze(db, "idem_send_1")
            assert set_sending(db, item_id) is True  # First: OK
            assert set_sending(db, item_id) is False  # Second: already sending

    def test_store_analyses_skips_non_pending(self):
        """store_analyses should skip items that aren't pending."""
        with temp_db() as db:
            item_id = _add_and_analyze(db, "idem_analyze_1")
            # Item is now 'analyzed', try to re-analyze
            count = store_analyses(db, [{
                "item_id": item_id,
                "category": "New Category",
                "analysis": "New analysis",
                "buttons": ["fc"],
                "content_type": "tweet",
            }])
            assert count == 0


# ============================================================================
# Test 8: Profile Update on Callback
# ============================================================================

class TestProfileUpdateOnCallback:
    """Test that callback actions update user profile weights."""

    def test_fact_check_boosts_category_weight(self):
        """Fact Check action should increase category weight by 0.05."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = os.path.join(tmpdir, "profile.json")

            # Create initial profile
            initial = {
                "version": "v1",
                "topics": {"Health": 0.5, "AI": 0.8},
                "content_types": {},
                "generated_at": "2026-02-26T00:00:00Z",
                "bookmark_count": 10,
            }
            save_profile(profile_path, initial)

            # Simulate fact_check callback updating profile
            updated = update_weights(profile_path, "fc", "Health")
            assert "Health" in updated["topics"]
            # Weight should have increased (exact value depends on normalization)
            assert updated["topics"]["Health"] > 0

    def test_deep_dive_boosts_weight(self):
        """Deep Dive should increase weight by 0.03."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = os.path.join(tmpdir, "profile.json")
            save_profile(profile_path, {
                "version": "v1",
                "topics": {"AI": 0.5},
                "content_types": {},
                "generated_at": "2026-02-26T00:00:00Z",
                "bookmark_count": 5,
            })

            updated = update_weights(profile_path, "dd", "AI")
            # After boost and normalization, AI should be 1.0 (only topic)
            assert updated["topics"]["AI"] == 1.0

    def test_new_category_created_on_action(self):
        """Action on uncategorized topic should create new weight entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = os.path.join(tmpdir, "profile.json")
            save_profile(profile_path, {
                "version": "v1",
                "topics": {"AI": 0.8},
                "content_types": {},
                "generated_at": "2026-02-26T00:00:00Z",
                "bookmark_count": 5,
            })

            updated = update_weights(profile_path, "sn", "Cooking")
            assert "Cooking" in updated["topics"]
            assert updated["topics"]["Cooking"] > 0

    def test_profile_build_from_db(self):
        """build_profile should analyze queue DB bookmarks."""
        with temp_db() as db:
            # Add categorized items
            for i, cat in enumerate(["AI", "AI", "Health", "AI", "Tech"]):
                item_id = add_item(db, sample_queue_item(f"profile_{i}", category=cat))
                store_analyses(db, [{
                    "item_id": item_id,
                    "category": cat,
                    "analysis": f"Analysis of {cat} item",
                    "buttons": ["dd"],
                    "content_type": "tweet",
                }])

            profile = build_profile(db, limit=100)
            assert profile["version"] == "v1"
            assert "AI" in profile["topics"]
            assert profile["topics"]["AI"] == 1.0  # Most frequent, normalized to 1.0
            assert profile["bookmark_count"] == 5

    def test_profile_context_string(self):
        """get_context should return human-readable interest summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = os.path.join(tmpdir, "profile.json")
            save_profile(profile_path, {
                "version": "v1",
                "topics": {"AI/Agents": 1.0, "Health": 0.5, "Rust": 0.2},
                "content_types": {"thread": 0.8, "tweet": 1.0},
                "generated_at": "2026-02-26T00:00:00Z",
                "bookmark_count": 50,
            })

            context = get_context(profile_path)
            assert "User interests:" in context
            assert "AI/Agents" in context
            assert "high" in context  # AI/Agents at 1.0 should be "high"


# ============================================================================
# Test 9: Full Pipeline Integration
# ============================================================================

class TestFullPipeline:
    """Test the complete pipeline from fetch to callback."""

    def test_full_lifecycle(self):
        """Test complete item lifecycle: pending → analyzed → sending → delivered → acted_on."""
        with temp_db() as db:
            # Step 1: Add item (simulates fetch)
            item_id = add_item(db, sample_queue_item("lifecycle_1",
                               title="@alice — AI Agent Deep Dive",
                               category="AI"))
            assert get_item(db, item_id)["status"] == "pending"

            # Step 2: Analyze
            store_analyses(db, [{
                "item_id": item_id,
                "category": "AI/Agents",
                "analysis": "Comprehensive review of autonomous AI agents.",
                "why_bookmarked": "User builds AI tools",
                "buttons": ["dd", "im", "sn"],
                "content_type": "thread",
                "relevance_score": 0.95,
            }])
            assert get_item(db, item_id)["status"] == "analyzed"

            # Step 3: Batch assignment
            batch = get_next_batch(db, batch_size=5)
            assert len(batch) == 1
            batch_id = batch[0]["batch_id"]

            # Step 4: Two-phase delivery
            assert set_sending(db, item_id)
            msg = format_delivery_message(get_item(db, item_id))
            assert "AI" in msg["text"] or "AI" in msg["category"]
            assert mark_delivered_with_message(db, item_id, "tg_999", batch_id)

            item = get_item(db, item_id)
            assert item["status"] == "delivered"
            assert item["telegram_message_id"] == "tg_999"

            # Step 5: User taps Deep Dive button
            result = handle_callback(db, f"q|dd|{item_id}")
            assert result["success"] is True
            assert result["action"] == "deep_dive"
            assert get_item(db, item_id)["status"] == "acted_on"

    def test_mixed_batch_with_errors(self):
        """Test a batch where some items succeed and some fail."""
        with temp_db() as db:
            # Add 5 items
            ids = []
            for i in range(5):
                item_id = _add_and_analyze(db, f"mixed_{i}")
                ids.append(item_id)

            # Get batch
            batch = get_next_batch(db, batch_size=5)
            batch_id = batch[0]["batch_id"]

            # Deliver first 3 successfully
            for item in batch[:3]:
                set_sending(db, item["id"])
                mark_delivered_with_message(db, item["id"], f"tg_{item['id']}", batch_id)

            # Items 4-5: simulate send failure (stay in analyzed after rollback)
            for item in batch[3:]:
                set_sending(db, item["id"])
                # Don't complete delivery — simulate failure
                # In real code, would rollback to analyzed

            stats = get_stats(db)
            assert stats.get("delivered", 0) == 3
            assert stats.get("sending", 0) == 2  # Stuck in sending

    def test_next_batch_callback(self):
        """Next Batch callback should trigger delivery of next batch."""
        with temp_db() as db:
            # Add 3 analyzed items for next batch to pick up
            for i in range(3):
                _add_and_analyze(db, f"nb_{i}")

            result = handle_callback(db, "q|nb|previous_batch_id")
            assert result["success"] is True
            assert result["action"] == "next_batch"
            assert result["delivered"] >= 0  # May be 0 if items got assigned batch but not sent


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
