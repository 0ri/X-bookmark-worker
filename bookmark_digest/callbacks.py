#!/usr/bin/env python3
"""Button callback handler for bookmark-digest skill.

Supports both compact (q|{code}|{item_id}) and legacy (queue_{action}_{item_id}) formats.
All callbacks perform strict SQLite lookups with status validation.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .bookmark_queue import get_item, mark_acted_on, get_next_batch, set_sending

logger = logging.getLogger(__name__)

# Valid action codes in the button palette
VALID_CODES = {"dd", "im", "fc", "sn", "rm", "fs", "rs", "nb"}

# Action code mapping (long name -> compact code)
ACTION_CODES = {
    "deepdive": "dd",
    "implement": "im",
    "factcheck": "fc",
    "savenotes": "sn",
    "remind": "rm",
    "fullsummary": "fs",
    "readsource": "rs",
    "next_batch": "nb",
}

# Reverse mapping (code -> long name)
CODE_TO_ACTION = {v: k for k, v in ACTION_CODES.items()}


def parse_callback(data: str) -> tuple[str, str]:
    """Parse a callback_data string into (action_code, item_id).

    Supports two formats:
    1. Compact: q|{action_code}|{item_id} (e.g., "q|dd|bk_abc123")
    2. Legacy: queue_{action}_{item_id} (e.g., "queue_deepdive_bk_abc123")

    Args:
        data: Raw callback string from Telegram button press

    Returns:
        Tuple of (action_code, item_id)

    Raises:
        ValueError: If the callback data is invalid or action code is not in palette
    """
    if not data or not isinstance(data, str):
        raise ValueError("Empty or invalid callback data")

    # Try compact format: q|{code}|{id}
    compact_match = re.match(r"^q\|([a-z]{2})\|(.+)$", data)
    if compact_match:
        code = compact_match.group(1)
        item_id = compact_match.group(2)

        if code not in VALID_CODES:
            raise ValueError(f"Unknown action code: {code}")

        return (code, item_id)

    # Try legacy format: queue_{action}_{id}
    # Handle next_batch specially since it contains underscore
    if data.startswith("queue_next_batch_"):
        batch_id = data[len("queue_next_batch_"):]
        if batch_id:
            return ("nb", batch_id)

    # The item_id may contain underscores (e.g., bk_abc123), so we match
    # the action as the first segment after "queue_"
    legacy_match = re.match(r"^queue_([a-z]+)_(bk_.+|rd_.+|yt_.+|gh_.+|hn_.+|kd_.+|nl_.+)$", data)
    if legacy_match:
        action_name = legacy_match.group(1)
        item_id = legacy_match.group(2)

        code = ACTION_CODES.get(action_name)
        if code is None:
            raise ValueError(f"Unknown legacy action: {action_name}")

        return (code, item_id)

    raise ValueError(f"Invalid callback format: {data[:64]}")


def handle_callback(db_path: str, callback_data: str) -> dict:
    """Handle a button callback with strict validation and action dispatch.

    Parses callback data, validates item status, dispatches to handler,
    and returns structured JSON response.

    Args:
        db_path: Path to SQLite queue database
        callback_data: The callback_data string from the button press

    Returns:
        dict with action response (varies by handler)
    """
    # Parse callback
    try:
        code, item_id = parse_callback(callback_data)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    # Dispatch next_batch separately (no item lookup needed)
    if code == "nb":
        return _handle_next_batch(db_path, item_id)

    # SQLite lookup
    item = get_item(db_path, item_id)
    if item is None:
        return {
            "success": False,
            "error": f"Item not found: {item_id}",
            "action": code,
        }

    # Status validation: must be 'delivered' (or 'acted_on' for idempotent re-tap)
    status = item.get("status")
    if status == "acted_on":
        return {
            "success": True,
            "action": code,
            "item_id": item_id,
            "warning": "Already processed",
        }
    if status != "delivered":
        return {
            "success": False,
            "error": f"Item not in delivered state (current: {status})",
            "action": code,
            "item_id": item_id,
        }

    # Mark as acted_on before dispatching
    if not mark_acted_on(db_path, item_id):
        return {
            "success": False,
            "error": f"Failed to mark item as acted_on: {item_id}",
            "action": code,
        }

    # Dispatch to action handler
    handler = _ACTION_HANDLERS.get(code)
    if handler is None:
        return {
            "success": False,
            "error": f"No handler for action code: {code}",
            "action": code,
        }

    return handler(db_path, item_id, item)


# ============================================================================
# Action Handlers
# ============================================================================

def _handle_deep_dive(db_path: str, item_id: str, item: dict) -> dict:
    """Deep Dive: returns JSON indicating a research sub-agent should be spawned."""
    return {
        "success": True,
        "action": "deep_dive",
        "item_id": item_id,
        "url": item.get("canonical_url", ""),
        "title": item.get("title", ""),
        "agent": "research",
        "instructions": f"Deep dive into: {item.get('title', 'bookmark')}",
    }


def _handle_implement(db_path: str, item_id: str, item: dict) -> dict:
    """Implement: returns JSON indicating a coding sub-agent should be spawned."""
    analysis = {}
    if item.get("analysis"):
        try:
            analysis = json.loads(item["analysis"])
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "success": True,
        "action": "implement",
        "item_id": item_id,
        "url": item.get("canonical_url", ""),
        "title": item.get("title", ""),
        "agent": "coding",
        "instructions": f"Implement ideas from: {item.get('title', 'bookmark')}",
        "context": analysis.get("analysis", ""),
    }


def _handle_fact_check(db_path: str, item_id: str, item: dict) -> dict:
    """Fact Check: returns JSON with web search + LLM task spec."""
    analysis = {}
    if item.get("analysis"):
        try:
            analysis = json.loads(item["analysis"])
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "success": True,
        "action": "fact_check",
        "item_id": item_id,
        "query": f"Fact check: {item.get('title', '')} - {analysis.get('analysis', '')[:200]}",
        "tools": ["web_search", "llm-task"],
        "url": item.get("canonical_url", ""),
    }


def _handle_save_notes(db_path: str, item_id: str, item: dict) -> dict:
    """Save Notes: writes item content + analysis to memory/daily/YYYY-MM-DD.md."""
    analysis = {}
    if item.get("analysis"):
        try:
            analysis = json.loads(item["analysis"])
        except (json.JSONDecodeError, TypeError):
            pass

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Build note content
    title = item.get("title", "Untitled bookmark")
    url = item.get("canonical_url", "")
    category = item.get("category", "")
    analysis_text = analysis.get("analysis", "")
    why_bookmarked = analysis.get("why_bookmarked", "")

    note_lines = [
        f"\n## Saved Bookmark: {title}",
        f"- **URL:** {url}" if url else "",
        f"- **Category:** {category}" if category else "",
        f"- **Why bookmarked:** {why_bookmarked}" if why_bookmarked else "",
        f"- **Analysis:** {analysis_text}" if analysis_text else "",
        f"- **Item ID:** {item_id}",
        "",
    ]
    note_content = "\n".join(line for line in note_lines if line or line == "")

    # Write to daily notes file (in data dir alongside queue.db)
    data_dir = Path(db_path).parent
    daily_dir = data_dir / "notes" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    daily_file = daily_dir / f"{today}.md"

    # Idempotency: check if this item is already saved
    if daily_file.exists():
        existing = daily_file.read_text()
        if item_id in existing:
            return {
                "success": True,
                "action": "save_notes",
                "item_id": item_id,
                "file": str(daily_file),
                "message": "Already saved to notes",
            }

    with open(daily_file, "a") as f:
        f.write(note_content)

    return {
        "success": True,
        "action": "save_notes",
        "item_id": item_id,
        "file": str(daily_file),
        "message": f"Saved to {daily_file.name}",
    }


def _handle_remind(db_path: str, item_id: str, item: dict) -> dict:
    """Remind Me: returns JSON with cron job spec."""
    return {
        "success": True,
        "action": "remind",
        "item_id": item_id,
        "title": item.get("title", "Bookmark reminder"),
        "url": item.get("canonical_url", ""),
        "schedule": "tomorrow_9am",
    }


def _handle_full_summary(db_path: str, item_id: str, item: dict) -> dict:
    """Full Summary: returns JSON with fabric/llm-task spec."""
    return {
        "success": True,
        "action": "full_summary",
        "item_id": item_id,
        "url": item.get("canonical_url", ""),
        "title": item.get("title", ""),
        "tool": "fabric",
        "pattern": "extract_wisdom",
    }


def _handle_read_source(db_path: str, item_id: str, item: dict) -> dict:
    """Read Source: returns JSON with web_fetch spec."""
    return {
        "success": True,
        "action": "read_source",
        "item_id": item_id,
        "url": item.get("canonical_url", ""),
        "title": item.get("title", ""),
        "tool": "web_fetch",
    }


def _handle_next_batch(db_path: str, batch_id: str) -> dict:
    """Next Batch: triggers next batch delivery."""
    from .__main__ import format_delivery_message

    batch = get_next_batch(db_path, batch_size=5)

    if not batch:
        return {
            "success": True,
            "action": "next_batch",
            "batch_id": batch_id,
            "delivered": 0,
            "messages": [],
        }

    new_batch_id = batch[0].get("batch_id", batch_id)
    messages = []

    for item in batch:
        item_id = item["id"]
        if set_sending(db_path, item_id):
            msg = format_delivery_message(item)
            messages.append(msg)

    return {
        "success": True,
        "action": "next_batch",
        "batch_id": new_batch_id,
        "delivered": len(messages),
        "messages": messages,
    }


# Handler dispatch table
_ACTION_HANDLERS = {
    "dd": _handle_deep_dive,
    "im": _handle_implement,
    "fc": _handle_fact_check,
    "sn": _handle_save_notes,
    "rm": _handle_remind,
    "fs": _handle_full_summary,
    "rs": _handle_read_source,
    # nb handled separately in handle_callback
}
