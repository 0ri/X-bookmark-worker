#!/usr/bin/env python3
"""Tests for CLI module (__main__.py) — integration tests."""

import json
import os
import subprocess
import sys
import tempfile
from bookmark_digest.test_utils import sample_queue_item
from bookmark_digest.bookmark_queue import (
    init_db, add_item,
    store_analyses, set_sending, mark_delivered_with_message,
)


def run_cli(*args, data_dir: str) -> subprocess.CompletedProcess:
    """Run the CLI with given args and DATA_DIR."""
    env = os.environ.copy()
    env["DATA_DIR"] = data_dir
    return subprocess.run(
        [sys.executable, "-m", "bookmark_digest"] + list(args),
        capture_output=True, text=True, timeout=10, env=env,
        cwd=os.path.dirname(os.path.dirname(__file__)),
    )


def test_stats_json():
    """Test stats command outputs valid JSON."""
    with tempfile.TemporaryDirectory() as data_dir:
        db = os.path.join(data_dir, "queue.db")
        init_db(db)
        add_item(db, sample_queue_item("test_stats"))
        
        result = run_cli("--json", "stats", data_dir=data_dir)
        
        assert result.returncode == 0, f"Failed: {result.stderr}"
        output = json.loads(result.stdout)
        assert "total" in output
        assert "stats" in output
        assert output["total"] >= 1
        print("✓ CLI: stats --json")


def test_callback():
    """Test callback command — requires item in delivered state."""
    with tempfile.TemporaryDirectory() as data_dir:
        db = os.path.join(data_dir, "queue.db")
        init_db(db)
        item_id = add_item(db, sample_queue_item("test_callback"))
        # Advance item to delivered state
        store_analyses(db, [{"item_id": item_id, "category": "Test", "analysis": "T", "buttons": ["dd"], "content_type": "tweet"}])
        set_sending(db, item_id)
        mark_delivered_with_message(db, item_id, "tg_1", "batch_1")

        result = run_cli("--json", "callback", f"q|dd|{item_id}", data_dir=data_dir)

        assert result.returncode == 0, f"Failed: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["success"] is True
        assert output["action"] == "deep_dive"
        print("✓ CLI: callback (v2 format)")


if __name__ == "__main__":
    test_stats_json()
    test_callback()
    print("\n✅ CLI TESTS PASSED")
