#!/usr/bin/env python3
"""
Delivery engine for bookmark digests.

Formats analyzed items into Telegram messages with interactive buttons.
Replaces the legacy digest.py module with LLM-first approach.
"""

import html
import json
import logging
from typing import Any

from .bookmark_queue import get_undelivered, mark_delivered

logger = logging.getLogger(__name__)

# Action code mapping for compact callback format
ACTION_CODES = {
    "deepdive": "dd",
    "implement": "im",
    "factcheck": "fc",
    "savenotes": "sn",
    "remind": "rm",
    "fullsummary": "fs",
    "readsource": "rs",
}

# Reverse mapping for parsing
CODE_TO_ACTION = {v: k for k, v in ACTION_CODES.items()}

# Category emoji mapping
CATEGORY_EMOJI = {
    "ai": "🤖",
    "health": "🏥",
    "productivity": "⚡",
    "tech": "💻",
    "science": "🔬",
    "business": "💼",
    "learning": "📚",
    "tools": "🛠️",
    "news": "📰",
    "entertainment": "🎬",
    "politics": "🏛️",
    "finance": "💰",
    "crypto": "₿",
    "design": "🎨",
    "writing": "✍️",
    "marketing": "📊",
    "social": "👥",
    "philosophy": "🧠",
    "history": "📜",
    "climate": "🌍",
}


def get_category_emoji(category: str) -> str:
    """Get emoji for category, with fuzzy matching.
    
    Args:
        category: Category string from analysis
        
    Returns:
        Emoji string, defaults to 📌 if no match
    """
    if not category:
        return "📌"
    
    category_lower = category.lower()
    
    # Direct match
    if category_lower in CATEGORY_EMOJI:
        return CATEGORY_EMOJI[category_lower]
    
    # Fuzzy match - check if any key is in category
    for key, emoji in CATEGORY_EMOJI.items():
        if key in category_lower:
            return emoji
    
    return "📌"


def get_next_batch(db_path: str, batch_size: int = 5) -> list[dict]:
    """Fetch next batch of analyzed, undelivered items.
    
    Uses bookmark_queue.get_next_batch() which assigns a batch_id.
    
    Args:
        db_path: Path to SQLite database
        batch_size: Number of items to include in batch
        
    Returns:
        List of item dicts with batch_id assigned
    """
    from .bookmark_queue import get_next_batch as _get_next_batch
    return _get_next_batch(db_path, batch_size)


def format_item(item: dict, max_length: int = 4000) -> str:
    """Format a single item for Telegram delivery.
    
    Format:
        Line 1: emoji + category + " — " + title
        Body: analysis summary
        Last line: 🔗 URL
    
    Args:
        item: Queue item dict with analysis JSON
        max_length: Maximum message length (Telegram limit = 4096, use 4000 for safety)
        
    Returns:
        Formatted message string
    """
    # Parse analysis JSON - handle None case
    analysis_str = item.get("analysis")
    if analysis_str is None:
        analysis_str = "{}"
    
    try:
        analysis = json.loads(analysis_str)
    except json.JSONDecodeError:
        logger.warning("Failed to parse analysis for item %s", item.get("id"))
        analysis = {}
    
    # Extract fields (escape user-generated content for HTML parse mode)
    category = html.escape(analysis.get("category", "Uncategorized"))
    title = html.escape(analysis.get("title", item.get("title", "Untitled")))
    summary = html.escape(analysis.get("summary", "No summary available"))
    url = item.get("canonical_url", "")
    
    # Get category emoji
    emoji = get_category_emoji(category)
    
    # Build message parts
    header = f"{emoji} {category} — {title}"
    footer = f"🔗 {url}" if url else ""
    
    # Calculate available space for summary
    header_len = len(header)
    footer_len = len(footer)
    separator_len = 4  # "\n\n" before and after summary
    available = max_length - header_len - footer_len - separator_len
    
    # Truncate summary if needed
    truncated = False
    if len(summary) > available:
        summary = summary[:available - 3] + "..."
        truncated = True
        logger.debug("Truncated summary for item %s to %d chars", item.get("id"), available)
    
    # Assemble message
    parts = [header, "", summary]
    
    # Only add footer if we have a URL
    # For truncated messages, the ellipsis should be visible at the end
    if footer:
        parts.extend(["", footer])
    
    return "\n".join(parts)


def build_button_rows(buttons_json: str, item_id: str) -> list[list[dict]]:
    """Build Telegram button rows from analysis buttons.
    
    Args:
        buttons_json: JSON string of button list from analysis
        item_id: Queue item ID for callback data
        
    Returns:
        List of button rows, max 3 buttons per row
        
    Example:
        [
            [{"text": "🔬 Deep Dive", "callback_data": "q|dd|bk_abc"}],
            [{"text": "💾 Save Notes", "callback_data": "q|sn|bk_abc"}]
        ]
    """
    try:
        buttons = json.loads(buttons_json)
    except json.JSONDecodeError:
        logger.warning("Failed to parse buttons_json for item %s", item_id)
        buttons = []
    
    if not buttons:
        return []
    
    # Build button dicts with compact callback_data
    button_dicts = []
    for btn in buttons:
        text = btn.get("text", "")
        action = btn.get("action", "")
        
        # Get action code (reject unknown actions)
        code = ACTION_CODES.get(action)
        if code is None:
            logger.warning("Unknown action '%s' for item %s, skipping button", action, item_id)
            continue
        
        # Build compact callback: q|{code}|{item_id}
        callback_data = f"q|{code}|{item_id}"
        
        # Validate callback_data length (Telegram limit = 64 bytes)
        if len(callback_data.encode('utf-8')) > 64:
            logger.warning("Callback data too long for item %s action %s: %d bytes", 
                         item_id, action, len(callback_data.encode('utf-8')))
            continue
        
        button_dicts.append({
            "text": text,
            "callback_data": callback_data
        })
    
    # Group into rows of max 3 buttons
    rows = []
    for i in range(0, len(button_dicts), 3):
        row = button_dicts[i:i+3]
        rows.append(row)
    
    return rows


def format_batch_footer(batch_num: int, delivered: int, total: int) -> str:
    """Format footer message for batch delivery.
    
    Args:
        batch_num: Current batch number (1-indexed)
        delivered: Number of items delivered so far
        total: Total number of items in queue
        
    Returns:
        Formatted footer string
    """
    return f"📋 Batch {batch_num} ({delivered} of {total}) — tap below for next batch"


def build_next_batch_button(batch_id: str, remaining: int = 5, batch_size: int = 5) -> list[list[dict]]:
    """Build "Next N" button for batch pagination.
    
    Args:
        batch_id: Batch ID for callback tracking
        remaining: Number of items remaining in queue
        batch_size: Max items per batch
        
    Returns:
        Single-row button list with next-batch callback, or empty if nothing remaining
    """
    if remaining <= 0:
        return []
    next_count = min(remaining, batch_size)
    return [[{
        "text": f"Next {next_count} ▶",
        "callback_data": f"q|nb|{batch_id}"
    }]]
