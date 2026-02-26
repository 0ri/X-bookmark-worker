#!/usr/bin/env python3
"""Content extraction + categorization for bookmarks."""

import json
import logging
import re
import textwrap

from .bird import run_bird

logger = logging.getLogger(__name__)


# ── Heuristic content summarization ──────────────────────────────

_NOISE_RE = re.compile(
    r'(?:^|\s)(?:@\w+|https?://\S+|RT\b)',  # @mentions, URLs, RT prefix
)
_SECTION_HEADING_RE = re.compile(
    r'^(?:\d+[\.\)]\s*|[-•]\s*|[A-Z][A-Z ]{2,}:)',  # "1. Foo", "- Foo", "SECTION:"
    re.MULTILINE,
)
_STAT_RE = re.compile(r'\d+[%xX+]|\$\d|\d{3,}')  # numbers that signal data/stats
_SENTENCE_END_RE = re.compile(r'[.!?]\s+|[.!?]$')


def _clean_line(line: str) -> str:
    """Strip @mentions and bare URLs from a line, collapse whitespace."""
    cleaned = _NOISE_RE.sub(' ', line).strip()
    return re.sub(r'\s{2,}', ' ', cleaned)


def _first_meaningful_sentence(text: str) -> str:
    """Extract the first sentence that contains real words (not just mentions/links)."""
    for line in text.split('\n'):
        cleaned = _clean_line(line)
        if len(cleaned) > 15:  # skip very short noise lines
            # Take up to first sentence-end or the whole line
            m = _SENTENCE_END_RE.search(cleaned)
            if m:
                return cleaned[:m.end()].strip()
            return cleaned[:200].strip()
    return ""


def _extract_thread_structure(text: str) -> dict:
    """Analyze thread text (joined by ---) for structure.

    Returns dict with: section_count, headings (list[str]), has_list (bool)
    """
    sections = [s.strip() for s in text.split('\n\n---\n\n') if s.strip()]
    headings = []
    has_list = False
    for section in sections:
        for line in section.split('\n'):
            line = line.strip()
            if re.match(r'^\d+[\.\)]\s+', line):
                has_list = True
                # Extract the heading portion after the number
                heading = re.sub(r'^\d+[\.\)]\s+', '', line)
                heading = _clean_line(heading)
                if heading and len(heading) > 5:
                    headings.append(heading[:80])
            elif re.match(r'^[-•]\s+', line):
                has_list = True
    return {
        "section_count": len(sections),
        "headings": headings[:8],  # cap at 8 headings
        "has_list": has_list,
    }


def _extract_key_sentences(text: str, max_sentences: int = 3) -> list[str]:
    """Extract key sentences from text using simple heuristics.

    Prioritizes sentences with: numbers/stats, quoted text, strong signals.
    """
    sentences = []
    for line in text.split('\n'):
        cleaned = _clean_line(line)
        if len(cleaned) < 15:
            continue
        # Split on sentence boundaries
        parts = _SENTENCE_END_RE.split(cleaned)
        for part in parts:
            part = part.strip()
            if len(part) > 15:
                sentences.append(part)

    if not sentences:
        return []

    # Score sentences: prefer those with stats, quotes, or strong keywords
    scored = []
    for s in sentences:
        score = 0
        if _STAT_RE.search(s):
            score += 2  # has numbers/stats
        if '"' in s or '\u201c' in s:
            score += 2  # has quotes
        if any(w in s.lower() for w in ['key', 'important', 'insight', 'takeaway', 'lesson', 'gap']):
            score += 1
        scored.append((score, s))

    scored.sort(key=lambda x: -x[0])
    seen = set()
    result = []
    for _, s in scored:
        # Deduplicate similar sentences
        sig = s[:30].lower()
        if sig not in seen:
            seen.add(sig)
            result.append(s[:200])
            if len(result) >= max_sentences:
                break
    return result


def _extract_quote(text: str) -> str:
    """Extract a notable quoted phrase from text, if any."""
    # Look for quoted text using various quote styles
    for pattern in [r'\u201c([^\u201d]{10,150})\u201d', r'"([^"]{10,150})"']:
        m = re.search(pattern, text)
        if m:
            return m.group(1).strip()
    return ""


def summarize_content(raw_content: str, category: str, config=None) -> str:
    """Generate an intelligent 2-4 line summary using heuristics.

    For THREAD items: counts sections, extracts topic headings or numbered lists.
    For text-heavy items: extracts key sentences with stats/quotes.
    For short items: returns the first meaningful sentence.

    Args:
        raw_content: Full text content (may include thread sections separated by ---)
        category: Category string (THREAD, AI, etc.)
        config: Optional config object (reserved for future use)

    Returns:
        Multi-line summary string (2-4 lines)
    """
    if not raw_content:
        return ""

    # Strip the [Engagement: ...] and [Media: ...] trailers
    content = re.sub(r'\n*\[Engagement:.*?\]$', '', raw_content, flags=re.DOTALL).strip()
    content = re.sub(r'\n*\[Media:.*?\]$', '', content, flags=re.DOTALL).strip()

    lines = []

    if category == "THREAD" or '\n\n---\n\n' in content:
        # Thread analysis
        structure = _extract_thread_structure(content)
        opener = _first_meaningful_sentence(content)
        if opener:
            lines.append(opener)

        if structure["headings"]:
            # Show numbered topic list
            count = structure["section_count"]
            lines.append(f"{count}-part thread covering:")
            for i, h in enumerate(structure["headings"][:6], 1):
                lines.append(f"  {i}. {h}")
        elif structure["section_count"] > 1:
            lines.append(f"{structure['section_count']}-part thread.")

        quote = _extract_quote(content)
        if quote:
            lines.append(f'Key insight: "{quote}"')

    else:
        # Regular tweet / long-form content
        opener = _first_meaningful_sentence(content)
        if opener:
            lines.append(opener)

        # For longer content, add key sentences
        if len(content) > 300:
            key = _extract_key_sentences(content, max_sentences=2)
            for s in key:
                if s != opener and s[:30] != opener[:30]:
                    lines.append(s)

        quote = _extract_quote(content)
        if quote and quote not in '\n'.join(lines):
            lines.append(f'"{quote}"')

    # Ensure we have something
    if not lines:
        first = _first_meaningful_sentence(content)
        if first:
            lines.append(first)
        else:
            # Last resort: clean first 200 chars
            lines.append(_clean_line(content[:200]))

    return '\n'.join(lines[:5])


def extract_urls(text: str) -> list[str]:
    """Extract URLs from tweet text using regex pattern.

    Args:
        text: Tweet text to search for URLs

    Returns:
        List of URL strings found in the text
    """
    return re.findall(r'https?://[^\s\)]+', text or "")


def categorize(text: str, bookmark: dict, config=None) -> str:
    """Categorize a bookmark by keyword matching + engagement heuristics.

    Uses word-boundary regex matching to avoid false positives.
    Falls back to engagement-based scoring if no keywords match.
    Categories and keywords are loaded from config.

    Args:
        text: Tweet text to analyze
        bookmark: Raw bookmark dict with metadata (likeCount, etc.)
        config: Config object with category_regexes and engagement_threshold

    Returns:
        Category string (AI, TECH, INTERESTING, GENERAL, etc.)
    """
    text_to_check = text or ""

    # Get category regexes from config or use empty dict
    if config is not None:
        category_regexes = config.category_regexes
        engagement_threshold = config.engagement_threshold or 500
    else:
        # Fallback: use default categories directly instead of creating throwaway Config
        from .config import DEFAULT_CATEGORIES, DEFAULTS
        import re as _re
        category_regexes = {}
        for cat, cat_cfg in DEFAULT_CATEGORIES.items():
            kws = cat_cfg.get("keywords", [])
            if kws:
                category_regexes[cat] = _re.compile(
                    r'\b(' + '|'.join(_re.escape(kw) for kw in kws) + r')\b',
                    _re.IGNORECASE
                )
        engagement_threshold = DEFAULTS.get("engagement_threshold", 500)

    # Check each category's keywords using word-boundary regex
    scores = {}
    for cat, regex in category_regexes.items():
        matches = regex.findall(text_to_check)
        if matches:
            scores[cat] = len(matches)

    if scores:
        return max(scores, key=scores.get)

    # High engagement = interesting
    likes = bookmark.get("likeCount", 0) or 0
    if likes >= engagement_threshold:
        return "INTERESTING"

    return "GENERAL"


def _build_title(username: str, text: str, full_text: str, category: str) -> str:
    """Build a descriptive title instead of truncated raw text.

    For threads: @username — "Topic of the thread"
    For link-heavy tweets: @username — description or first sentence
    For regular tweets: @username — first complete sentence
    """
    source = full_text or text or ""
    first = _first_meaningful_sentence(source)

    if category == "THREAD":
        if first:
            # Wrap in quotes to signal it's a thread topic
            topic = first[:70]
            return f'@{username} — "{topic}"'
        return f"@{username} — Thread"

    # Check if tweet is mostly links (link-only tweet)
    cleaned_text = _NOISE_RE.sub('', text or '').strip()
    if len(cleaned_text) < 10 and extract_urls(text or ''):
        # Link-only: try to get something from full_text (bird read output)
        if first and len(first) > 10:
            return f"@{username} — {first[:70]}"
        return f"@{username} — Shared link"

    # Regular tweet: first meaningful sentence
    if first:
        return f"@{username} — {first[:70]}"

    # Fallback
    fallback = (text or "").replace("\n", " ").strip()[:60]
    return f"@{username} — {fallback}" if fallback else f"@{username}"


def process_bookmark(bookmark: dict, config=None) -> dict:
    """Process a raw bookmark into a queue-ready item dict.

    Args:
        bookmark: Raw dict from bird bookmarks --json
        config: Config object (optional, uses defaults if not provided)

    Returns:
        Dict ready for queue.add_item() with: source, source_id, title,
        canonical_url, raw_content, category, summary
    """
    tweet_id = str(bookmark.get("id", ""))
    text = bookmark.get("text", "")
    # Handle None author explicitly (Twitter API can return null)
    author = bookmark.get("author") or {}
    username = author.get("username", "unknown")
    name = author.get("name", username)
    likes = bookmark.get("likeCount", 0)
    rts = bookmark.get("retweetCount", 0)
    replies = bookmark.get("replyCount", 0)

    # Build full content: try reading the full tweet
    full_text = text
    full_output = run_bird(["read", tweet_id])
    if full_output:
        full_text = full_output.strip()

    # Check if it's a thread (author posted multiple tweets in sequence)
    # Only mark as THREAD if bird thread returns 3+ tweets from the SAME author
    is_thread = False
    conv_id = bookmark.get("conversationId", "")
    if conv_id and conv_id == tweet_id:
        thread_output = run_bird(["thread", tweet_id, "--json"])
        if thread_output:
            try:
                thread_data = json.loads(thread_output)
                if isinstance(thread_data, list):
                    # Count tweets from the same author
                    author_id = bookmark.get("authorId", "")
                    same_author = [t for t in thread_data
                                   if t.get("authorId") == author_id or
                                   t.get("author", {}).get("username") == username]
                    if len(same_author) >= 3:
                        is_thread = True
                        thread_texts = [t.get("text", "") for t in same_author[:10]]
                        full_text = "\n\n---\n\n".join(thread_texts)
                        logger.info("Thread detected for %s: %d tweets from @%s",
                                   tweet_id, len(same_author), username)
            except json.JSONDecodeError as e:
                logger.warning("Thread fetch JSON parse failed for %s: %s", tweet_id, e)
        else:
            logger.debug("Thread fetch returned no output for %s", tweet_id)

    # Extract URLs
    urls = extract_urls(text)
    canonical_url = f"https://x.com/{username}/status/{tweet_id}"

    # Categorize (with fallback to GENERAL on error)
    try:
        category = "THREAD" if is_thread else categorize(full_text, bookmark, config=config)
    except Exception as e:
        logger.warning("Categorization failed for %s: %s. Defaulting to GENERAL.", tweet_id, e)
        category = "GENERAL"

    # Build descriptive title
    title = _build_title(username, text, full_text, category)

    # Build media info
    media = bookmark.get("media", [])
    media_info = ""
    if media:
        media_types = [m.get("type", "?") for m in media]
        media_info = f"\n[Media: {', '.join(media_types)}]"

    raw_content = f"{full_text}{media_info}\n\n[Engagement: ❤️{likes} 🔁{rts} 💬{replies}]"

    # Build intelligent summary
    summary = summarize_content(raw_content, category, config=config)

    # Build engagement line
    eng_parts = []
    if likes:
        eng_parts.append(f"❤️{likes}")
    if rts:
        eng_parts.append(f"🔁{rts}")
    if replies:
        eng_parts.append(f"💬{replies}")
    engagement = " | ".join(eng_parts) if eng_parts else ""

    return {
        "source": "twitter",
        "source_id": tweet_id,
        "canonical_url": canonical_url,
        "title": title,
        "category": category,
        "summary": summary[:500],
        "engagement": engagement,
        "raw_content": raw_content,
    }
