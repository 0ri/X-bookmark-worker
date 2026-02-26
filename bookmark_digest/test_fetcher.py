#!/usr/bin/env python3
"""Tests for fetcher.py — state management (no bird CLI calls)."""

import json
import os
from bookmark_digest.test_utils import temp_state
from bookmark_digest.fetcher import _load_state, _save_state, mark_processed


def test_load_state_missing():
    """_load_state with nonexistent file returns empty state."""
    state = _load_state("/tmp/nonexistent_state_xyz.json")
    assert state == {"processed_ids": [], "last_fetch": None}
    print("✓ _load_state: missing → empty")


def test_load_state_valid():
    """_load_state with valid JSON returns parsed state."""
    with temp_state() as path:
        data = {"processed_ids": ["100", "200"], "last_fetch": "2026-01-30T10:00:00Z"}
        with open(path, "w") as f:
            json.dump(data, f)
        
        state = _load_state(path)
        assert state["processed_ids"] == ["100", "200"]
        print("✓ _load_state: valid file")


def test_load_state_corrupt():
    """_load_state with corrupt JSON backs up and returns empty."""
    with temp_state() as path:
        with open(path, "w") as f:
            f.write("{broken json!!")
        
        state = _load_state(path)
        assert state == {"processed_ids": [], "last_fetch": None}
        assert not os.path.exists(path), "Corrupt file should be renamed"
        print("✓ _load_state: corrupt → empty + backup")


def test_save_state_atomic():
    """_save_state writes atomically via rename."""
    with temp_state() as path:
        state = {"processed_ids": ["1", "2"], "last_fetch": "2026-01-30T12:00:00Z"}
        _save_state(path, state)
        
        assert os.path.exists(path)
        assert not os.path.exists(path + ".tmp"), "Temp file should be gone"
        
        loaded = json.loads(open(path).read())
        assert loaded == state
        print("✓ _save_state: atomic write")


def test_save_state_creates_dirs():
    """_save_state creates parent directories."""
    with temp_state() as base:
        path = os.path.join(os.path.dirname(base), "nested", "deep", "state.json")
        _save_state(path, {"processed_ids": [], "last_fetch": None})
        assert os.path.exists(path)
        print("✓ _save_state: creates dirs")


def test_mark_processed():
    """mark_processed adds and deduplicates IDs."""
    with temp_state() as path:
        mark_processed(path, ["100", "200", "300"])
        state = json.loads(open(path).read())
        assert set(state["processed_ids"]) == {"100", "200", "300"}
        
        # Dedupe on second call
        mark_processed(path, ["200", "300", "400"])
        state = json.loads(open(path).read())
        assert len(state["processed_ids"]) == 4
        print("✓ mark_processed: adds + dedupes")


def test_mark_processed_timestamp():
    """mark_processed updates last_fetch timestamp."""
    with temp_state() as path:
        mark_processed(path, ["100"])
        state = json.loads(open(path).read())
        assert state["last_fetch"] is not None
        assert "T" in state["last_fetch"]
        print("✓ mark_processed: updates timestamp")


def test_mark_processed_empty():
    """mark_processed with empty list is no-op."""
    with temp_state() as path:
        mark_processed(path, [])
        assert not os.path.exists(path)
        print("✓ mark_processed: empty is no-op")


if __name__ == "__main__":
    test_load_state_missing()
    test_load_state_valid()
    test_load_state_corrupt()
    test_save_state_atomic()
    test_save_state_creates_dirs()
    test_mark_processed()
    test_mark_processed_timestamp()
    test_mark_processed_empty()
    print("\n✅ FETCHER TESTS PASSED")
