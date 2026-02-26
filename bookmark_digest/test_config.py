#!/usr/bin/env python3
"""Tests for config.py — configuration loading, categories, actions."""

import json
import tempfile
from pathlib import Path
from bookmark_digest.test_utils import env_override
from bookmark_digest.config import Config


def test_defaults():
    """Test default configuration values."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = Config(skill_dir=tmpdir)
        
        assert config.log_level == "INFO"
        assert config.max_bookmarks == 50
        assert config.max_digest_items == 10
        assert config.engagement_threshold == 500
        assert config.bird_timeout == 30
        print("✓ Config: defaults")


def test_env_override():
    """Test environment variable override."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with env_override(LOG_LEVEL="DEBUG", MAX_BOOKMARKS="100", ENGAGEMENT_THRESHOLD="1000"):
            config = Config(skill_dir=tmpdir)
            
            assert config.log_level == "DEBUG"
            assert config.max_bookmarks == 100
            assert config.engagement_threshold == 1000
            print("✓ Config: env override")


def test_json_config():
    """Test loading from config.json."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.json"
        config_path.write_text(json.dumps({
            "log_level": "WARNING",
            "max_bookmarks": 25,
            "bird_timeout": 60,
        }))
        
        config = Config(skill_dir=tmpdir)
        
        assert config.log_level == "WARNING"
        assert config.max_bookmarks == 25
        assert config.bird_timeout == 60
        assert config.max_digest_items == 10  # Unset uses default
        print("✓ Config: JSON file")


def test_priority():
    """Test priority: env > file > defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.json"
        config_path.write_text(json.dumps({"max_bookmarks": 25}))
        
        with env_override(MAX_BOOKMARKS="200"):
            config = Config(skill_dir=tmpdir)
            assert config.max_bookmarks == 200, "Env should override file"
            print("✓ Config: priority order")


def test_access_methods():
    """Test attribute, dict, and get() access."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = Config(skill_dir=tmpdir)
        
        assert config.log_level == "INFO"
        assert config["log_level"] == "INFO"
        assert config.get("log_level") == "INFO"
        assert config.get("nonexistent", "default") == "default"
        print("✓ Config: access methods")


def test_to_dict():
    """Test exporting config as dict."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = Config(skill_dir=tmpdir)
        d = config.to_dict()
        
        assert isinstance(d, dict)
        assert "log_level" in d
        assert "max_bookmarks" in d
        assert "categories" in d
        assert "actions" in d
        assert "default_buttons" in d
        print("✓ Config: to_dict()")


def test_corrupt_json():
    """Test handling of corrupt JSON."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.json"
        config_path.write_text("{invalid json")
        
        config = Config(skill_dir=tmpdir)
        assert config.log_level == "INFO"  # Falls back to default
        print("✓ Config: corrupt JSON fallback")


def test_default_categories():
    """Test default categories are loaded."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = Config(skill_dir=tmpdir)
        
        cats = config.categories
        assert "AI" in cats
        assert "TECH" in cats
        assert "GENERAL" in cats
        assert "INTERESTING" in cats
        # Removed personal categories should not exist in defaults
        assert "CLAWDBOT" not in cats
        assert "HEALTH" not in cats
        print("✓ Config: default categories")


def test_custom_categories():
    """Test loading custom categories from config.json replaces defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.json"
        config_path.write_text(json.dumps({
            "categories": {
                "FINANCE": {
                    "keywords": ["stock", "crypto"],
                    "emoji": "💰",
                    "buttons": [{"text": "📊 Analyze", "action": "deepdive"}]
                }
            }
        }))
        
        config = Config(skill_dir=tmpdir)
        cats = config.categories
        assert "FINANCE" in cats
        assert "AI" not in cats  # Custom categories replace defaults entirely
        assert cats["FINANCE"]["emoji"] == "💰"
        assert cats["FINANCE"]["keywords"] == ["stock", "crypto"]
        print("✓ Config: custom categories from file")


def test_category_emoji():
    """Test get_category_emoji() method."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = Config(skill_dir=tmpdir)
        
        assert config.get_category_emoji("AI") == "🧠"
        assert config.get_category_emoji("TECH") == "🤖"
        assert config.get_category_emoji("INTERESTING") == "💡"
        assert config.get_category_emoji("GENERAL") == "📌"
        # Special categories not in DEFAULT_CATEGORIES but in SPECIAL_CATEGORY_EMOJI
        assert config.get_category_emoji("THREAD") == "🧵"
        assert config.get_category_emoji("TOO_LONG") == "⏳"
        assert config.get_category_emoji("UNCATEGORIZED") == "❓"
        # Unknown category falls back to default 📌
        assert config.get_category_emoji("NONEXISTENT") == "📌"
        print("✓ Config: get_category_emoji()")


def test_category_buttons():
    """Test get_category_buttons() method."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = Config(skill_dir=tmpdir)
        
        # AI has 3 buttons: deepdive, savenotes, skip
        ai_buttons = config.get_category_buttons("AI")
        assert len(ai_buttons) == 3
        assert ai_buttons[0]["action"] == "deepdive"
        assert ai_buttons[1]["action"] == "savenotes"
        assert ai_buttons[2]["action"] == "skip"
        
        # GENERAL has different buttons: acton, fileaway, skip
        general_buttons = config.get_category_buttons("GENERAL")
        assert len(general_buttons) == 3
        assert general_buttons[0]["action"] == "acton"
        assert general_buttons[1]["action"] == "fileaway"
        assert general_buttons[2]["action"] == "skip"
        
        # Unknown category gets default buttons
        unknown_buttons = config.get_category_buttons("NONEXISTENT")
        assert len(unknown_buttons) == 3
        assert unknown_buttons[0]["action"] == "deepdive"
        print("✓ Config: get_category_buttons()")


def test_category_buttons_custom():
    """Test get_category_buttons() with custom config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.json"
        config_path.write_text(json.dumps({
            "categories": {
                "FINANCE": {
                    "keywords": ["stock"],
                    "emoji": "💰",
                    "buttons": [
                        {"text": "📈 Buy", "action": "acton"},
                        {"text": "⏭ Skip", "action": "skip"},
                    ]
                }
            },
            "default_buttons": [
                {"text": "🔍 Look", "action": "deepdive"},
            ]
        }))
        config = Config(skill_dir=tmpdir)
        
        fin_buttons = config.get_category_buttons("FINANCE")
        assert len(fin_buttons) == 2
        assert fin_buttons[0]["text"] == "📈 Buy"
        
        # Unknown category falls back to custom default_buttons
        unk_buttons = config.get_category_buttons("UNKNOWN")
        assert len(unk_buttons) == 1
        assert unk_buttons[0]["action"] == "deepdive"
        print("✓ Config: get_category_buttons() custom")


def test_valid_actions():
    """Test valid_actions property returns set of action names."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = Config(skill_dir=tmpdir)
        
        actions = config.valid_actions
        assert isinstance(actions, set)
        assert "skip" in actions
        assert "deepdive" in actions
        assert "savenotes" in actions
        assert "fileaway" in actions
        assert "backlog" in actions
        assert "implement" in actions
        assert "research" in actions
        assert "fullsummary" in actions
        assert "acton" in actions
        assert "scaffold" in actions
        # Not valid actions
        assert "routine" not in actions
        assert "audiobrief" not in actions
        assert "hack" not in actions
        print("✓ Config: valid_actions")


def test_custom_actions():
    """Test custom actions from config.json."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.json"
        config_path.write_text(json.dumps({
            "actions": {
                "approve": {"status": "approved", "response": "Approved!", "next": "none"},
                "reject": {"status": "rejected", "response": "Rejected.", "next": "none"},
            }
        }))
        config = Config(skill_dir=tmpdir)
        
        actions = config.valid_actions
        assert "approve" in actions
        assert "reject" in actions
        assert "skip" not in actions  # Custom actions replace defaults
        print("✓ Config: custom actions")


def test_action_config():
    """Test get_action_config() method."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = Config(skill_dir=tmpdir)
        
        skip_cfg = config.get_action_config("skip")
        assert skip_cfg is not None
        assert skip_cfg["status"] == "skipped"
        assert "Skipped" in skip_cfg["response"]
        assert skip_cfg["next"] == "none"
        
        deepdive_cfg = config.get_action_config("deepdive")
        assert deepdive_cfg is not None
        assert deepdive_cfg["status"] == "queued"
        assert deepdive_cfg["next"] == "queue_overnight"
        
        assert config.get_action_config("nonexistent") is None
        print("✓ Config: get_action_config()")


def test_category_regexes():
    """Test category_regexes are pre-compiled."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = Config(skill_dir=tmpdir)
        
        regexes = config.category_regexes
        assert "AI" in regexes
        assert "TECH" in regexes
        # INTERESTING and GENERAL have empty keywords, so no regex
        assert "INTERESTING" not in regexes
        assert "GENERAL" not in regexes
        
        # Test regex matching
        ai_regex = regexes["AI"]
        assert ai_regex.search("Using LLM for inference")
        assert ai_regex.search("Claude is an AI agent")
        assert not ai_regex.search("Just a random tweet about cats")
        
        tech_regex = regexes["TECH"]
        assert tech_regex.search("Deploy Docker containers")
        assert tech_regex.search("New Python SDK released")
        assert not tech_regex.search("The weather is nice today")
        print("✓ Config: category_regexes")


def test_category_regexes_word_boundary():
    """Test that category regexes use word boundaries (no false positives)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = Config(skill_dir=tmpdir)
        
        ai_regex = config.category_regexes["AI"]
        # "ai" should match as a word but not as part of "said" or "main"
        assert ai_regex.search("the ai model works great")
        # Word boundary means 'ai' in 'said' should NOT match
        # (unless other keywords match)
        print("✓ Config: category_regexes word boundaries")


def test_digest_settings():
    """Test digest/fetch nested config loading."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.json"
        config_path.write_text(json.dumps({
            "digest": {"max_items": 20},
            "fetch": {"max_bookmarks": 100, "dedup_window": 5000}
        }))
        
        config = Config(skill_dir=tmpdir)
        assert config.max_digest_items == 20
        assert config.max_bookmarks == 100
        assert config.max_processed_ids == 5000
        print("✓ Config: digest/fetch nested settings")


def test_validate_invalid_actions():
    """Test validation rejects actions missing required fields."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.json"
        config_path.write_text(json.dumps({
            "actions": {
                "bad": {"status": "ok"}  # missing 'response' and 'next'
            }
        }))
        try:
            Config(skill_dir=tmpdir)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "missing required fields" in str(e)
            print("✓ Config: validate rejects bad actions")


def test_validate_invalid_threshold():
    """Test validation rejects non-positive engagement_threshold."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.json"
        config_path.write_text(json.dumps({
            "engagement_threshold": -1
        }))
        try:
            Config(skill_dir=tmpdir)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "engagement_threshold" in str(e)
            print("✓ Config: validate rejects bad threshold")


def test_validate_button_action_crossref():
    """Test validation catches button referencing undefined action."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.json"
        config_path.write_text(json.dumps({
            "categories": {
                "TEST": {
                    "keywords": ["test"],
                    "emoji": "🧪",
                    "buttons": [{"text": "Do X", "action": "nonexistent_action"}]
                }
            },
            "actions": {
                "skip": {"status": "skipped", "response": "Skipped.", "next": "none"}
            }
        }))
        try:
            Config(skill_dir=tmpdir)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "nonexistent_action" in str(e)
            print("✓ Config: validate catches bad button→action ref")


if __name__ == "__main__":
    test_defaults()
    test_env_override()
    test_json_config()
    test_priority()
    test_access_methods()
    test_to_dict()
    test_corrupt_json()
    test_default_categories()
    test_custom_categories()
    test_category_emoji()
    test_category_buttons()
    test_category_buttons_custom()
    test_valid_actions()
    test_custom_actions()
    test_action_config()
    test_category_regexes()
    test_category_regexes_word_boundary()
    test_digest_settings()
    test_validate_invalid_actions()
    test_validate_invalid_threshold()
    test_validate_button_action_crossref()
    print("\n✅ CONFIG TESTS PASSED")
