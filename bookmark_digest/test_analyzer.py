"""
Tests for analyzer module.
"""

import pytest
import json
import tempfile
import os
from .analyzer import (
    validate_analysis,
    save_analysis,
    get_button_palette,
    get_analysis_prompt,
    ButtonChoice,
    BookmarkAnalysis,
    BUTTON_PALETTE,
    VALID_ACTION_CODES,
)
from .bookmark_queue import init_db, get_item


class TestValidateAnalysis:
    """Tests for validate_analysis function."""
    
    def test_valid_analysis_validates(self):
        """Valid analysis passes validation."""
        raw = {
            "title": "@username — AI Tools",
            "summary": "Great overview of new AI coding assistants. Compares Claude vs GPT.",
            "category": "AI Tools",
            "rationale": "User is interested in AI development tools",
            "content_type": "thread",
            "buttons": [
                {"text": "🔬 Deep Dive", "action": "deepdive"},
                {"text": "💾 Save Notes", "action": "savenotes"}
            ],
            "confidence": 0.9,
            "sources": ["https://twitter.com/user/status/123"]
        }
        
        result = validate_analysis(raw)
        
        assert isinstance(result, BookmarkAnalysis)
        assert result.title == "@username — AI Tools"
        assert result.content_type == "thread"
        assert result.confidence == 0.9
        assert len(result.buttons) == 2
        assert result.buttons[0].action == "deepdive"
    
    def test_missing_required_field_raises_error(self):
        """Missing required field raises ValueError."""
        raw = {
            "title": "Test",
            "summary": "Test summary",
            # Missing category, rationale, content_type, buttons, confidence, sources
        }
        
        with pytest.raises(ValueError) as exc:
            validate_analysis(raw)
        
        assert "Missing required fields" in str(exc.value)
        assert "category" in str(exc.value)
    
    def test_invalid_content_type_raises_error(self):
        """Invalid content_type raises ValueError."""
        raw = {
            "title": "Test",
            "summary": "Test summary",
            "category": "Tech",
            "rationale": "Interesting",
            "content_type": "invalid_type",
            "buttons": [{"text": "Test", "action": "deepdive"}],
            "confidence": 0.8,
            "sources": []
        }
        
        with pytest.raises(ValueError) as exc:
            validate_analysis(raw)
        
        assert "Invalid content_type" in str(exc.value)
        assert "invalid_type" in str(exc.value)
    
    def test_valid_content_types(self):
        """All valid content types pass validation."""
        valid_types = ["tweet", "thread", "article", "video", "podcast", "repo", "paper"]
        
        for content_type in valid_types:
            raw = {
                "title": "Test",
                "summary": "Test summary",
                "category": "Tech",
                "rationale": "Interesting",
                "content_type": content_type,
                "buttons": [{"text": "Test", "action": "deepdive"}],
                "confidence": 0.8,
                "sources": []
            }
            
            result = validate_analysis(raw)
            assert result.content_type == content_type
    
    def test_unknown_button_action_raises_error(self):
        """Unknown button action raises ValueError."""
        raw = {
            "title": "Test",
            "summary": "Test summary",
            "category": "Tech",
            "rationale": "Interesting",
            "content_type": "tweet",
            "buttons": [{"text": "Test", "action": "unknown_action"}],
            "confidence": 0.8,
            "sources": []
        }
        
        with pytest.raises(ValueError) as exc:
            validate_analysis(raw)
        
        assert "unknown action" in str(exc.value).lower()
        assert "unknown_action" in str(exc.value)
    
    def test_all_palette_actions_are_valid(self):
        """All palette button actions validate correctly."""
        for btn in BUTTON_PALETTE:
            raw = {
                "title": "Test",
                "summary": "Test summary",
                "category": "Tech",
                "rationale": "Interesting",
                "content_type": "tweet",
                "buttons": [{"text": btn["text"], "action": btn["action"]}],
                "confidence": 0.8,
                "sources": []
            }
            
            result = validate_analysis(raw)
            assert result.buttons[0].action == btn["action"]
    
    def test_empty_buttons_list_raises_error(self):
        """Empty buttons list raises ValueError."""
        raw = {
            "title": "Test",
            "summary": "Test summary",
            "category": "Tech",
            "rationale": "Interesting",
            "content_type": "tweet",
            "buttons": [],
            "confidence": 0.8,
            "sources": []
        }
        
        with pytest.raises(ValueError) as exc:
            validate_analysis(raw)
        
        assert "At least one button is required" in str(exc.value)
    
    def test_too_many_buttons_raises_error(self):
        """More than 7 buttons raises ValueError."""
        raw = {
            "title": "Test",
            "summary": "Test summary",
            "category": "Tech",
            "rationale": "Interesting",
            "content_type": "tweet",
            "buttons": [{"text": f"Button {i}", "action": "deepdive"} for i in range(8)],
            "confidence": 0.8,
            "sources": []
        }
        
        with pytest.raises(ValueError) as exc:
            validate_analysis(raw)
        
        assert "Too many buttons" in str(exc.value)
    
    def test_button_missing_text_raises_error(self):
        """Button missing 'text' field raises ValueError."""
        raw = {
            "title": "Test",
            "summary": "Test summary",
            "category": "Tech",
            "rationale": "Interesting",
            "content_type": "tweet",
            "buttons": [{"action": "deepdive"}],  # Missing 'text'
            "confidence": 0.8,
            "sources": []
        }
        
        with pytest.raises(ValueError) as exc:
            validate_analysis(raw)
        
        assert "missing 'text' field" in str(exc.value)
    
    def test_button_missing_action_raises_error(self):
        """Button missing 'action' field raises ValueError."""
        raw = {
            "title": "Test",
            "summary": "Test summary",
            "category": "Tech",
            "rationale": "Interesting",
            "content_type": "tweet",
            "buttons": [{"text": "Test"}],  # Missing 'action'
            "confidence": 0.8,
            "sources": []
        }
        
        with pytest.raises(ValueError) as exc:
            validate_analysis(raw)
        
        assert "missing 'action' field" in str(exc.value)
    
    def test_confidence_out_of_range_raises_error(self):
        """Confidence outside 0-1 range raises ValueError."""
        raw = {
            "title": "Test",
            "summary": "Test summary",
            "category": "Tech",
            "rationale": "Interesting",
            "content_type": "tweet",
            "buttons": [{"text": "Test", "action": "deepdive"}],
            "confidence": 1.5,
            "sources": []
        }
        
        with pytest.raises(ValueError) as exc:
            validate_analysis(raw)
        
        assert "Confidence must be between 0 and 1" in str(exc.value)
    
    def test_invalid_confidence_type_raises_error(self):
        """Non-numeric confidence raises ValueError."""
        raw = {
            "title": "Test",
            "summary": "Test summary",
            "category": "Tech",
            "rationale": "Interesting",
            "content_type": "tweet",
            "buttons": [{"text": "Test", "action": "deepdive"}],
            "confidence": "high",
            "sources": []
        }
        
        with pytest.raises(ValueError) as exc:
            validate_analysis(raw)
        
        assert "Invalid confidence value" in str(exc.value)
    
    def test_empty_title_raises_error(self):
        """Empty title raises ValueError."""
        raw = {
            "title": "   ",
            "summary": "Test summary",
            "category": "Tech",
            "rationale": "Interesting",
            "content_type": "tweet",
            "buttons": [{"text": "Test", "action": "deepdive"}],
            "confidence": 0.8,
            "sources": []
        }
        
        with pytest.raises(ValueError) as exc:
            validate_analysis(raw)
        
        assert "title cannot be empty" in str(exc.value)
    
    def test_sources_not_list_raises_error(self):
        """Sources that's not a list raises ValueError."""
        raw = {
            "title": "Test",
            "summary": "Test summary",
            "category": "Tech",
            "rationale": "Interesting",
            "content_type": "tweet",
            "buttons": [{"text": "Test", "action": "deepdive"}],
            "confidence": 0.8,
            "sources": "not a list"
        }
        
        with pytest.raises(ValueError) as exc:
            validate_analysis(raw)
        
        assert "Sources must be a list" in str(exc.value)


class TestSaveAnalysis:
    """Tests for save_analysis function."""
    
    def test_save_analysis_writes_to_db(self):
        """save_analysis writes to database and is retrievable."""
        # Create temp database
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        
        try:
            # Initialize database
            init_db(db_path)
            
            # Create a test item
            from .bookmark_queue import add_item
            item_dict = {
                "source": "twitter",
                "source_id": "test123",
                "canonical_url": "https://twitter.com/test/123",
                "title": "Test tweet",
                "raw_content": "Test tweet",
                "category": "test",
                "status": "pending"
            }
            item_id = add_item(db_path, item_dict)
            
            # Create analysis
            analysis = BookmarkAnalysis(
                title="@testuser — Test Topic",
                summary="This is a test analysis.",
                category="Testing",
                rationale="For testing purposes",
                content_type="tweet",
                buttons=[
                    ButtonChoice(text="🔬 Deep Dive", action="deepdive"),
                    ButtonChoice(text="💾 Save Notes", action="savenotes")
                ],
                confidence=0.95,
                sources=["https://twitter.com/test/123"]
            )
            
            # Save analysis
            save_analysis(db_path, item_id, analysis)
            
            # Retrieve and verify
            item = get_item(db_path, item_id)
            assert item is not None
            assert item["analysis"] is not None
            
            # Parse and verify analysis
            saved_analysis = json.loads(item["analysis"])
            assert saved_analysis["title"] == "@testuser — Test Topic"
            assert saved_analysis["content_type"] == "tweet"
            assert saved_analysis["confidence"] == 0.95
            assert len(saved_analysis["buttons"]) == 2
            
            # Verify buttons_json
            assert item["buttons_json"] is not None
            buttons = json.loads(item["buttons_json"])
            assert len(buttons) == 2
            assert buttons[0]["action"] == "deepdive"
            
        finally:
            # Cleanup
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestGetButtonPalette:
    """Tests for get_button_palette function."""
    
    def test_returns_palette(self):
        """get_button_palette returns the button palette."""
        palette = get_button_palette()
        
        assert isinstance(palette, list)
        assert len(palette) == len(BUTTON_PALETTE)
        
        # Check structure
        for btn in palette:
            assert "code" in btn
            assert "text" in btn
            assert "action" in btn
            assert "when" in btn
    
    def test_returns_copy(self):
        """get_button_palette returns a copy, not the original."""
        palette1 = get_button_palette()
        palette2 = get_button_palette()
        
        # Should be equal but not the same object
        assert palette1 == palette2
        assert palette1 is not palette2


class TestGetAnalysisPrompt:
    """Tests for get_analysis_prompt function."""
    
    def test_prompt_includes_item_details(self):
        """Prompt includes item URL, author, text."""
        item = {
            "url": "https://twitter.com/user/status/123",
            "author": "@testuser",
            "text": "This is a test tweet about AI.",
            "engagement": "100 likes"
        }
        
        prompt = get_analysis_prompt(item)
        
        assert "https://twitter.com/user/status/123" in prompt
        assert "@testuser" in prompt
        assert "This is a test tweet about AI." in prompt
        assert "100 likes" in prompt
    
    def test_prompt_includes_button_palette(self):
        """Prompt includes all available buttons."""
        item = {"url": "https://test.com", "text": "Test"}
        
        prompt = get_analysis_prompt(item)
        
        # Check all button actions are mentioned
        for btn in BUTTON_PALETTE:
            assert btn["action"] in prompt
            assert btn["text"] in prompt
    
    def test_prompt_includes_profile_context_when_provided(self):
        """Prompt includes profile context when provided."""
        item = {"url": "https://test.com", "text": "Test"}
        profile = {
            "interests": ["AI", "health"],
            "patterns": ["tends to bookmark threads"],
            "preferences": ["fact-check health claims"]
        }
        
        prompt = get_analysis_prompt(item, profile)
        
        assert "User Profile Context" in prompt
        assert "AI" in prompt
        assert "health" in prompt
        assert "tends to bookmark threads" in prompt
        assert "fact-check health claims" in prompt
    
    def test_prompt_works_without_profile(self):
        """Prompt works correctly without profile."""
        item = {"url": "https://test.com", "text": "Test"}
        
        prompt = get_analysis_prompt(item, None)
        
        assert "User Profile Context" not in prompt
        # Should still include all other sections
        assert "Item Details" in prompt
        assert "Available Buttons" in prompt
        assert "Output Format" in prompt
    
    def test_prompt_with_empty_profile(self):
        """Prompt handles empty profile gracefully."""
        item = {"url": "https://test.com", "text": "Test"}
        profile = {}
        
        prompt = get_analysis_prompt(item, profile)
        
        # Empty profile should not add context section
        assert "User Profile Context" not in prompt
    
    def test_prompt_includes_output_format(self):
        """Prompt includes JSON output format example."""
        item = {"url": "https://test.com", "text": "Test"}
        
        prompt = get_analysis_prompt(item)
        
        assert "Output Format (JSON)" in prompt
        assert '"title":' in prompt
        assert '"content_type":' in prompt
        assert '"buttons":' in prompt
        assert '"confidence":' in prompt
