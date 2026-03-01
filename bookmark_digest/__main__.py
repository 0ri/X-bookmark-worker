#!/usr/bin/env python3
"""
Bookmark Digest CLI - Standalone mode.

This module allows running the bookmark digest as:
    python -m bookmark_digest <command> [options]

Commands:
    fetch       Fetch new bookmarks from Twitter/X
    digest      Generate and display digest
    run         Full pipeline: fetch -> process -> digest
    stats       Show queue statistics
    callback    Handle button callback
    init        Generate config.json from template
    config      Show current configuration

The CLI works standalone (no OpenClaw required), just needs:
- bird CLI (npm install -g bird-cli)
- Twitter auth cookies (AUTH_TOKEN + CT0 env vars)
- Optional: Telegram bot token for sending digests
"""

import argparse
import html as html_mod
import json
import logging
import os
import shutil
import sys
import uuid
from pathlib import Path

from .bookmark_queue import (
    init_db, add_item, get_pending, get_stats,
    store_analyses, reset_failed,
    get_next_batch, set_sending, get_undelivered, save_batch_footer,
    record_error, recover_sending,
)
from .fetcher import fetch_new_bookmarks, mark_processed
from .callbacks import handle_callback
from .config import load_config
from .lock import RunLock, RunLockError
from .profile import build_profile, build_profile_from_bookmarks, save_profile


class RunIdFormatter(logging.Formatter):
    """Custom formatter that includes run_id in log messages."""
    
    def __init__(self, run_id: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.run_id = run_id[:8]  # Use first 8 chars of UUID
    
    def format(self, record):
        # Inject run_id into the record
        record.run_id = self.run_id
        return super().format(record)


def setup_logging(level: str = "INFO", verbose: bool = False, run_id: str = None) -> None:
    """Configure logging for the CLI."""
    if verbose:
        level = "DEBUG"

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    
    # Create custom formatter with run_id
    if run_id:
        formatter = RunIdFormatter(
            run_id,
            fmt="%(asctime)s [run:%(run_id)s] [%(name)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Add new handler with custom formatter
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)


def ensure_dirs(config) -> None:
    """Ensure data directories exist."""
    os.makedirs(config.data_dir, exist_ok=True)
    db_path = os.path.join(config.data_dir, "queue.db")
    init_db(db_path)


def cmd_fetch(args, config) -> int:
    """Fetch new bookmarks from Twitter/X."""
    logger = logging.getLogger("bookmark-digest")

    # Acquire run lock to prevent concurrent execution
    try:
        with RunLock(config.data_dir):
            return _cmd_fetch_impl(args, config)
    except RunLockError as e:
        logger.error(str(e))
        print(f"❌ {e}", file=sys.stderr)
        return 2


def _parse_raw_bookmark(bm: dict) -> dict:
    """Parse a raw bookmark from bird CLI into a queue-ready item dict.

    Deterministic extraction only — no categorization, no summarization.
    The LLM handles all intelligence in the analysis step.

    Args:
        bm: Raw bookmark dict from bird bookmarks --json

    Returns:
        Dict ready for add_item(): source, source_id, canonical_url,
        title, raw_content, engagement
    """
    tweet_id = str(bm.get("id", ""))
    text = bm.get("text", "")
    author = bm.get("author") or {}
    username = author.get("username", "unknown")
    likes = bm.get("likeCount", 0) or 0
    rts = bm.get("retweetCount", 0) or 0
    replies = bm.get("replyCount", 0) or 0

    canonical_url = f"https://x.com/{username}/status/{tweet_id}"
    title = f"@{username} — {text[:100]}" if text else f"@{username}"

    # Build engagement string
    eng_parts = []
    if likes:
        eng_parts.append(f"❤️{likes}")
    if rts:
        eng_parts.append(f"🔁{rts}")
    if replies:
        eng_parts.append(f"💬{replies}")
    engagement = " | ".join(eng_parts) if eng_parts else ""

    raw_content = f"{text}\n\n[Engagement: {engagement}]" if engagement else text

    return {
        "source": "twitter",
        "source_id": tweet_id,
        "canonical_url": canonical_url,
        "title": title,
        "raw_content": raw_content,
        "engagement": engagement,
    }


def _cmd_fetch_impl(args, config) -> int:
    """Implementation of fetch command (called within lock)."""
    logger = logging.getLogger("bookmark-digest")

    ensure_dirs(config)
    state_path = os.path.join(config.data_dir, "bookmark-state.json")
    db_path = os.path.join(config.data_dir, "queue.db")

    limit = args.limit or config.max_bookmarks

    logger.info("Fetching new bookmarks (limit=%d)...", limit)
    bookmarks = fetch_new_bookmarks(state_path, limit=limit, config=config)

    if not bookmarks:
        logger.info("No new bookmarks found.")
        if args.json:
            print(json.dumps({"fetched": 0, "items": []}, indent=2))
        else:
            print("No new bookmarks.")
        return 0

    logger.info("Inserting %d new bookmarks into queue...", len(bookmarks))
    inserted = []
    processed_ids = []

    for bm in bookmarks:
        try:
            item = _parse_raw_bookmark(bm)
            item_id = add_item(db_path, item)
            if item_id:
                item["id"] = item_id
                inserted.append(item)
                logger.info("  + %s: %s", item_id, item.get("title", "?")[:60])
            else:
                logger.debug("  = duplicate: %s", bm.get("id"))
            processed_ids.append(str(bm.get("id", "")))
        except Exception as e:
            logger.error("  x Error inserting %s: %s", bm.get("id"), e, exc_info=args.verbose)
            processed_ids.append(str(bm.get("id", "")))

    # Mark all fetched IDs as processed
    mark_processed(state_path, processed_ids, max_ids=config.max_processed_ids)
    logger.info("Inserted %d/%d bookmarks", len(inserted), len(bookmarks))

    if args.json:
        print(json.dumps({
            "fetched": len(bookmarks),
            "inserted": len(inserted),
            "items": inserted
        }, indent=2))
    else:
        print(f"Fetched {len(bookmarks)}, inserted {len(inserted)} new bookmarks.")

    return 0


def cmd_stats(args, config) -> int:
    """Show queue statistics."""
    ensure_dirs(config)
    db_path = os.path.join(config.data_dir, "queue.db")

    stats = get_stats(db_path)
    total = sum(stats.values())

    if args.json:
        print(json.dumps({"total": total, "stats": stats}, indent=2))
    else:
        print(f"\nQueue Stats ({total} total):")
        for status, count in sorted(stats.items()):
            print(f"  {status}: {count}")
        print()

    return 0


def cmd_callback(args, config) -> int:
    """Handle button callback."""
    logger = logging.getLogger("bookmark-digest")

    ensure_dirs(config)
    db_path = os.path.join(config.data_dir, "queue.db")

    callback_data = args.callback_data
    logger.info("Handling callback: %s", callback_data)

    result = handle_callback(db_path, callback_data)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["response_text"])
        if result.get("next_action") != "none":
            print(f"Next action: {result['next_action']}")

    return 0


def cmd_init(args, config) -> int:
    """Generate config.json from template."""
    dest = Path(config.skill_dir) / "config.json"
    src = Path(config.skill_dir) / "config.example.json"

    if dest.exists() and not args.force:
        print(f"config.json already exists at {dest}")
        print("Use --force to overwrite.")
        return 1

    if src.exists():
        shutil.copy2(src, dest)
        print(f"Created config.json from config.example.json")
    else:
        # Generate from current defaults
        from .config import DEFAULT_CATEGORIES, DEFAULT_ACTIONS, DEFAULT_BUTTONS
        template = {
            "categories": DEFAULT_CATEGORIES,
            "digest": {
                "max_items": config.max_digest_items,
                "show_engagement": True,
                "show_urls": True,
            },
            "fetch": {
                "max_bookmarks": config.max_bookmarks,
                "dedup_window": config.max_processed_ids,
            },
            "engagement_threshold": config.engagement_threshold,
            "default_buttons": DEFAULT_BUTTONS,
            "actions": DEFAULT_ACTIONS,
        }
        dest.write_text(json.dumps(template, indent=2, ensure_ascii=False) + "\n")
        print(f"Created config.json with defaults at {dest}")

    print("Edit config.json to customize categories, buttons, and actions.")
    return 0


def cmd_config(args, config) -> int:
    """Show current configuration."""
    d = config.to_dict()
    if args.json:
        print(json.dumps(d, indent=2, ensure_ascii=False))
    else:
        print("Current configuration:")
        print(f"  Skill dir:    {config.skill_dir}")
        print(f"  Data dir:     {config.data_dir}")
        print(f"  Bird CLI:     {config.bird_cli}")
        print(f"  Log level:    {config.log_level}")
        print(f"  Max bookmarks: {config.max_bookmarks}")
        print(f"  Max digest:   {config.max_digest_items}")
        print(f"  Engagement:   {config.engagement_threshold}")
        print(f"  Categories:   {', '.join(config.categories.keys())}")
        print(f"  Actions:      {', '.join(sorted(config.actions.keys()))}")
    return 0


def cmd_profile(args, config) -> int:
    """Build user profile from recent bookmarks."""
    logger = logging.getLogger("bookmark-digest")
    
    ensure_dirs(config)
    
    # Resolve profile path (can be relative to skill_dir)
    profile_path = Path(config.profile_path)
    if not profile_path.is_absolute():
        profile_path = Path(config.skill_dir) / profile_path
    
    # Fetch recent bookmarks
    limit = args.limit or 200
    logger.info("Fetching last %d bookmarks for profile analysis...", limit)
    
    try:
        # Use bird CLI directly to fetch bookmarks
        import subprocess
        cmd = [config.bird_cli, "bookmarks", "--json", "-n", str(limit)]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.bird_timeout,
            check=True,
        )
        
        bookmarks = json.loads(result.stdout)
        logger.info("Fetched %d bookmarks", len(bookmarks))
        
    except subprocess.CalledProcessError as e:
        logger.error("Failed to fetch bookmarks: %s", e)
        print(f"❌ Failed to fetch bookmarks from bird CLI", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        logger.error("bird CLI timeout after %d seconds", config.bird_timeout)
        print(f"❌ bird CLI timed out", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        logger.error("Failed to parse bird CLI output: %s", e)
        print(f"❌ Invalid JSON from bird CLI", file=sys.stderr)
        return 1
    
    if not bookmarks:
        logger.warning("No bookmarks found")
        print("No bookmarks to analyze for profile.")
        return 0
    
    # Build structured profile data (legacy: from raw bookmarks)
    profile_data = build_profile_from_bookmarks(bookmarks)
    
    if args.dry_run:
        # Just show the structured data that would be sent to LLM
        logger.info("Dry run: showing profile structure (not saving)")
        print(json.dumps(profile_data, indent=2, ensure_ascii=False))
        print(f"\n📋 Next step: Send this to LLM to analyze and fill in interests/patterns/preferences")
        print(f"    Profile would be saved to: {profile_path}")
        return 0
    
    # In actual usage, an LLM would analyze this and fill in the template
    # For now, just save the structure as a starting template
    logger.info("Saving profile template to %s", profile_path)
    save_profile(profile_path, profile_data)
    
    print(f"✅ Profile template saved to {profile_path}")
    print(f"📋 Analyzed {len(bookmarks)} bookmarks")
    print(f"\n💡 Next steps:")
    print(f"   1. This is a template - use an LLM to analyze raw_sample and fill:")
    print(f"      - interests: {{topic: weight}}")
    print(f"      - bookmark_patterns: {{pattern: value}}")
    print(f"      - analysis_preferences: {{pref: value}}")
    print(f"   2. Edit {profile_path} manually or via LLM")
    print(f"   3. Profile will be used to personalize bookmark analysis")
    
    return 0


# ============================================================================
# v2 Delivery Formatting (Session 4)
# ============================================================================

# Telegram rate limit: seconds between messages in batch delivery
TELEGRAM_RATE_LIMIT = 1.0

# Max message length before truncation (Telegram limit is 4096)
MAX_MESSAGE_LENGTH = 4000

BUTTON_LABELS = {
    "dd": "🔬 Deep Dive",
    "im": "⚡ Implement",
    "fc": "📊 Fact Check",
    "sn": "💾 Save",
    "rm": "⏰ Remind",
    "sk": "⏭ Skip",
}

# dd and fc are mutually exclusive — never show both
MUTUALLY_EXCLUSIVE = {"dd", "fc"}

CATEGORY_EMOJIS = {
    "ai": "🤖",
    "health": "💊",
    "programming": "💻",
    "startups": "🚀",
    "tech": "⚡",
    "science": "🔬",
    "finance": "💰",
    "design": "🎨",
    "crypto": "₿",
    "productivity": "📈",
}


def _get_category_emoji(category: str) -> str:
    """Get emoji for a category, matching on substring."""
    cat_lower = category.lower()
    for key, emoji in CATEGORY_EMOJIS.items():
        if key in cat_lower:
            return emoji
    return "📌"


def format_delivery_message(item: dict) -> dict:
    """Format a queue item into a Telegram-ready message dict.

    Args:
        item: Queue item dict with analysis, buttons_json, category, etc.

    Returns:
        Dict with item_id, text, buttons (inline keyboard rows), category
    """
    item_id = item["id"]
    category = item.get("category", "Uncategorized")
    title = item.get("title", "")
    url = item.get("canonical_url", "")

    # Parse analysis blob (stored as JSON string of the full analysis dict)
    analysis_data = {}
    if item.get("analysis"):
        try:
            analysis_data = json.loads(item["analysis"])
        except (json.JSONDecodeError, TypeError):
            analysis_data = {}

    analysis_text = analysis_data.get("analysis", "")
    why_bookmarked = analysis_data.get("why_bookmarked", "")

    # Escape user-generated content for HTML parse mode
    category = html_mod.escape(category)
    title = html_mod.escape(title)
    analysis_text = html_mod.escape(analysis_text)
    why_bookmarked = html_mod.escape(why_bookmarked)

    # Build message text
    emoji = _get_category_emoji(category)
    parts = [f"{emoji} {category}"]
    if title:
        parts.append(f"<b>{title}</b>")
    parts.append("")  # blank line
    if analysis_text:
        parts.append(analysis_text)
    if why_bookmarked:
        parts.append(f"\n💡 {why_bookmarked}")
    if url:
        parts.append(f"\n🔗 {url}")

    text = "\n".join(parts)

    # Truncate if over limit (preserve valid HTML by not cutting mid-tag)
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[:MAX_MESSAGE_LENGTH - 3] + "..."

    parse_mode = "HTML"

    # Parse button codes
    button_codes = ["dd"]  # default
    if item.get("buttons_json"):
        try:
            parsed = json.loads(item["buttons_json"])
            if parsed:
                button_codes = parsed
        except (json.JSONDecodeError, TypeError):
            pass

    # Filter: only keep known buttons, drop removed codes (fs, rs)
    button_codes = [c for c in button_codes if c in BUTTON_LABELS]
    if not button_codes:
        button_codes = ["dd"]

    # Enforce mutual exclusivity: dd and fc never together
    # If both present, keep whichever came first
    has_exclusive = [c for c in button_codes if c in MUTUALLY_EXCLUSIVE]
    if len(has_exclusive) > 1:
        keep = has_exclusive[0]
        button_codes = [c for c in button_codes if c not in MUTUALLY_EXCLUSIVE or c == keep]

    # Build inline keyboard rows (2 buttons per row)
    keyboard = []
    row = []
    for code in button_codes:
        label = BUTTON_LABELS.get(code, code)
        row.append({
            "text": label,
            "callback_data": f"q|{code}|{item_id}",
        })
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    return {
        "item_id": item_id,
        "text": text,
        "parse_mode": parse_mode,
        "buttons": keyboard,
        "category": category,
    }


def build_analysis_prompt(items: list[dict], profile_context: str = "") -> str:
    """Build the LLM prompt for bookmark analysis with few-shot examples.

    Args:
        items: List of bookmark items from fetch output
        profile_context: Optional user profile context string

    Returns:
        Prompt string for llm-task
    """
    profile_section = ""
    if profile_context:
        profile_section = f"""
## User Profile
{profile_context}
Use this profile to inform your category choices, relevance scores, and why_bookmarked inferences.
"""

    few_shot_1 = json.dumps({
        "item_id": "bk_example1",
        "category": "Health/Supplements",
        "why_bookmarked": "User tracks supplement research, especially with peer-reviewed backing",
        "analysis": "Thread claims creatine loading (20g single dose) improves cognitive processing speed by 24.5%. The cited study (Watanabe et al. 2002) is real but small (n=45). The specific percentage claim needs verification against the actual paper.",
        "relevance_score": 0.8,
        "content_type": "thread",
        "buttons": ["fc", "sn"],
        "needs_enrichment": False,
        "enrichment_urls": []
    }, indent=2)

    few_shot_2 = json.dumps({
        "item_id": "bk_example2",
        "category": "AI/Agents",
        "why_bookmarked": "User follows AI tooling developments closely",
        "analysis": "New AI coding agent that can autonomously edit files, run tests, and commit. Represents a significant step in agentic coding. The error handling and retry loop is the key differentiator from simpler approaches.",
        "relevance_score": 0.9,
        "content_type": "tweet",
        "buttons": ["dd", "im"],
        "needs_enrichment": True,
        "enrichment_urls": ["https://example.com/ai-agent"]
    }, indent=2)

    return f"""Analyze these bookmarks saved by a user from Twitter/X. For each bookmark, provide a structured analysis.
{profile_section}
For each bookmark, determine:
1. **category**: A dynamic category like "AI/Agents", "Health/Supplements", "Programming/Rust", etc.
2. **why_bookmarked**: Your inference of why the user saved this (max 200 chars)
3. **analysis**: 2-4 sentence deep analysis covering key takeaway, relevance, and actionability (max 1000 chars). If the bookmark contains URLs to articles/repos/tools, describe what the linked content is about — the pipeline will proactively fetch and summarize all linked URLs.
4. **relevance_score**: How relevant to the user's interests (0.0 = noise, 1.0 = must-act)
5. **content_type**: One of: tweet, thread, article, video, repo, paper, tool, other
6. **buttons**: Select 1-3 action buttons from the palette below
7. **needs_enrichment**: true if the linked URL should be fetched for deeper analysis (DEFAULT: true if any URL is present in the content)
8. **enrichment_urls**: URLs to fetch if needs_enrichment is true

## Button Palette (pick 1-3, no more)
- dd (Deep Dive): Complex, multi-faceted topics worth researching broadly
- im (Implement): Actionable ideas, tools, or techniques to try
- fc (Fact Check): A SINGLE specific verifiable claim (health, political, statistical)
- sn (Save): Reference material worth bookmarking permanently
- rm (Remind): Habits, routines, or time-sensitive items

## CRITICAL BUTTON RULES
- **dd and fc are MUTUALLY EXCLUSIVE** — never assign both to the same item
  - Use fc when there is ONE specific claim to verify (e.g. "Study says X causes Y")
  - Use dd when the topic is complex and needs broader exploration
- **Do NOT use "rs" or "fs"** — those codes are removed. Linked content is fetched automatically.
- Most items need only 2 buttons. 3 is the max.

## Few-Shot Examples

### Example 1: Health claim (single verifiable claim -> fc, NOT dd)
Input: {{"id": "bk_example1", "title": "Creatine study thread", "raw_content": "Thread: Creatine loading (20g single dose) improves cognitive processing speed by 24.5%. Study: Watanabe et al. 2002, n=45."}}
Output:
{few_shot_1}

### Example 2: AI tool (complex topic -> dd, NOT fc)
Input: {{"id": "bk_example2", "title": "New AI coding agent", "raw_content": "Just shipped: Claude Code can now edit files, run tests, and git commit autonomously. The agentic loop handles errors and retries."}}
Output:
{few_shot_2}

## Your Task
Analyze the following {len(items)} bookmark(s) and return a JSON object matching the provided schema. Use each bookmark's "id" field as the "item_id" in your response."""


# ============================================================================
# v2 Pipeline Subcommands (Session 3)
# ============================================================================

def cmd_store_analyses(args, config) -> int:
    """Store llm-task analysis results into the queue DB.

    Reads JSON from stdin with format: {"analyses": [...]}
    Only updates items with status='pending' (idempotent).
    """
    ensure_dirs(config)
    db_path = os.path.join(config.data_dir, "queue.db")

    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON on stdin: {e}"}), file=sys.stderr)
        return 1

    analyses = data.get("analyses", [])
    if not analyses:
        print(json.dumps({"stored": 0, "total_input": 0}))
        return 0

    count = store_analyses(db_path, analyses)
    print(json.dumps({"stored": count, "total_input": len(analyses)}))
    return 0


def cmd_build_llm_task_request(args, config) -> int:
    """Build llm-task request JSON from fetch output.

    Reads JSON from stdin (output of fetch step), loads the JSON Schema,
    optionally loads user profile, and outputs a complete llm-task request
    with prompt, input data, and schema.
    """
    ensure_dirs(config)

    # Read stdin (fetch output)
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON on stdin: {e}"}), file=sys.stderr)
        return 1

    # Accept both "items" (from fetch) and "bookmarks" keys
    items = data.get("items", data.get("bookmarks", []))

    # Load JSON Schema
    schema_path = Path(__file__).parent.parent / "schemas" / "bookmark-analysis-v1.json"
    try:
        schema = json.loads(schema_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(json.dumps({"error": f"Failed to load schema: {e}"}), file=sys.stderr)
        return 1

    # Load user profile (optional)
    profile_context = ""
    profile_path = Path(config.profile_path)
    if not profile_path.is_absolute():
        profile_path = Path(config.skill_dir) / profile_path
    if profile_path.exists():
        from .profile import get_context
        profile_context = get_context(str(profile_path))

    # Build prompt with few-shot examples
    prompt = build_analysis_prompt(items, profile_context)

    output = {
        "prompt": prompt,
        "input": {"bookmarks": items},
        "schema": schema,
    }

    print(json.dumps(output, indent=2))
    return 0


def cmd_enrich(args, config) -> int:
    """Enrich analyzed items with web content (placeholder).

    In Session 4 this will use web_fetch to pull linked content
    and re-analyze with full context. For now, passes through.
    """
    ensure_dirs(config)
    db_path = os.path.join(config.data_dir, "queue.db")
    batch_size = args.batch_size or 5

    # Read stdin if available (piped from store-analyses)
    input_data = {}
    if not sys.stdin.isatty():
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            pass

    print(json.dumps({
        "enriched": 0,
        "skipped": 0,
        "batch_size": batch_size,
        "status": "placeholder",
    }))
    return 0


def cmd_deliver_v2(args, config) -> int:
    """Deliver analyzed items to Telegram via two-phase protocol.

    1. Gets next batch of analyzed items (assigns batch_id atomically)
    2. For each item: set status='sending' -> format message with buttons
    3. Outputs JSON with formatted messages for the caller to send to Telegram
    4. Caller sends to Telegram, gets message IDs, calls mark_delivered_with_message

    Idempotent: items already in sending/delivered state are skipped.
    """
    ensure_dirs(config)
    db_path = os.path.join(config.data_dir, "queue.db")
    batch_size = args.batch_size or 5

    # Recover any items stuck in 'sending' from a crashed previous run
    recovered = recover_sending(db_path)
    if recovered:
        logger = logging.getLogger("bookmark-digest")
        logger.info("Recovered %d items stuck in 'sending' state", recovered)

    # Get next batch (atomically assigns batch_id to analyzed items)
    batch = get_next_batch(db_path, batch_size)

    if not batch:
        remaining = len(get_undelivered(db_path))
        print(json.dumps({
            "delivered": 0,
            "remaining": remaining,
            "batch_id": None,
            "messages": [],
        }))
        return 0

    batch_id = batch[0]["batch_id"]
    messages = []

    for item in batch:
        item_id = item["id"]

        # Phase 1: transition analyzed -> sending
        if not set_sending(db_path, item_id):
            continue  # Skip if not in analyzed state (idempotency guard)

        # Format message with buttons
        msg = format_delivery_message(item)
        messages.append(msg)

    # Count remaining undelivered items (for future batches)
    remaining = len(get_undelivered(db_path))

    # Build batch footer (only show "Next" button if more items remain)
    next_count = min(remaining, batch_size)
    footer = {
        "text": f"📚 Batch complete — {len(messages)} items analyzed, {remaining} remaining",
        "parse_mode": "HTML",
    }
    if remaining > 0:
        footer["buttons"] = [[{
            "text": f"Next {next_count} ▶",
            "callback_data": f"q|nb|{batch_id}",
        }]]

    print(json.dumps({
        "delivered": len(messages),
        "remaining": remaining,
        "batch_id": batch_id,
        "messages": messages,
        "footer": footer,
    }))
    return 0


def cmd_callback_v2(args, config) -> int:
    """Handle button callback in v2 JSON mode with full action dispatch.

    Constructs compact callback data from --action and --item-id args,
    then delegates to the callback handler for parsing, validation, and dispatch.
    """
    ensure_dirs(config)
    db_path = os.path.join(config.data_dir, "queue.db")

    action = args.action
    item_id = args.item_id

    # Build compact callback format and delegate to handler
    callback_data = f"q|{action}|{item_id}"
    result = handle_callback(db_path, callback_data)

    print(json.dumps(result, indent=2))
    return 0 if result.get("success") else 1


def cmd_profile_v2(args, config) -> int:
    """Profile handler for v2 pipeline.

    --context: output profile context string as JSON
    --rebuild: rebuild profile from queue DB bookmarks
    (default): load and output current profile
    """
    ensure_dirs(config)

    from .profile import get_context, load_profile, save_profile

    # Resolve profile path
    profile_path = Path(config.profile_path)
    if not profile_path.is_absolute():
        profile_path = Path(config.skill_dir) / profile_path

    if args.context:
        context = get_context(str(profile_path))
        print(json.dumps({"context": context}))
        return 0

    if args.rebuild:
        db_path = os.path.join(config.data_dir, "queue.db")
        limit = getattr(args, "limit", None) or 200
        profile = build_profile(db_path, limit=limit)
        save_profile(str(profile_path), profile)
        print(json.dumps(profile, indent=2))
        return 0

    # Default: load and output current profile
    profile = load_profile(str(profile_path))
    if not profile:
        print(json.dumps({"error": "No profile found", "path": str(profile_path)}))
        return 1
    print(json.dumps(profile, indent=2))
    return 0


def cmd_reset_failed(args, config) -> int:
    """Reset failed items back to pending."""
    ensure_dirs(config)
    db_path = os.path.join(config.data_dir, "queue.db")

    count = reset_failed(db_path)
    print(json.dumps({"reset": count}))
    return 0


def cmd_fetch_and_prep(args, config) -> int:
    """Fetch new bookmarks and build llm-task request in one step.

    1. Fetch new bookmarks (same as fetch)
    2. Insert into DB as pending
    3. Load all pending items from DB
    4. Build the llm-task request using build_analysis_prompt()
    5. Output the complete llm-task request JSON to stdout
    """
    logger = logging.getLogger("bookmark-digest")

    try:
        with RunLock(config.data_dir):
            ensure_dirs(config)
            state_path = os.path.join(config.data_dir, "bookmark-state.json")
            db_path = os.path.join(config.data_dir, "queue.db")

            limit = args.limit or config.max_bookmarks

            # Step 1-2: Fetch and insert
            logger.info("Fetching new bookmarks (limit=%d)...", limit)
            bookmarks = fetch_new_bookmarks(state_path, limit=limit, config=config)

            inserted_count = 0
            processed_ids = []

            if bookmarks:
                for bm in bookmarks:
                    try:
                        item = _parse_raw_bookmark(bm)
                        item_id = add_item(db_path, item)
                        if item_id:
                            inserted_count += 1
                            logger.info("  + %s: %s", item_id, item.get("title", "?")[:60])
                        processed_ids.append(str(bm.get("id", "")))
                    except Exception as e:
                        logger.error("  x Error inserting %s: %s", bm.get("id"), e)
                        processed_ids.append(str(bm.get("id", "")))

                mark_processed(state_path, processed_ids, max_ids=config.max_processed_ids)
                logger.info("Inserted %d/%d bookmarks", inserted_count, len(bookmarks))

            # Step 3: Load all pending items
            pending = get_pending(db_path, limit=500)

            if not pending:
                logger.info("No pending items to analyze.")
                print(json.dumps({
                    "fetched": len(bookmarks) if bookmarks else 0,
                    "inserted": inserted_count,
                    "pending": 0,
                    "prompt": None,
                }))
                return 0

            # Step 4: Build llm-task request
            # Load user profile (optional)
            profile_context = ""
            profile_path = Path(config.profile_path)
            if not profile_path.is_absolute():
                profile_path = Path(config.skill_dir) / profile_path
            if profile_path.exists():
                from .profile import get_context
                profile_context = get_context(str(profile_path))

            # Build items for the prompt (use queue item format)
            prompt_items = []
            for item in pending:
                prompt_items.append({
                    "id": item["id"],
                    "title": item.get("title", ""),
                    "raw_content": item.get("raw_content", ""),
                    "canonical_url": item.get("canonical_url", ""),
                    "engagement": item.get("engagement", ""),
                })

            prompt = build_analysis_prompt(prompt_items, profile_context)

            # Load JSON Schema
            schema_path = Path(__file__).parent.parent / "schemas" / "bookmark-analysis-v1.json"
            schema = {}
            try:
                schema = json.loads(schema_path.read_text())
            except (FileNotFoundError, json.JSONDecodeError) as e:
                logger.warning("Failed to load schema: %s", e)

            # Step 5: Output complete request
            output = {
                "fetched": len(bookmarks) if bookmarks else 0,
                "inserted": inserted_count,
                "pending": len(pending),
                "prompt": prompt,
                "input": {"bookmarks": prompt_items},
                "schema": schema,
            }

            print(json.dumps(output, indent=2))
            return 0

    except RunLockError as e:
        logger.error(str(e))
        print(f"❌ {e}", file=sys.stderr)
        return 2


def cmd_analyze_and_deliver(args, config) -> int:
    """Store analysis results and deliver to Telegram in one step.

    Reads llm-task analysis JSON from stdin, then:
    1. Parse the analysis JSON
    2. Call store_analyses() to store results and transition pending→analyzed
    3. Run the deliver logic (same as cmd_deliver_v2)
    4. Output summary JSON
    """
    logger = logging.getLogger("bookmark-digest")

    ensure_dirs(config)
    db_path = os.path.join(config.data_dir, "queue.db")
    batch_size = args.batch_size or 5

    # Step 1: Parse analysis JSON from stdin
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON on stdin: {e}"}), file=sys.stderr)
        return 1

    # Step 2: Store analyses
    analyses = data.get("analyses", [])
    stored = 0
    if analyses:
        stored = store_analyses(db_path, analyses)
        logger.info("Stored %d/%d analyses", stored, len(analyses))

    # Step 3: Deliver (same logic as cmd_deliver_v2)
    recovered = recover_sending(db_path)
    if recovered:
        logger.info("Recovered %d items stuck in 'sending' state", recovered)

    batch = get_next_batch(db_path, batch_size)

    if not batch:
        remaining = len(get_undelivered(db_path))
        print(json.dumps({
            "stored": stored,
            "delivered": 0,
            "remaining": remaining,
            "batch_id": None,
            "messages": [],
        }))
        return 0

    batch_id = batch[0]["batch_id"]
    messages = []

    for item in batch:
        item_id = item["id"]
        if not set_sending(db_path, item_id):
            continue
        msg = format_delivery_message(item)
        messages.append(msg)

    remaining = len(get_undelivered(db_path))

    next_count = min(remaining, batch_size)
    footer = {
        "text": f"📚 Batch complete — {len(messages)} items analyzed, {remaining} remaining",
        "parse_mode": "HTML",
    }
    if remaining > 0:
        footer["buttons"] = [[{
            "text": f"Next {next_count} ▶",
            "callback_data": f"q|nb|{batch_id}",
        }]]

    # Step 4: Output summary
    print(json.dumps({
        "stored": stored,
        "delivered": len(messages),
        "remaining": remaining,
        "batch_id": batch_id,
        "messages": messages,
        "footer": footer,
    }))
    return 0


def cmd_fix_stuck(args, config) -> int:
    """Fix items stuck in transitional states.

    Resets:
    - 'queued' items → 'pending' (v1 legacy state)
    - 'sending' items → 'analyzed' (crashed delivery)
    - 'triaged' items → 'completed' (v1 already-shown items)

    Reports what it did.
    """
    ensure_dirs(config)
    db_path = os.path.join(config.data_dir, "queue.db")
    now = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat()

    results = {}

    from .bookmark_queue import _get_connection

    with _get_connection(db_path) as conn:
        # Reset queued → pending
        cur = conn.execute(
            "UPDATE queue SET status = 'pending', updated_at = ? WHERE status = 'queued'",
            (now,)
        )
        results["queued_to_pending"] = cur.rowcount

        # Recover sending → analyzed
        cur = conn.execute(
            "UPDATE queue SET status = 'analyzed', updated_at = ? WHERE status = 'sending'",
            (now,)
        )
        results["sending_to_analyzed"] = cur.rowcount

        # Mark triaged → completed
        cur = conn.execute(
            "UPDATE queue SET status = 'completed', completed_at = ?, updated_at = ? WHERE status = 'triaged'",
            (now, now)
        )
        results["triaged_to_completed"] = cur.rowcount

        conn.commit()

    total = sum(results.values())
    results["total_fixed"] = total

    if args.json:
        print(json.dumps(results))
    else:
        if total == 0:
            print("No stuck items found.")
        else:
            print(f"Fixed {total} stuck items:")
            if results["queued_to_pending"]:
                print(f"  queued → pending: {results['queued_to_pending']}")
            if results["sending_to_analyzed"]:
                print(f"  sending → analyzed: {results['sending_to_analyzed']}")
            if results["triaged_to_completed"]:
                print(f"  triaged → completed: {results['triaged_to_completed']}")

    return 0


def main():
    """Main CLI entry point."""
    # Generate run_id for this invocation
    run_id = str(uuid.uuid4())
    
    parser = argparse.ArgumentParser(
        prog="bookmark-digest",
        description="Twitter/X bookmark digest tool with AI categorization",
        epilog="Requires bird CLI and Twitter auth cookies (AUTH_TOKEN + CT0 env vars)",
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging (DEBUG level)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and categorize but don't send or mark as triaged",
    )
    parser.add_argument(
        "--config", "-C",
        type=str,
        dest="config_path",
        help="Path to config.json (default: config.json in skill directory)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force overwrite (for init command)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # fetch command
    fetch_parser = subparsers.add_parser("fetch", help="Fetch new bookmarks from Twitter/X")
    fetch_parser.add_argument(
        "--limit", "-n",
        type=int,
        help="Max bookmarks to fetch (default: from config)",
    )

    # stats command
    subparsers.add_parser("stats", help="Show queue statistics")

    # callback command
    callback_parser = subparsers.add_parser("callback", help="Handle button callback")
    callback_parser.add_argument(
        "callback_data",
        type=str,
        help="Callback data string (e.g., queue_skip_bk_abc123)",
    )

    # init command
    subparsers.add_parser("init", help="Generate config.json from template")

    # config command
    subparsers.add_parser("config", help="Show current configuration")

    # profile command (legacy)
    profile_parser = subparsers.add_parser("profile", help="Build user profile from recent bookmarks")
    profile_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=200,
        help="Number of recent bookmarks to analyze (default: 200)",
    )

    # ---- v2 pipeline subcommands ----

    # build-llm-task-request: reads fetch output from stdin, outputs llm-task request
    subparsers.add_parser("build-llm-task-request", help="Build llm-task request JSON (reads fetch output from stdin)")

    # store-analyses: reads JSON from stdin
    subparsers.add_parser("store-analyses", help="Store llm-task analysis results (reads JSON from stdin)")

    # enrich: placeholder for web content enrichment
    enrich_parser = subparsers.add_parser("enrich", help="Enrich items with web content (placeholder)")
    enrich_parser.add_argument("--batch-size", type=int, help="Batch size (default: 5)")

    # deliver: placeholder for Telegram delivery
    deliver_parser = subparsers.add_parser("deliver", help="Deliver items to Telegram (placeholder)")
    deliver_parser.add_argument("--batch-size", type=int, help="Batch size (default: 5)")

    # callback v2: with --action and --item-id
    callback_v2_parser = subparsers.add_parser("callback-v2", help="Handle button callback (v2 JSON)")
    callback_v2_parser.add_argument("--action", required=True, help="Action code (dd, im, fc, sn, rm, fs, rs)")
    callback_v2_parser.add_argument("--item-id", required=True, help="Queue item ID")

    # profile v2: with --context and --rebuild
    profile_v2_parser = subparsers.add_parser("profile-v2", help="Profile handler (v2 JSON)")
    profile_v2_parser.add_argument("--context", action="store_true", help="Output profile as JSON context")
    profile_v2_parser.add_argument("--rebuild", action="store_true", help="Rebuild profile from scratch")
    profile_v2_parser.add_argument("--limit", "-n", type=int, help="Bookmarks to analyze for rebuild (default: 200)")

    # reset-failed
    subparsers.add_parser("reset-failed", help="Reset failed items to pending")

    # fetch-and-prep: combined fetch + build-llm-task-request
    fap_parser = subparsers.add_parser("fetch-and-prep", help="Fetch bookmarks and build llm-task request")
    fap_parser.add_argument("--limit", "-n", type=int, help="Max bookmarks to fetch (default: from config)")

    # analyze-and-deliver: combined store-analyses + deliver
    aad_parser = subparsers.add_parser("analyze-and-deliver", help="Store analyses from stdin and deliver to Telegram")
    aad_parser.add_argument("--batch-size", type=int, help="Batch size (default: 5)")

    # fix-stuck: reset transitional states
    subparsers.add_parser("fix-stuck", help="Fix items stuck in transitional states")

    args = parser.parse_args()

    # Load configuration
    config = load_config(config_path=getattr(args, "config_path", None))

    # Setup logging with run_id
    log_level = os.environ.get("LOG_LEVEL", config.log_level)
    setup_logging(level=log_level, verbose=args.verbose, run_id=run_id)

    # Default to 'stats' if no command specified
    if args.command is None:
        args.command = "stats"

    # Dispatch to command handler
    commands = {
        "fetch": cmd_fetch,
        "stats": cmd_stats,
        "callback": cmd_callback,
        "init": cmd_init,
        "config": cmd_config,
        "profile": cmd_profile,
        # v2 pipeline subcommands
        "build-llm-task-request": cmd_build_llm_task_request,
        "store-analyses": cmd_store_analyses,
        "enrich": cmd_enrich,
        "deliver": cmd_deliver_v2,
        "callback-v2": cmd_callback_v2,
        "profile-v2": cmd_profile_v2,
        "reset-failed": cmd_reset_failed,
        "fetch-and-prep": cmd_fetch_and_prep,
        "analyze-and-deliver": cmd_analyze_and_deliver,
        "fix-stuck": cmd_fix_stuck,
    }

    handler = commands.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    try:
        return handler(args, config)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.", file=sys.stderr)
        return 130
    except Exception as e:
        logger = logging.getLogger("bookmark-digest")
        logger.error("Fatal error: %s", e, exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    sys.exit(main())
