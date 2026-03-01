#!/usr/bin/env python3
"""Shared test utilities for bookmark-digest tests."""

import os
import tempfile
from contextlib import contextmanager


@contextmanager
def temp_db():
    """Context manager that provides a temporary SQLite database path."""
    from bookmark_digest.bookmark_queue import init_db
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        init_db(db_path)
        yield db_path


@contextmanager
def temp_state():
    """Context manager that provides a temporary state file path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "state.json")


@contextmanager
def env_override(**kwargs):
    """Context manager to temporarily override environment variables."""
    old_values = {}
    for key, value in kwargs.items():
        old_values[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(value)
    try:
        yield
    finally:
        for key, old_value in old_values.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


def sample_bookmark(tweet_id: str = "123456", **overrides) -> dict:
    """Create a sample bookmark dict for testing."""
    base = {
        "id": tweet_id,
        "text": "Check out this amazing AI model!",
        "author": {"username": "testuser", "name": "Test User"},
        "likeCount": 100,
        "retweetCount": 20,
        "replyCount": 5,
        "conversationId": tweet_id,
    }
    base.update(overrides)
    return base


def sample_queue_item(source_id: str = "t001", **overrides) -> dict:
    """Create a sample queue item dict for testing."""
    base = {
        "source": "twitter",
        "source_id": source_id,
        "title": f"Test item {source_id}",
        "category": "TECH",
        "canonical_url": f"https://x.com/{source_id}",
    }
    base.update(overrides)
    return base
