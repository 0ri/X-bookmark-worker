#!/usr/bin/env python3
"""Twitter bookmark fetcher — pulls new bookmarks via bird CLI, deduplicates against state."""

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .bird import run_bird

logger = logging.getLogger(__name__)

# Default max IDs to keep in state file (overridden by config.max_processed_ids)
_DEFAULT_MAX_PROCESSED_IDS = 2000


def _load_state(state_path: str) -> dict:
    """Load state file or return empty state.

    If the state file is corrupted, backs it up and starts fresh.
    """
    p = Path(state_path)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            # Corrupt JSON - back it up and start fresh
            backup_path = p.with_suffix(f".corrupt.{int(time.time())}")
            p.rename(backup_path)
            logger.error("State file corrupted. Backed up to %s. Starting fresh.", backup_path)
            return {"processed_ids": [], "last_fetch": None}
        except OSError as e:
            logger.warning("Failed to read state file %s: %s", state_path, e)
    return {"processed_ids": [], "last_fetch": None}


def _save_state(state_path: str, state: dict) -> None:
    """Atomically write state file."""
    p = Path(state_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.rename(p)


def fetch_new_bookmarks(state_path: str, limit: int = 50, config=None) -> list[dict]:
    """Fetch bookmarks via bird CLI, filter out already-processed IDs.

    Args:
        state_path: Path to the JSON state file for dedup tracking
        limit: Max bookmarks to fetch
        config: Config object (optional, used for bird_cli settings)

    Returns:
        List of raw bookmark dicts (only new ones).
    """
    state = _load_state(state_path)
    processed = set(state.get("processed_ids", []))

    # Pass config to run_bird for bird_cli path/timeout
    bird_kwargs = {}
    if config is not None:
        if config.bird_timeout:
            bird_kwargs["timeout"] = config.bird_timeout
        if config.bird_retry is not None:
            bird_kwargs["retry"] = config.bird_retry
        if config.bird_cli:
            bird_kwargs["bird_cli"] = config.bird_cli

    raw = run_bird(["bookmarks", "--json", "-n", str(limit)], **bird_kwargs)
    if raw is None:
        logger.error("Could not fetch bookmarks from bird CLI")
        return []

    # bird outputs a JSON array
    try:
        bookmarks = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse bird JSON output: %s", e)
        return []

    if not isinstance(bookmarks, list):
        logger.error("Expected list from bird, got %s", type(bookmarks).__name__)
        return []

    new_bookmarks = [bm for bm in bookmarks if bm.get("id") not in processed]

    # Update last_fetch timestamp
    state["last_fetch"] = datetime.now(timezone.utc).isoformat()
    _save_state(state_path, state)

    logger.info("Fetched %d bookmarks, %d new", len(bookmarks), len(new_bookmarks))
    return new_bookmarks


def is_already_processed(db_path: str, source: str, source_id: str) -> bool:
    """Check if an item has already been processed (exists in SQLite queue).
    
    This is the SQLite-based deduplication check. The JSON state file is kept
    as a write-through cache for fast startup, but SQLite is the source of truth.
    
    Args:
        db_path: Path to SQLite database file
        source: Source name (e.g., 'twitter')
        source_id: Source-specific ID (e.g., tweet ID)
        
    Returns:
        True if item exists in queue, False otherwise
    """
    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        cursor = conn.execute(
            "SELECT 1 FROM queue WHERE source = ? AND source_id = ? LIMIT 1",
            (source, source_id)
        )
        exists = cursor.fetchone() is not None
        conn.close()
        return exists
    except sqlite3.Error as e:
        logger.warning("Failed to check SQLite for duplicate: %s", e)
        return False  # Fail open - allow insertion, UNIQUE constraint will catch it


def mark_processed(state_path: str, tweet_ids: list[str], max_ids: int | None = None) -> None:
    """Add tweet IDs to the processed set in state file.

    Caps the list at max_ids to prevent unbounded growth.
    Uses a rolling window - keeps most recent IDs.

    Args:
        state_path: Path to the JSON state file
        tweet_ids: List of tweet ID strings to mark as processed
        max_ids: Max IDs to keep (default: 2000)
    """
    if not tweet_ids:
        return

    cap = max_ids if max_ids is not None else _DEFAULT_MAX_PROCESSED_IDS

    state = _load_state(state_path)
    existing = list(state.get("processed_ids", []))

    # Add new IDs (use set for O(1) lookup)
    existing_set = set(existing)
    existing.extend(str(tid) for tid in tweet_ids if str(tid) not in existing_set)

    # Cap the list (keep most recent)
    if len(existing) > cap:
        existing = existing[-cap:]

    state["processed_ids"] = existing
    state["last_fetch"] = datetime.now(timezone.utc).isoformat()
    _save_state(state_path, state)
    logger.info("Marked %d IDs as processed (total: %d, capped at %d)",
                len(tweet_ids), len(existing), cap)
