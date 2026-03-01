#!/usr/bin/env python3
"""Tests for profile.py — Session 6 profile system.

Tests for:
- build_profile(db_path) querying DB for topic/content_type weights
- get_context(profile_path) loading profile and formatting context string
- update_weights(profile_path, action, category) adjusting weights
- save/load roundtrip
- Edge cases: empty DB, missing file, rebuild overwrites
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from .profile import (
    build_profile,
    build_profile_from_bookmarks,
    get_context,
    load_profile,
    save_profile,
    update_weights,
    _normalize,
    _weight_label,
    _empty_profile,
    ACTION_WEIGHTS,
)
from .bookmark_queue import init_db, add_item, store_analyses


# ============================================================================
# Helpers
# ============================================================================

def _make_db(tmpdir: str) -> str:
    """Create an initialized queue DB and return its path."""
    db_path = os.path.join(tmpdir, "queue.db")
    init_db(db_path)
    return db_path


def _add_analyzed_items(db_path: str, specs: list[dict]) -> list[str]:
    """Add items to DB and store analyses so they have categories.

    Each spec should have: category, content_type (optional, default "tweet").
    Returns list of item IDs.
    """
    item_ids = []
    for i, spec in enumerate(specs):
        item_id = add_item(db_path, {
            "source": "twitter",
            "source_id": f"prof_{i}_{spec.get('category', 'x')}",
            "title": f"Test bookmark {i}",
            "raw_content": f"Content about {spec.get('category', 'stuff')}",
        })
        if item_id:
            item_ids.append(item_id)

    analyses = []
    for item_id, spec in zip(item_ids, specs):
        analyses.append({
            "item_id": item_id,
            "category": spec["category"],
            "analysis": "Test analysis",
            "buttons": ["dd"],
            "content_type": spec.get("content_type", "tweet"),
        })

    store_analyses(db_path, analyses)
    return item_ids


# ============================================================================
# File I/O
# ============================================================================

def test_load_profile_missing_file():
    """load_profile returns {} for missing file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "nonexistent.json"
        assert load_profile(path) == {}


def test_save_and_load_profile_roundtrip():
    """save_profile + load_profile preserves all data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "profile.json"
        profile = {
            "version": "v1",
            "topics": {"AI/Agents": 0.8, "Health": 0.6},
            "content_types": {"thread": 0.5, "tweet": 1.0},
            "generated_at": "2026-02-26T00:00:00+00:00",
            "bookmark_count": 10,
        }
        save_profile(path, profile)
        assert load_profile(path) == profile


def test_save_profile_creates_parent_dirs():
    """save_profile creates parent directories if missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "sub" / "nested" / "profile.json"
        save_profile(path, {"version": "v1", "topics": {"test": 0.5}})
        assert path.exists()
        assert load_profile(path)["topics"]["test"] == 0.5


def test_load_profile_handles_corrupt_json():
    """load_profile returns {} for corrupt JSON file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "corrupt.json"
        path.write_text("{broken json")
        assert load_profile(path) == {}


# ============================================================================
# build_profile (from DB)
# ============================================================================

def test_build_profile_from_db():
    """build_profile analyzes DB bookmarks and produces normalized weights."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = _make_db(tmpdir)
        _add_analyzed_items(db_path, [
            {"category": "AI/Agents", "content_type": "tweet"},
            {"category": "AI/Agents", "content_type": "tweet"},
            {"category": "AI/Agents", "content_type": "thread"},
            {"category": "Health", "content_type": "article"},
            {"category": "Health", "content_type": "tweet"},
            {"category": "Programming", "content_type": "repo"},
        ])

        profile = build_profile(db_path)

        assert profile["version"] == "v1"
        assert profile["bookmark_count"] == 6
        assert profile["generated_at"] != ""

        # AI/Agents: 3x (most frequent) → 1.0
        assert profile["topics"]["AI/Agents"] == 1.0
        # Health: 2x → 2/3 ≈ 0.67
        assert profile["topics"]["Health"] == 0.67
        # Programming: 1x → 1/3 ≈ 0.33
        assert profile["topics"]["Programming"] == 0.33

        # Content types: tweet (3x) = 1.0
        assert profile["content_types"]["tweet"] == 1.0
        assert "thread" in profile["content_types"]
        assert "article" in profile["content_types"]
        assert "repo" in profile["content_types"]


def test_build_profile_empty_db():
    """build_profile returns empty profile when no categorized items exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = _make_db(tmpdir)

        profile = build_profile(db_path)

        assert profile["version"] == "v1"
        assert profile["topics"] == {}
        assert profile["content_types"] == {}
        assert profile["bookmark_count"] == 0
        assert profile["generated_at"] != ""


def test_build_profile_respects_limit():
    """build_profile only analyzes up to `limit` bookmarks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = _make_db(tmpdir)
        _add_analyzed_items(db_path, [
            {"category": f"Cat{i}"} for i in range(10)
        ])

        profile = build_profile(db_path, limit=5)
        assert profile["bookmark_count"] == 5


def test_build_profile_pending_items_excluded():
    """build_profile only sees analyzed items (with category set by store_analyses)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = _make_db(tmpdir)

        # Add an item but don't analyze it (stays pending, no category)
        add_item(db_path, {
            "source": "twitter",
            "source_id": "pending_1",
            "title": "Pending item",
        })

        # Add an analyzed item
        _add_analyzed_items(db_path, [{"category": "AI/Agents"}])

        profile = build_profile(db_path)
        assert profile["bookmark_count"] == 1
        assert "AI/Agents" in profile["topics"]


# ============================================================================
# get_context
# ============================================================================

def test_get_context_valid_profile():
    """get_context returns formatted string with topic labels."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "profile.json"
        save_profile(path, {
            "version": "v1",
            "topics": {"AI/Agents": 0.9, "Health": 0.5, "Programming": 0.3},
        })

        context = get_context(str(path))

        assert context.startswith("User interests:")
        assert "AI/Agents (high)" in context
        assert "Health (medium)" in context
        assert "Programming (low)" in context
        assert context.endswith(".")


def test_get_context_empty_profile():
    """get_context returns empty string for missing file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "nonexistent.json"
        assert get_context(str(path)) == ""


def test_get_context_no_topics():
    """get_context returns empty string for profile with empty topics."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "profile.json"
        save_profile(path, {"version": "v1", "topics": {}})
        assert get_context(str(path)) == ""


def test_get_context_sorted_by_weight():
    """get_context lists topics in descending weight order."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "profile.json"
        save_profile(path, {
            "version": "v1",
            "topics": {"Low": 0.1, "High": 0.9, "Mid": 0.5},
        })

        context = get_context(str(path))
        high_pos = context.index("High")
        mid_pos = context.index("Mid")
        low_pos = context.index("Low")
        assert high_pos < mid_pos < low_pos


# ============================================================================
# update_weights
# ============================================================================

def test_update_weights_fc_increment():
    """fc action increments by 0.05."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "profile.json"
        save_profile(path, {
            "version": "v1",
            "topics": {"Health": 0.5, "AI": 0.5},
        })

        result = update_weights(str(path), "fc", "Health")

        # Health: 0.5 + 0.05 = 0.55 (max). AI: 0.5. Norm: Health=1.0, AI≈0.91
        assert result["topics"]["Health"] == 1.0
        assert result["topics"]["AI"] < 1.0

        # Verify persisted
        loaded = load_profile(path)
        assert loaded["topics"]["Health"] == 1.0


def test_update_weights_dd_increment():
    """dd action increments by 0.03."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "profile.json"
        save_profile(path, {
            "version": "v1",
            "topics": {"AI": 1.0, "Health": 0.5},
        })

        result = update_weights(str(path), "dd", "AI")
        # AI: 1.0 + 0.03 = 1.03 (new max). Health: 0.5. Norm: AI=1.0, Health≈0.49
        assert result["topics"]["AI"] == 1.0
        assert result["topics"]["Health"] < 0.5


def test_update_weights_sn_increment():
    """sn action increments by 0.02."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "profile.json"
        save_profile(path, {
            "version": "v1",
            "topics": {"Tech": 1.0, "Health": 0.5},
        })

        result = update_weights(str(path), "sn", "Health")
        # Health: 0.5 + 0.02 = 0.52. Tech: 1.0 (max). Norm: Tech=1.0, Health=0.52
        assert result["topics"]["Tech"] == 1.0
        assert result["topics"]["Health"] == 0.52


def test_update_weights_new_category():
    """update_weights creates entry for category not in profile."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "profile.json"
        save_profile(path, {"version": "v1", "topics": {"AI": 0.8}})

        result = update_weights(str(path), "fc", "NewTopic")
        assert "NewTopic" in result["topics"]
        assert result["topics"]["NewTopic"] > 0


def test_update_weights_creates_profile():
    """update_weights creates profile file if it doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "profile.json"
        assert not path.exists()

        result = update_weights(str(path), "dd", "AI")
        assert "AI" in result["topics"]
        assert result["version"] == "v1"
        assert path.exists()


def test_update_weights_unknown_action():
    """update_weights is a no-op for unknown actions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "profile.json"
        save_profile(path, {"version": "v1", "topics": {"AI": 0.8}})

        result = update_weights(str(path), "zzz", "AI")
        assert result["topics"]["AI"] == 0.8


def test_update_weights_empty_category():
    """update_weights is a no-op for empty category."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "profile.json"
        save_profile(path, {"version": "v1", "topics": {"AI": 0.8}})

        result = update_weights(str(path), "fc", "")
        assert result["topics"]["AI"] == 0.8


def test_update_weights_normalizes():
    """All topic weights stay in 0-1 range after normalization."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "profile.json"
        save_profile(path, {
            "version": "v1",
            "topics": {"A": 0.3, "B": 0.6, "C": 0.9},
        })

        update_weights(str(path), "fc", "A")
        loaded = load_profile(path)

        assert loaded["topics"]["C"] == 1.0  # was max before, still max
        assert all(0.0 <= v <= 1.0 for v in loaded["topics"].values())


# ============================================================================
# Rebuild (build_profile + save overwrites)
# ============================================================================

def test_rebuild_overwrites_existing():
    """Building a new profile and saving it overwrites old data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = _make_db(tmpdir)
        profile_path = Path(tmpdir) / "profile.json"

        # Save old profile
        save_profile(profile_path, {
            "version": "v1",
            "topics": {"OldTopic": 1.0},
            "content_types": {},
            "generated_at": "old",
            "bookmark_count": 999,
        })

        # Add new DB items
        _add_analyzed_items(db_path, [{"category": "NewTopic"}])

        # Rebuild
        new_profile = build_profile(db_path)
        save_profile(profile_path, new_profile)

        loaded = load_profile(profile_path)
        assert "OldTopic" not in loaded["topics"]
        assert "NewTopic" in loaded["topics"]
        assert loaded["bookmark_count"] == 1


# ============================================================================
# Legacy helper (build_profile_from_bookmarks)
# ============================================================================

def test_build_profile_from_bookmarks_structure():
    """build_profile_from_bookmarks structures raw bookmark data."""
    bookmarks = [
        {
            "text": "AI is cool",
            "author": {"username": "alice"},
            "likes": 100,
            "retweets": 20,
            "conversation_count": 5,
            "media": ["img1.jpg"],
            "urls": ["https://example.com"],
        },
        {
            "text": "Health tip",
            "author": {"username": "bob"},
            "likes": 50,
            "retweets": 10,
            "conversation_count": 1,
            "media": [],
            "urls": [],
        },
    ]

    result = build_profile_from_bookmarks(bookmarks)

    assert "interests" in result
    assert "bookmark_patterns" in result
    assert "analysis_preferences" in result
    assert "raw_sample" in result
    assert result["total_bookmarks_analyzed"] == 2
    assert len(result["raw_sample"]) == 2
    assert result["raw_sample"][0]["author"] == "alice"


def test_build_profile_from_bookmarks_limits_sample():
    """build_profile_from_bookmarks caps raw_sample at 50."""
    bookmarks = [
        {"text": f"Bm {i}", "author": {"username": "u"}, "likes": 0,
         "retweets": 0, "conversation_count": 1, "media": [], "urls": []}
        for i in range(100)
    ]

    result = build_profile_from_bookmarks(bookmarks)
    assert len(result["raw_sample"]) == 50
    assert result["total_bookmarks_analyzed"] == 100


def test_build_profile_from_bookmarks_truncates_text():
    """build_profile_from_bookmarks truncates text to 200 chars."""
    bookmarks = [
        {"text": "A" * 500, "author": {"username": "u"}, "likes": 0,
         "retweets": 0, "conversation_count": 1, "media": [], "urls": []}
    ]

    result = build_profile_from_bookmarks(bookmarks)
    assert len(result["raw_sample"][0]["text"]) == 200


# ============================================================================
# Internal helpers
# ============================================================================

def test_normalize_basic():
    """_normalize scales values relative to max."""
    result = _normalize({"a": 10, "b": 5, "c": 2})
    assert result["a"] == 1.0
    assert result["b"] == 0.5
    assert result["c"] == 0.2


def test_normalize_empty():
    assert _normalize({}) == {}


def test_normalize_all_zero():
    result = _normalize({"a": 0, "b": 0})
    assert result["a"] == 0.0


def test_weight_label():
    assert _weight_label(0.9) == "high"
    assert _weight_label(0.7) == "high"
    assert _weight_label(0.5) == "medium"
    assert _weight_label(0.4) == "medium"
    assert _weight_label(0.3) == "low"
    assert _weight_label(0.0) == "low"


def test_empty_profile_schema():
    profile = _empty_profile()
    assert profile["version"] == "v1"
    assert profile["topics"] == {}
    assert profile["content_types"] == {}
    assert profile["generated_at"] == ""
    assert profile["bookmark_count"] == 0
