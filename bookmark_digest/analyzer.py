"""
Analyzer module - defines the BookmarkAnalysis contract and validation.

This module defines the JSON schema for bookmark analysis and provides
functions for validation, saving, and prompt generation for LLM analysis.
"""

from dataclasses import dataclass, asdict
from typing import Literal
import json


# Button palette - available actions for analyzed bookmarks
BUTTON_PALETTE = [
    {"code": "dd", "text": "🔬 Deep Dive", "action": "deepdive", "when": "always"},
    {"code": "im", "text": "⚡ Implement", "action": "implement", "when": "has_plan"},
    {"code": "fc", "text": "📊 Fact Check", "action": "factcheck", "when": "health_claim"},
    {"code": "sn", "text": "💾 Save Notes", "action": "savenotes", "when": "always"},
    {"code": "rm", "text": "⏰ Remind Me", "action": "remind", "when": "habit_or_routine"},
    {"code": "fs", "text": "📝 Full Summary", "action": "fullsummary", "when": "long_content"},
    {"code": "rs", "text": "🔗 Read Source", "action": "readsource", "when": "has_url"},
]

# Valid action codes for button validation
VALID_ACTION_CODES = {btn["action"] for btn in BUTTON_PALETTE}

# Valid content types
VALID_CONTENT_TYPES = {"tweet", "thread", "article", "video", "podcast", "repo", "paper"}


@dataclass
class ButtonChoice:
    """Represents a single button action for a bookmark."""
    text: str      # Display text with emoji, e.g., "🔬 Deep Dive"
    action: str    # Action code from palette, e.g., "deepdive"


@dataclass
class BookmarkAnalysis:
    """
    Complete analysis of a bookmarked item.
    
    This schema defines the contract between the LLM and the Python pipeline.
    All fields are required.
    """
    title: str                   # Descriptive title: "@username — Topic"
    summary: str                 # 2-5 sentence analysis
    category: str                # LLM-inferred category (free-form)
    rationale: str               # Why user likely bookmarked this
    content_type: str            # tweet|thread|article|video|podcast|repo|paper
    buttons: list[ButtonChoice]  # Selected from palette (2-5 buttons)
    confidence: float            # 0-1, how confident in analysis
    sources: list[str]           # URLs consulted during analysis


def validate_analysis(raw_dict: dict) -> BookmarkAnalysis:
    """
    Validate raw analysis dictionary from LLM output.
    
    Args:
        raw_dict: Dictionary containing analysis fields
        
    Returns:
        BookmarkAnalysis instance if validation succeeds
        
    Raises:
        ValueError: If validation fails with clear error message
    """
    # Check required fields
    required_fields = ["title", "summary", "category", "rationale", 
                      "content_type", "buttons", "confidence", "sources"]
    missing = [f for f in required_fields if f not in raw_dict]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")
    
    # Validate content_type
    content_type = raw_dict.get("content_type", "").lower()
    if content_type not in VALID_CONTENT_TYPES:
        raise ValueError(
            f"Invalid content_type: '{content_type}'. "
            f"Must be one of: {', '.join(sorted(VALID_CONTENT_TYPES))}"
        )
    
    # Validate confidence
    confidence = raw_dict.get("confidence")
    try:
        confidence = float(confidence)
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"Confidence must be between 0 and 1, got: {confidence}")
    except (TypeError, ValueError) as e:
        raise ValueError(f"Invalid confidence value: {e}")
    
    # Validate buttons
    buttons_raw = raw_dict.get("buttons", [])
    if not isinstance(buttons_raw, list):
        raise ValueError(f"Buttons must be a list, got: {type(buttons_raw).__name__}")
    
    if not buttons_raw:
        raise ValueError("At least one button is required")
    
    if len(buttons_raw) > 7:
        raise ValueError(f"Too many buttons ({len(buttons_raw)}). Maximum is 7.")
    
    buttons = []
    for i, btn in enumerate(buttons_raw):
        if not isinstance(btn, dict):
            raise ValueError(f"Button {i} must be a dict, got: {type(btn).__name__}")
        
        if "text" not in btn:
            raise ValueError(f"Button {i} missing 'text' field")
        if "action" not in btn:
            raise ValueError(f"Button {i} missing 'action' field")
        
        action = btn["action"]
        if action not in VALID_ACTION_CODES:
            raise ValueError(
                f"Button {i} has unknown action '{action}'. "
                f"Valid actions: {', '.join(sorted(VALID_ACTION_CODES))}"
            )
        
        buttons.append(ButtonChoice(text=btn["text"], action=action))
    
    # Validate sources is a list
    sources = raw_dict.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError(f"Sources must be a list, got: {type(sources).__name__}")
    
    # Validate string fields are non-empty
    for field in ["title", "summary", "category", "rationale"]:
        value = raw_dict.get(field, "")
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string, got: {type(value).__name__}")
        if not value.strip():
            raise ValueError(f"{field} cannot be empty")
    
    # Construct the dataclass
    return BookmarkAnalysis(
        title=raw_dict["title"].strip(),
        summary=raw_dict["summary"].strip(),
        category=raw_dict["category"].strip(),
        rationale=raw_dict["rationale"].strip(),
        content_type=content_type,
        buttons=buttons,
        confidence=confidence,
        sources=sources
    )


def save_analysis(db_path: str, item_id: str, analysis: BookmarkAnalysis) -> None:
    """
    Save analysis to database via bookmark_queue module.
    
    Args:
        db_path: Path to SQLite database
        item_id: Bookmark item ID
        analysis: Validated BookmarkAnalysis instance
    """
    from .bookmark_queue import update_analysis
    
    # Serialize analysis to JSON
    analysis_dict = asdict(analysis)
    analysis_json = json.dumps(analysis_dict, indent=2)
    
    # Serialize buttons separately for Telegram callback generation
    buttons_json = json.dumps([asdict(btn) for btn in analysis.buttons])
    
    # Update database
    update_analysis(db_path, item_id, analysis_json, buttons_json)


def get_button_palette() -> list[dict]:
    """
    Get the available button palette.
    
    Returns:
        List of button definitions with code, text, action, and when fields
    """
    return BUTTON_PALETTE.copy()


def get_analysis_prompt(item: dict, profile: dict | None = None) -> str:
    """
    Build the analysis prompt for LLM.
    
    Args:
        item: Bookmark item dictionary with url, text, author, etc.
        profile: Optional user profile dictionary with interests and patterns
        
    Returns:
        Formatted prompt string for LLM
    """
    # Build button palette description
    button_desc = "\n".join([
        f"  - {btn['text']} (action: {btn['action']}, use when: {btn['when']})"
        for btn in BUTTON_PALETTE
    ])
    
    # Build profile context if available
    profile_context = ""
    if profile:
        interests = profile.get("interests", [])
        patterns = profile.get("patterns", [])
        preferences = profile.get("preferences", [])
        
        parts = []
        if interests:
            parts.append(f"User interests: {', '.join(interests)}")
        if patterns:
            parts.append(f"Bookmark patterns: {'; '.join(patterns)}")
        if preferences:
            parts.append(f"Analysis preferences: {'; '.join(preferences)}")
        
        if parts:
            profile_context = "\n\n**User Profile Context:**\n" + "\n".join(parts)
    
    # Build the prompt
    prompt = f"""Analyze this bookmarked Twitter/X item and provide structured analysis.

**Item Details:**
- URL: {item.get('url', 'N/A')}
- Author: {item.get('author', 'Unknown')}
- Text: {item.get('text', '')}
- Engagement: {item.get('engagement', 'N/A')}
{profile_context}

**Your Task:**
1. Read the full content (thread if applicable, linked articles if present)
2. Determine why the user likely bookmarked this
3. Categorize the content type and topic
4. Select 2-5 appropriate action buttons from the palette below

**Available Buttons:**
{button_desc}

**Button Selection Rules:**
- Always include at least one "always" button (Deep Dive or Save Notes)
- Include conditional buttons only when relevant (e.g., Fact Check for health claims)
- Choose 2-5 buttons total - be selective
- Order buttons by likely user priority

**Output Format (JSON):**
{{
  "title": "@username — Brief topic description",
  "summary": "2-5 sentence analysis of why this is interesting/useful",
  "category": "Free-form category (e.g., AI Tools, Health, Productivity)",
  "rationale": "Why you think the user bookmarked this",
  "content_type": "tweet|thread|article|video|podcast|repo|paper",
  "buttons": [
    {{"text": "🔬 Deep Dive", "action": "deepdive"}},
    {{"text": "💾 Save Notes", "action": "savenotes"}}
  ],
  "confidence": 0.85,
  "sources": ["https://twitter.com/user/status/123", "https://article.com"]
}}

**Important:**
- Be concise but insightful in summary
- Infer content_type correctly (thread = multiple connected tweets)
- Confidence = your certainty about the analysis (0-1)
- List all URLs you consulted in sources

Output valid JSON only, no markdown code fences."""
    
    return prompt
