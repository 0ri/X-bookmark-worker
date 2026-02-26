#!/usr/bin/env python3
"""Tests for bookmark_queue.py — SQLite CRUD operations."""

from bookmark_digest.test_utils import temp_db, sample_queue_item
from bookmark_digest.bookmark_queue import (
    add_item, get_pending, get_queued, update_status,
    claim_item, get_item, get_stats, archive_completed,
    get_undelivered, get_next_batch, mark_delivered, update_analysis, mark_acted_on,
)


def test_add_and_get():
    """Test adding items and retrieving them."""
    with temp_db() as db:
        # Add items from different sources
        tw1 = add_item(db, sample_queue_item("tw_001", source="twitter"))
        tw2 = add_item(db, sample_queue_item("tw_002", source="twitter"))
        rd1 = add_item(db, sample_queue_item("rd_001", source="reddit"))
        
        assert tw1 and tw2 and rd1, "All adds should succeed"
        assert tw1.startswith("bk_"), f"Twitter items get bk_ prefix: {tw1}"
        assert rd1.startswith("rd_"), f"Reddit items get rd_ prefix: {rd1}"
        
        # Duplicate should fail
        dup = add_item(db, sample_queue_item("tw_001", source="twitter"))
        assert dup is None, "Duplicate should return None"
        
        # Get pending
        pending = get_pending(db, limit=10)
        assert len(pending) == 3
        print("✓ add_item + get_pending")


def test_claim_and_update():
    """Test claiming items and updating status."""
    with temp_db() as db:
        item_id = add_item(db, sample_queue_item("t001"))
        
        # Claim item
        assert claim_item(db, item_id), "First claim should succeed"
        item = get_item(db, item_id)
        assert item["status"] == "processing"
        assert item["attempt_count"] == 1
        
        # Can't claim again
        assert not claim_item(db, item_id), "Double claim should fail"
        
        # Update to completed
        update_status(db, item_id, "completed", result="Done")
        item = get_item(db, item_id)
        assert item["status"] == "completed"
        assert item["result"] == "Done"
        print("✓ claim_item + update_status")


def test_queued_items():
    """Test get_queued for items marked for overnight processing."""
    with temp_db() as db:
        id1 = add_item(db, sample_queue_item("t001"))
        id2 = add_item(db, sample_queue_item("t002"))
        
        update_status(db, id1, "queued", action="deepdive")
        
        queued = get_queued(db)
        assert len(queued) == 1
        assert queued[0]["id"] == id1
        print("✓ get_queued")


def test_stats():
    """Test get_stats returns correct counts."""
    with temp_db() as db:
        id1 = add_item(db, sample_queue_item("t001"))
        id2 = add_item(db, sample_queue_item("t002"))
        id3 = add_item(db, sample_queue_item("t003"))
        
        update_status(db, id1, "completed")
        update_status(db, id2, "queued")
        
        stats = get_stats(db)
        assert stats["completed"] == 1
        assert stats["queued"] == 1
        assert stats["pending"] == 1
        print("✓ get_stats")


def test_archive():
    """Test archive_completed removes old completed items."""
    with temp_db() as db:
        item_id = add_item(db, sample_queue_item("t001"))
        update_status(db, item_id, "completed")
        
        archived = archive_completed(db, older_than_days=0)
        assert archived == 1
        assert get_item(db, item_id) is None
        print("✓ archive_completed")


def test_get_item_missing():
    """Test get_item returns None for nonexistent items."""
    with temp_db() as db:
        assert get_item(db, "nonexistent") is None
        print("✓ get_item returns None for missing")


def test_full_lifecycle():
    """Test complete lifecycle: add → analyze → batch → deliver → act → complete."""
    with temp_db() as db:
        # Add item
        item_id = add_item(db, sample_queue_item("t001"))
        assert item_id is not None
        
        # Update with analysis
        analysis = '{"summary": "Test analysis", "category": "tech"}'
        buttons = '[{"text": "Deep Dive", "action": "dd"}]'
        assert update_analysis(db, item_id, analysis, buttons)
        
        item = get_item(db, item_id)
        assert item["status"] == "analyzed"
        assert item["analysis"] == analysis
        assert item["buttons_json"] == buttons
        
        # Get next batch
        batch = get_next_batch(db, batch_size=5)
        assert len(batch) == 1
        assert batch[0]["id"] == item_id
        assert batch[0]["batch_id"] is not None
        
        # Mark delivered
        assert mark_delivered(db, item_id, "msg_123")
        item = get_item(db, item_id)
        assert item["status"] == "delivered"
        assert item["telegram_message_id"] == "msg_123"
        
        # Mark acted on
        assert mark_acted_on(db, item_id)
        item = get_item(db, item_id)
        assert item["status"] == "acted_on"
        
        # Complete (using new update_status with validation)
        from bookmark_digest.bookmark_queue import update_status as new_update_status
        assert new_update_status(db, item_id, "completed")
        item = get_item(db, item_id)
        assert item["status"] == "completed"
        
        print("✓ full_lifecycle")


def test_get_next_batch_limits():
    """Test get_next_batch with different sizes."""
    with temp_db() as db:
        # Add 10 analyzed items
        item_ids = []
        for i in range(10):
            item_id = add_item(db, sample_queue_item(f"t{i:03d}"))
            update_analysis(db, item_id, "{}", "[]")
            item_ids.append(item_id)
        
        # Get batch of 5
        batch1 = get_next_batch(db, batch_size=5)
        assert len(batch1) == 5
        
        # All should have same batch_id
        batch_ids = {item["batch_id"] for item in batch1}
        assert len(batch_ids) == 1, "All items should have same batch_id"
        
        # Get next batch of 5
        batch2 = get_next_batch(db, batch_size=5)
        assert len(batch2) == 5
        
        # Should have different batch_id
        assert batch2[0]["batch_id"] != batch1[0]["batch_id"]
        
        # No more items
        batch3 = get_next_batch(db, batch_size=5)
        assert len(batch3) == 0
        
        print("✓ get_next_batch limits")


def test_get_next_batch_partial():
    """Test get_next_batch returns what's available when fewer than requested."""
    with temp_db() as db:
        # Add only 3 analyzed items
        for i in range(3):
            item_id = add_item(db, sample_queue_item(f"t{i:03d}"))
            update_analysis(db, item_id, "{}", "[]")
        
        # Request 5, should get 3
        batch = get_next_batch(db, batch_size=5)
        assert len(batch) == 3
        
        print("✓ get_next_batch partial")


def test_status_transition_validation():
    """Test update_status validates transitions."""
    with temp_db() as db:
        item_id = add_item(db, sample_queue_item("t001"))
        
        # Valid: pending → analyzed
        from bookmark_digest.bookmark_queue import update_status as new_update_status
        assert new_update_status(db, item_id, "analyzed")
        
        # Valid: analyzed → delivered
        assert new_update_status(db, item_id, "delivered")
        
        # Invalid: delivered → analyzed (can't go backward)
        try:
            new_update_status(db, item_id, "analyzed")
            assert False, "Should raise ValueError for invalid transition"
        except ValueError as e:
            assert "Invalid status transition" in str(e)
        
        # Valid: delivered → acted_on
        assert new_update_status(db, item_id, "acted_on")
        
        # Valid: acted_on → completed
        assert new_update_status(db, item_id, "completed")
        
        # Invalid: completed → pending
        try:
            new_update_status(db, item_id, "pending")
            assert False, "Should raise ValueError"
        except ValueError as e:
            assert "Invalid status transition" in str(e)
        
        print("✓ status transition validation")


def test_status_transition_archived_always_allowed():
    """Test that any status can transition to archived."""
    with temp_db() as db:
        from bookmark_digest.bookmark_queue import update_status as new_update_status
        
        # Test from various states
        for start_status in ["pending", "analyzed", "delivered", "acted_on", "completed"]:
            item_id = add_item(db, sample_queue_item(f"t_{start_status}"))
            
            # Advance to start_status
            if start_status == "analyzed":
                new_update_status(db, item_id, "analyzed")
            elif start_status == "delivered":
                new_update_status(db, item_id, "analyzed")
                new_update_status(db, item_id, "delivered")
            elif start_status == "acted_on":
                new_update_status(db, item_id, "analyzed")
                new_update_status(db, item_id, "delivered")
                new_update_status(db, item_id, "acted_on")
            elif start_status == "completed":
                new_update_status(db, item_id, "analyzed")
                new_update_status(db, item_id, "delivered")
                new_update_status(db, item_id, "acted_on")
                new_update_status(db, item_id, "completed")
            
            # Archive should always work
            assert new_update_status(db, item_id, "archived")
            item = get_item(db, item_id)
            assert item["status"] == "archived"
        
        print("✓ archived always allowed")


def test_mark_delivered_stores_telegram_id():
    """Test mark_delivered stores telegram_message_id."""
    with temp_db() as db:
        item_id = add_item(db, sample_queue_item("t001"))
        update_analysis(db, item_id, "{}", "[]")
        get_next_batch(db, batch_size=1)  # Assign batch_id
        
        assert mark_delivered(db, item_id, "msg_456")
        
        item = get_item(db, item_id)
        assert item["telegram_message_id"] == "msg_456"
        assert item["status"] == "delivered"
        
        print("✓ mark_delivered stores telegram_id")


def test_get_undelivered():
    """Test get_undelivered returns only analyzed items without batch_id."""
    with temp_db() as db:
        # Add various items
        id1 = add_item(db, sample_queue_item("t001"))
        id2 = add_item(db, sample_queue_item("t002"))
        id3 = add_item(db, sample_queue_item("t003"))
        id4 = add_item(db, sample_queue_item("t004"))
        
        # Only id2 and id3 are analyzed
        update_analysis(db, id2, "{}", "[]")
        update_analysis(db, id3, "{}", "[]")
        
        # id4 is analyzed but already in a batch
        update_analysis(db, id4, "{}", "[]")
        batch = get_next_batch(db, batch_size=1)  # Assigns batch_id to first analyzed item (id2)
        
        # get_undelivered should only return id3 and id4 (id2 has batch_id now)
        undelivered = get_undelivered(db)
        assert len(undelivered) == 2
        undelivered_ids = {item["id"] for item in undelivered}
        assert undelivered_ids == {id3, id4}
        
        # Test limit
        limited = get_undelivered(db, limit=1)
        assert len(limited) == 1
        
        print("✓ get_undelivered")


def test_dedup_via_sqlite():
    """Test is_already_processed checks SQLite for duplicates."""
    from bookmark_digest.fetcher import is_already_processed
    
    with temp_db() as db:
        # Add item
        add_item(db, {"source": "twitter", "source_id": "12345", "title": "Test"})
        
        # Should be found
        assert is_already_processed(db, "twitter", "12345")
        
        # Should not be found
        assert not is_already_processed(db, "twitter", "99999")
        assert not is_already_processed(db, "reddit", "12345")
        
        print("✓ dedup via SQLite")


if __name__ == "__main__":
    test_add_and_get()
    test_claim_and_update()
    test_queued_items()
    test_stats()
    test_archive()
    test_get_item_missing()
    test_full_lifecycle()
    test_get_next_batch_limits()
    test_get_next_batch_partial()
    test_status_transition_validation()
    test_status_transition_archived_always_allowed()
    test_mark_delivered_stores_telegram_id()
    test_get_undelivered()
    test_dedup_via_sqlite()
    print("\n✅ QUEUE TESTS PASSED")
