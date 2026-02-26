#!/usr/bin/env python3
"""
User profile management for bookmark analysis personalization.

Builds interest profiles from bookmark history in the queue DB.
Tracks topic weights, content type preferences, and adjusts
weights based on user callback actions over time.
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Weight increments per action type
ACTION_WEIGHTS = {
    "fc": 0.05,  # Fact Check
    "dd": 0.03,  # Deep Dive
    "sn": 0.02,  # Save Notes
    "im": 0.04,  # Implement
    "rs": 0.01,  # Read Source
    "fs": 0.02,  # Full Summary
    "rm": 0.02,  # Remind Me
}

# Weight label thresholds (descending)
_WEIGHT_LABELS = [
    (0.7, "high"),
    (0.4, "medium"),
    (0.0, "low"),
]


def _weight_label(weight: float) -> str:
    """Convert numeric weight (0-1) to human label."""
    for threshold, label in _WEIGHT_LABELS:
        if weight >= threshold:
            return label
    return "low"


def _normalize(counts: dict[str, float]) -> dict[str, float]:
    """Normalize values to 0-1 range based on max value."""
    if not counts:
        return {}
    max_val = max(counts.values())
    if max_val <= 0:
        return {k: 0.0 for k in counts}
    return {k: round(v / max_val, 2) for k, v in counts.items()}


def _empty_profile() -> dict:
    """Return an empty profile with correct schema."""
    return {
        "version": "v1",
        "topics": {},
        "content_types": {},
        "generated_at": "",
        "bookmark_count": 0,
    }


# ============================================================================
# File I/O
# ============================================================================

def load_profile(path: str | Path) -> dict:
    """Load user profile from JSON file.

    Returns empty dict if file doesn't exist or is corrupt.
    """
    path = Path(path)
    if not path.exists():
        logger.debug("Profile not found at %s", path)
        return {}

    try:
        data = json.loads(path.read_text())
        logger.info("Loaded profile from %s", path)
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load profile from %s: %s", path, e)
        return {}


def save_profile(path: str | Path, profile: dict) -> None:
    """Save profile to JSON file with atomic write (temp + rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp"
    )

    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(profile, f, indent=2, ensure_ascii=False)
            f.write('\n')
        os.rename(temp_path, path)
        logger.info("Saved profile to %s", path)
    except Exception as e:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise IOError(f"Failed to save profile: {e}") from e


# ============================================================================
# Core API
# ============================================================================

def build_profile(db_path: str, limit: int = 200) -> dict:
    """Analyze last N bookmarks from the queue DB to build interest profile.

    Queries bookmarks that have been categorized, counts category frequencies
    and content types, then normalizes weights to 0-1 range.

    Args:
        db_path: Path to the SQLite queue database
        limit: Maximum number of recent bookmarks to analyze

    Returns:
        Profile dict:
        {
            "version": "v1",
            "topics": {"AI/Agents": 0.8, "Health": 0.6, ...},
            "content_types": {"thread": 0.4, "tweet": 1.0, ...},
            "generated_at": "ISO-8601",
            "bookmark_count": N
        }
        Topic weights are normalized 0-1 based on frequency.
    """
    from .bookmark_queue import _connect

    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """SELECT category, analysis
               FROM queue
               WHERE category IS NOT NULL AND category != ''
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,)
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        profile = _empty_profile()
        profile["generated_at"] = datetime.now(timezone.utc).isoformat()
        return profile

    # Count category and content_type frequencies
    topic_counts: dict[str, int] = {}
    content_type_counts: dict[str, int] = {}

    for row in rows:
        cat = row["category"]
        if cat:
            topic_counts[cat] = topic_counts.get(cat, 0) + 1

        # Parse analysis JSON for content_type
        if row.get("analysis"):
            try:
                analysis = json.loads(row["analysis"])
                ct = analysis.get("content_type")
                if ct:
                    content_type_counts[ct] = content_type_counts.get(ct, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass

    return {
        "version": "v1",
        "topics": _normalize(topic_counts),
        "content_types": _normalize(content_type_counts),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bookmark_count": len(rows),
    }


def get_context(profile_path: str | Path) -> str:
    """Load profile from path and return summary string for prompt injection.

    Format: "User interests: AI/Agents (high), Health (medium), ..."
    Returns empty string if profile doesn't exist or has no topics.
    """
    profile = load_profile(profile_path)
    if not profile:
        return ""

    topics = profile.get("topics", {})
    if not topics:
        return ""

    # Sort by weight descending
    sorted_topics = sorted(topics.items(), key=lambda x: x[1], reverse=True)
    interest_strs = [f"{topic} ({_weight_label(weight)})" for topic, weight in sorted_topics]

    if not interest_strs:
        return ""

    return f"User interests: {', '.join(interest_strs)}."


def update_weights(profile_path: str | Path, action: str, category: str) -> dict:
    """Adjust profile topic weights based on callback action.

    Weight increments:
        fc → category += 0.05
        dd → category += 0.03
        sn → category += 0.02
        im → category += 0.04
        rs → category += 0.01
        fs → category += 0.02
        rm → category += 0.02

    Normalizes all topic weights after update. Saves to file.

    Args:
        profile_path: Path to user-profile.json
        action: Action code (fc, dd, sn, im, rs, fs, rm)
        category: Category string to boost

    Returns:
        Updated profile dict
    """
    profile = load_profile(profile_path)
    if not profile:
        profile = _empty_profile()
        profile["generated_at"] = datetime.now(timezone.utc).isoformat()

    delta = ACTION_WEIGHTS.get(action, 0.0)
    if delta == 0.0 or not category:
        return profile

    topics = profile.get("topics", {})
    current = topics.get(category, 0.0)
    topics[category] = current + delta

    # Normalize to keep values in 0-1 range
    profile["topics"] = _normalize(topics)

    save_profile(profile_path, profile)
    return profile


# ============================================================================
# Legacy helpers (used by cmd_profile for bird CLI bookmark analysis)
# ============================================================================

def build_profile_from_bookmarks(bookmarks: list[dict]) -> dict:
    """Structure raw bookmarks for LLM analysis (legacy cmd_profile helper).

    Takes raw bookmark data from bird CLI and structures it into a format
    suitable for LLM to analyze and generate a user profile.
    """
    sample_size = min(50, len(bookmarks))
    sample = bookmarks[:sample_size]

    bookmark_context = []
    for bm in sample:
        context = {
            "text": bm.get("text", "")[:200],
            "author": bm.get("author", {}).get("username", "unknown"),
            "engagement": {
                "likes": bm.get("likes", 0),
                "retweets": bm.get("retweets", 0),
            },
            "is_thread": bm.get("conversation_count", 0) > 1,
            "has_media": bool(bm.get("media", [])),
            "has_urls": bool(bm.get("urls", [])),
        }
        bookmark_context.append(context)

    return {
        "interests": {},
        "bookmark_patterns": {},
        "analysis_preferences": {},
        "raw_sample": bookmark_context,
        "total_bookmarks_analyzed": len(bookmarks),
    }
