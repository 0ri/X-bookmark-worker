"""
Tests for Session 3: State Ownership + llm-task Schema + CLI Wrappers.

Covers:
- JSON output tests for CLI subcommands (fetch, store-analyses, reset-failed)
- store_analyses idempotency
- Transition guards (store_analyses on non-pending items)
- Batch table (save_batch_footer + read back)
- sending status lifecycle (analyzed → sending → delivered)
- reset_failed (failed → pending)
- v2→v3 migration (analysis_schema_version column, batches table)
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from .test_utils import temp_db, sample_queue_item
from .bookmark_queue import (
    add_item, get_item, get_stats,
    update_analysis, update_status,
    store_analyses, reset_failed, set_sending,
    mark_delivered_with_message, save_batch_footer, get_batch_footer,
    init_db,
)


# ============================================================================
# store_analyses tests
# ============================================================================

def test_store_analyses_basic():
    """store_analyses updates pending items with analysis data."""
    with temp_db() as db:
        id1 = add_item(db, sample_queue_item("t001"))
        id2 = add_item(db, sample_queue_item("t002"))

        analyses = [
            {
                "item_id": id1,
                "category": "AI/Agents",
                "analysis": "Great AI agent framework.",
                "buttons": ["dd", "im"],
                "content_type": "tweet",
                "relevance_score": 0.9,
            },
            {
                "item_id": id2,
                "category": "Health/Supplements",
                "analysis": "Creatine loading study.",
                "buttons": ["fc", "sn"],
                "content_type": "thread",
            },
        ]

        count = store_analyses(db, analyses)
        assert count == 2

        item1 = get_item(db, id1)
        assert item1["status"] == "analyzed"
        assert item1["category"] == "AI/Agents"
        assert item1["analysis_schema_version"] == "v1"
        analysis_data = json.loads(item1["analysis"])
        assert analysis_data["relevance_score"] == 0.9

        item2 = get_item(db, id2)
        assert item2["status"] == "analyzed"
        assert json.loads(item2["buttons_json"]) == ["fc", "sn"]


def test_store_analyses_idempotent():
    """Running store_analyses twice with same data produces no change on second call."""
    with temp_db() as db:
        id1 = add_item(db, sample_queue_item("t001"))

        analyses = [{
            "item_id": id1,
            "category": "Tech",
            "analysis": "Some analysis.",
            "buttons": ["dd"],
            "content_type": "tweet",
        }]

        # First call: updates 1 item
        count1 = store_analyses(db, analyses)
        assert count1 == 1

        # Second call: item is now 'analyzed', not 'pending' → no update
        count2 = store_analyses(db, analyses)
        assert count2 == 0

        # Item still has the original analysis
        item = get_item(db, id1)
        assert item["status"] == "analyzed"


def test_store_analyses_rejects_non_pending():
    """store_analyses only updates items with status='pending'."""
    with temp_db() as db:
        id1 = add_item(db, sample_queue_item("t001"))

        # Manually advance to analyzed
        update_analysis(db, id1, '{"test": true}', '["dd"]')

        analyses = [{
            "item_id": id1,
            "category": "Override",
            "analysis": "This should not overwrite.",
            "buttons": ["im"],
            "content_type": "tweet",
        }]

        count = store_analyses(db, analyses)
        assert count == 0

        # Original analysis preserved
        item = get_item(db, id1)
        assert item["category"] != "Override"


def test_store_analyses_skips_missing_items():
    """store_analyses silently skips item_ids that don't exist."""
    with temp_db() as db:
        analyses = [{
            "item_id": "nonexistent_id",
            "category": "Test",
            "analysis": "Should be skipped.",
            "buttons": ["dd"],
            "content_type": "tweet",
        }]

        count = store_analyses(db, analyses)
        assert count == 0


def test_store_analyses_empty_list():
    """store_analyses with empty list returns 0."""
    with temp_db() as db:
        count = store_analyses(db, [])
        assert count == 0


def test_store_analyses_partial_update():
    """store_analyses updates only eligible items in a mixed batch."""
    with temp_db() as db:
        id1 = add_item(db, sample_queue_item("t001"))
        id2 = add_item(db, sample_queue_item("t002"))

        # Advance id2 past pending
        update_analysis(db, id2, '{}', '[]')

        analyses = [
            {"item_id": id1, "category": "A", "analysis": "a", "buttons": ["dd"], "content_type": "tweet"},
            {"item_id": id2, "category": "B", "analysis": "b", "buttons": ["dd"], "content_type": "tweet"},
        ]

        count = store_analyses(db, analyses)
        assert count == 1  # Only id1 was pending


# ============================================================================
# reset_failed tests
# ============================================================================

def test_reset_failed_basic():
    """reset_failed moves failed items back to pending."""
    with temp_db() as db:
        id1 = add_item(db, sample_queue_item("t001"))
        id2 = add_item(db, sample_queue_item("t002"))

        # Advance to analyzed then fail
        update_analysis(db, id1, '{}', '[]')
        update_status(db, id1, "failed", last_error="timeout")

        count = reset_failed(db)
        assert count == 1

        item = get_item(db, id1)
        assert item["status"] == "pending"
        assert item["error_count"] == 0
        assert item["last_error"] is None

        # id2 should be untouched
        item2 = get_item(db, id2)
        assert item2["status"] == "pending"


def test_reset_failed_no_failed_items():
    """reset_failed returns 0 when no items are failed."""
    with temp_db() as db:
        add_item(db, sample_queue_item("t001"))
        count = reset_failed(db)
        assert count == 0


# ============================================================================
# sending status lifecycle tests
# ============================================================================

def test_set_sending_from_analyzed():
    """set_sending transitions analyzed → sending."""
    with temp_db() as db:
        id1 = add_item(db, sample_queue_item("t001"))
        update_analysis(db, id1, '{}', '[]')

        assert set_sending(db, id1)
        item = get_item(db, id1)
        assert item["status"] == "sending"


def test_set_sending_rejects_non_analyzed():
    """set_sending fails if item is not in analyzed state."""
    with temp_db() as db:
        id1 = add_item(db, sample_queue_item("t001"))
        # Item is pending, not analyzed
        assert not set_sending(db, id1)


def test_full_two_phase_delivery():
    """Full lifecycle: pending → analyzed → sending → delivered."""
    with temp_db() as db:
        id1 = add_item(db, sample_queue_item("t001"))
        update_analysis(db, id1, '{"summary": "test"}', '["dd"]')

        # Phase 1: set sending
        assert set_sending(db, id1)
        assert get_item(db, id1)["status"] == "sending"

        # Phase 2: mark delivered with message
        assert mark_delivered_with_message(db, id1, "msg_123", "batch_abc")
        item = get_item(db, id1)
        assert item["status"] == "delivered"
        assert item["telegram_message_id"] == "msg_123"
        assert item["batch_id"] == "batch_abc"


def test_mark_delivered_with_message_rejects_non_sending():
    """mark_delivered_with_message only works from sending state."""
    with temp_db() as db:
        id1 = add_item(db, sample_queue_item("t001"))
        update_analysis(db, id1, '{}', '[]')
        # Item is analyzed, not sending
        assert not mark_delivered_with_message(db, id1, "msg_123", "batch_abc")


def test_sending_rollback():
    """sending → analyzed rollback on failure."""
    with temp_db() as db:
        id1 = add_item(db, sample_queue_item("t001"))
        update_analysis(db, id1, '{}', '[]')
        set_sending(db, id1)

        # Rollback: sending → analyzed
        update_status(db, id1, "analyzed")
        item = get_item(db, id1)
        assert item["status"] == "analyzed"


# ============================================================================
# Batch table tests
# ============================================================================

def test_save_batch_footer_and_read():
    """save_batch_footer stores data and get_batch_footer reads it back."""
    with temp_db() as db:
        save_batch_footer(db, "batch_001", "footer_msg_42", 5)

        footer = get_batch_footer(db, "batch_001")
        assert footer is not None
        assert footer["batch_id"] == "batch_001"
        assert footer["footer_message_id"] == "footer_msg_42"
        assert footer["item_count"] == 5
        assert footer["delivered_at"] is not None


def test_save_batch_footer_upsert():
    """save_batch_footer updates existing record on conflict."""
    with temp_db() as db:
        save_batch_footer(db, "batch_001", "footer_v1", 3)
        save_batch_footer(db, "batch_001", "footer_v2", 5)

        footer = get_batch_footer(db, "batch_001")
        assert footer["footer_message_id"] == "footer_v2"
        assert footer["item_count"] == 5


def test_get_batch_footer_missing():
    """get_batch_footer returns None for nonexistent batch."""
    with temp_db() as db:
        assert get_batch_footer(db, "nonexistent") is None


# ============================================================================
# v2→v3 migration tests
# ============================================================================

def test_v3_migration_adds_analysis_schema_version():
    """v3 migration adds analysis_schema_version column to queue table."""
    import sqlite3
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        init_db(db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(queue)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "analysis_schema_version" in columns
        conn.close()


def test_v3_migration_creates_batches_table():
    """v3 migration creates batches table."""
    import sqlite3
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        init_db(db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='batches'"
        )
        assert cursor.fetchone() is not None
        conn.close()


# ============================================================================
# CLI JSON output tests
# ============================================================================

def _run_cli(*args, data_dir: str, stdin_data: str = None) -> subprocess.CompletedProcess:
    """Run the CLI with given args and DATA_DIR."""
    env = os.environ.copy()
    env["DATA_DIR"] = data_dir
    return subprocess.run(
        [sys.executable, "-m", "bookmark_digest", "--json"] + list(args),
        capture_output=True, text=True, timeout=10, env=env,
        input=stdin_data,
        cwd=os.path.dirname(os.path.dirname(__file__)),
    )


def test_cli_store_analyses_json():
    """store-analyses outputs valid JSON."""
    with tempfile.TemporaryDirectory() as data_dir:
        db = os.path.join(data_dir, "queue.db")
        init_db(db)
        item_id = add_item(db, sample_queue_item("t001"))

        input_json = json.dumps({
            "analyses": [{
                "item_id": item_id,
                "category": "AI",
                "analysis": "Test analysis.",
                "buttons": ["dd"],
                "content_type": "tweet",
            }]
        })

        result = _run_cli("store-analyses", data_dir=data_dir, stdin_data=input_json)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["stored"] == 1
        assert output["total_input"] == 1


def test_cli_reset_failed_json():
    """reset-failed outputs valid JSON."""
    with tempfile.TemporaryDirectory() as data_dir:
        db = os.path.join(data_dir, "queue.db")
        init_db(db)

        result = _run_cli("reset-failed", data_dir=data_dir)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert "reset" in output
        assert output["reset"] == 0


def test_cli_enrich_json():
    """enrich outputs valid JSON."""
    with tempfile.TemporaryDirectory() as data_dir:
        db = os.path.join(data_dir, "queue.db")
        init_db(db)

        result = _run_cli("enrich", "--batch-size", "3", data_dir=data_dir)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["batch_size"] == 3
        assert output["status"] == "placeholder"


def test_cli_deliver_json():
    """deliver outputs valid JSON with empty queue."""
    with tempfile.TemporaryDirectory() as data_dir:
        db = os.path.join(data_dir, "queue.db")
        init_db(db)

        result = _run_cli("deliver", "--batch-size", "5", data_dir=data_dir)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["delivered"] == 0
        assert output["remaining"] == 0
        assert output["batch_id"] is None
        assert output["messages"] == []


def test_cli_store_analyses_idempotent():
    """CLI store-analyses is idempotent: second call stores 0."""
    with tempfile.TemporaryDirectory() as data_dir:
        db = os.path.join(data_dir, "queue.db")
        init_db(db)
        item_id = add_item(db, sample_queue_item("t001"))

        input_json = json.dumps({
            "analyses": [{
                "item_id": item_id,
                "category": "AI",
                "analysis": "Test.",
                "buttons": ["dd"],
                "content_type": "tweet",
            }]
        })

        # First call
        r1 = _run_cli("store-analyses", data_dir=data_dir, stdin_data=input_json)
        assert json.loads(r1.stdout)["stored"] == 1

        # Second call (idempotent)
        r2 = _run_cli("store-analyses", data_dir=data_dir, stdin_data=input_json)
        assert json.loads(r2.stdout)["stored"] == 0


def test_cli_store_analyses_invalid_json():
    """store-analyses returns error on invalid JSON stdin."""
    with tempfile.TemporaryDirectory() as data_dir:
        db = os.path.join(data_dir, "queue.db")
        init_db(db)

        result = _run_cli("store-analyses", data_dir=data_dir, stdin_data="not json")
        assert result.returncode == 1


# ============================================================================
# Schema file validation
# ============================================================================

def test_schema_file_exists_and_valid():
    """schemas/bookmark-analysis-v1.json exists and is valid JSON Schema."""
    schema_path = Path(__file__).parent.parent / "schemas" / "bookmark-analysis-v1.json"
    assert schema_path.exists(), f"Schema file should exist at {schema_path}"

    with open(schema_path) as f:
        schema = json.load(f)

    assert schema["$id"] == "bookmark-analysis-v1"
    assert "analyses" in schema["properties"]
    items_props = schema["properties"]["analyses"]["items"]["properties"]
    assert "item_id" in items_props
    assert "category" in items_props
    assert "analysis" in items_props
    assert "buttons" in items_props
    assert "content_type" in items_props


if __name__ == "__main__":
    test_store_analyses_basic()
    test_store_analyses_idempotent()
    test_store_analyses_rejects_non_pending()
    test_store_analyses_skips_missing_items()
    test_store_analyses_empty_list()
    test_store_analyses_partial_update()
    test_reset_failed_basic()
    test_reset_failed_no_failed_items()
    test_set_sending_from_analyzed()
    test_set_sending_rejects_non_analyzed()
    test_full_two_phase_delivery()
    test_mark_delivered_with_message_rejects_non_sending()
    test_sending_rollback()
    test_save_batch_footer_and_read()
    test_save_batch_footer_upsert()
    test_get_batch_footer_missing()
    test_v3_migration_adds_analysis_schema_version()
    test_v3_migration_creates_batches_table()
    test_schema_file_exists_and_valid()
    print("\n✅ SESSION 3 TESTS PASSED")
