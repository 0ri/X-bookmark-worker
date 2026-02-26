#!/usr/bin/env python3
"""
Configuration management for bookmark-digest.

Loads configuration from:
1. Environment variables (highest priority)
2. config.json in skill directory
3. Sensible defaults (lowest priority)

All categories, buttons, actions, and thresholds are configurable.
"""

import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default categories with keywords, emoji, and buttons
DEFAULT_CATEGORIES = {
    "AI": {
        "keywords": ["ai", "llm", "gpt", "claude", "model", "neural", "ml", "machine learning",
                      "inference", "training", "transformer", "embeddings", "openai", "anthropic",
                      "gemini", "agent", "rag", "fine-tun"],
        "emoji": "🧠",
        "buttons": [
            {"text": "🔬 Deep Dive", "action": "deepdive"},
            {"text": "💾 Save Notes", "action": "savenotes"},
            {"text": "⏭ Skip", "action": "skip"},
        ],
    },
    "TECH": {
        "keywords": ["api", "sdk", "rust", "python", "javascript", "typescript", "react",
                      "docker", "kubernetes", "aws", "cloud", "database", "linux", "open source",
                      "github", "deploy", "serverless", "infra", "devops", "cli", "npm"],
        "emoji": "🤖",
        "buttons": [
            {"text": "🔬 Deep Dive", "action": "deepdive"},
            {"text": "💾 Save Notes", "action": "savenotes"},
            {"text": "⏭ Skip", "action": "skip"},
        ],
    },
    "INTERESTING": {
        "keywords": [],
        "emoji": "💡",
        "buttons": [
            {"text": "🔬 Deep Dive", "action": "deepdive"},
            {"text": "💾 Save Notes", "action": "savenotes"},
            {"text": "⏭ Skip", "action": "skip"},
        ],
    },
    "GENERAL": {
        "keywords": [],
        "emoji": "📌",
        "buttons": [
            {"text": "📌 Act on This", "action": "acton"},
            {"text": "🗄 File Away", "action": "fileaway"},
            {"text": "⏭ Skip", "action": "skip"},
        ],
    },
}

# Special categories that are auto-detected (not keyword-based)
SPECIAL_CATEGORY_EMOJI = {
    "THREAD": "🧵",
    "TOO_LONG": "⏳",
    "UNCATEGORIZED": "❓",
}

# Default button set used when a category has no specific buttons
DEFAULT_BUTTONS = [
    {"text": "🔬 Deep Dive", "action": "deepdive"},
    {"text": "💾 Save Notes", "action": "savenotes"},
    {"text": "⏭ Skip", "action": "skip"},
]

# Default action definitions: action_name -> {status, response, next}
DEFAULT_ACTIONS = {
    "skip": {"status": "skipped", "response": "⏭ Skipped.", "next": "none"},
    "savenotes": {"status": "completed", "response": "💾 Saving notes...", "next": "execute_now"},
    "deepdive": {"status": "queued", "response": "🔬 Queued for deep dive.", "next": "queue_overnight"},
    "backlog": {"status": "queued", "response": "📋 Added to backlog.", "next": "queue_overnight"},
    "implement": {"status": "processing", "response": "⚡ Implementing now...", "next": "execute_now"},
    "research": {"status": "processing", "response": "📊 Researching...", "next": "execute_now"},
    "fileaway": {"status": "completed", "response": "🗄 Filed away.", "next": "none"},
    "fullsummary": {"status": "processing", "response": "📝 Generating full summary...", "next": "execute_now"},
    "acton": {"status": "processing", "response": "📌 Acting on this...", "next": "execute_now"},
    "scaffold": {"status": "queued", "response": "🛠 Queued for scaffolding.", "next": "queue_overnight"},
}

# Simple scalar defaults
DEFAULTS = {
    "bird_cli": "bird",
    "data_dir": None,  # Auto-detected relative to skill dir
    "telegram_bot_token": None,
    "telegram_chat_id": None,
    "log_level": "INFO",
    "max_bookmarks": 50,
    "max_digest_items": 10,
    "max_processed_ids": 2000,
    "bird_timeout": 30,
    "bird_retry": True,
    "engagement_threshold": 500,
    "show_engagement": True,
    "show_urls": True,
    "batch_size": 5,
    "analysis_model": "opus",
    "profile_path": "data/user-profile.json",
    "max_analyze_per_run": 30,
    "max_message_length": 4000,
}

# Environment variable mappings: ENV_VAR -> (config_key, type_converter or None)
ENV_MAPPINGS = {
    "BIRD_CLI": ("bird_cli", None),
    "DATA_DIR": ("data_dir", None),
    "TELEGRAM_BOT_TOKEN": ("telegram_bot_token", None),
    "TELEGRAM_CHAT_ID": ("telegram_chat_id", None),
    "LOG_LEVEL": ("log_level", None),
    "MAX_BOOKMARKS": ("max_bookmarks", int),
    "MAX_DIGEST_ITEMS": ("max_digest_items", int),
    "MAX_PROCESSED_IDS": ("max_processed_ids", int),
    "BIRD_TIMEOUT": ("bird_timeout", int),
    "ENGAGEMENT_THRESHOLD": ("engagement_threshold", int),
    "BATCH_SIZE": ("batch_size", int),
    "ANALYSIS_MODEL": ("analysis_model", None),
    "PROFILE_PATH": ("profile_path", None),
    "MAX_ANALYZE_PER_RUN": ("max_analyze_per_run", int),
    "MAX_MESSAGE_LENGTH": ("max_message_length", int),
}


class Config:
    """Configuration container with multi-source loading."""

    def __init__(self, skill_dir: str | None = None, config_path: str | None = None):
        self._config = DEFAULTS.copy()
        self._categories = None
        self._actions = None
        self._default_buttons = None
        self.skill_dir = Path(skill_dir) if skill_dir else Path(__file__).parent.parent

        self._load_from_file(config_path)
        self._load_from_env()
        self._apply_defaults()
        self._build_categories()
        self._build_actions()
        self.validate()

    def _load_from_file(self, config_path: str | None = None) -> None:
        """Load config.json if it exists."""
        if config_path:
            json_path = Path(config_path)
        else:
            json_path = self.skill_dir / "config.json"

        if json_path.exists():
            try:
                data = json.loads(json_path.read_text())
                # Extract nested config sections before merging scalars
                self._raw_categories = data.pop("categories", None)
                self._raw_actions = data.pop("actions", None)
                self._raw_default_buttons = data.pop("default_buttons", None)
                self._raw_digest = data.pop("digest", None)
                self._raw_fetch = data.pop("fetch", None)
                # Merge remaining scalar config
                self._config.update(data)
                # Apply nested digest/fetch settings to flat config
                if self._raw_digest:
                    if "max_items" in self._raw_digest:
                        self._config["max_digest_items"] = self._raw_digest["max_items"]
                    if "show_engagement" in self._raw_digest:
                        self._config["show_engagement"] = self._raw_digest["show_engagement"]
                    if "show_urls" in self._raw_digest:
                        self._config["show_urls"] = self._raw_digest["show_urls"]
                if self._raw_fetch:
                    if "max_bookmarks" in self._raw_fetch:
                        self._config["max_bookmarks"] = self._raw_fetch["max_bookmarks"]
                    if "dedup_window" in self._raw_fetch:
                        self._config["max_processed_ids"] = self._raw_fetch["dedup_window"]
                logger.info("Loaded config from %s", json_path)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load config: %s", e)
                self._raw_categories = None
                self._raw_actions = None
                self._raw_default_buttons = None
                self._raw_digest = None
                self._raw_fetch = None
        else:
            self._raw_categories = None
            self._raw_actions = None
            self._raw_default_buttons = None
            self._raw_digest = None
            self._raw_fetch = None

    def _load_from_env(self) -> None:
        """Override config from environment variables."""
        for env_var, (config_key, converter) in ENV_MAPPINGS.items():
            value = os.environ.get(env_var)
            if value is None:
                continue
            try:
                self._config[config_key] = converter(value) if converter else value
            except (ValueError, TypeError) as e:
                logger.warning("Invalid %s value '%s': %s", env_var, value, e)

    def _apply_defaults(self) -> None:
        """Apply computed defaults after loading."""
        if self._config["data_dir"] is None:
            self._config["data_dir"] = str(self.skill_dir / "data")

        # Resolve bird CLI path
        bird = self._config["bird_cli"]
        if not os.path.isabs(bird):
            resolved = shutil.which(bird)
            if resolved:
                self._config["bird_cli"] = resolved

    def _build_categories(self) -> None:
        """Build category configuration from file or defaults."""
        if self._raw_categories:
            self._categories = self._raw_categories
        else:
            self._categories = DEFAULT_CATEGORIES.copy()

        self._default_buttons = self._raw_default_buttons or DEFAULT_BUTTONS

        # Build compiled regexes for keyword categories
        self._category_regexes = {}
        for cat, cat_config in self._categories.items():
            kws = cat_config.get("keywords", [])
            if kws:
                self._category_regexes[cat] = re.compile(
                    r'\b(' + '|'.join(re.escape(kw) for kw in kws) + r')\b',
                    re.IGNORECASE
                )

    def _build_actions(self) -> None:
        """Build action configuration from file or defaults."""
        if self._raw_actions:
            self._actions = self._raw_actions
        else:
            self._actions = DEFAULT_ACTIONS.copy()

    def validate(self) -> None:
        """Validate configuration structure and types.

        Raises:
            ValueError: If configuration is invalid with a clear error message.
        """
        errors = []

        # Validate categories
        if not isinstance(self._categories, dict):
            errors.append("'categories' must be a dict")
        else:
            for cat_name, cat_cfg in self._categories.items():
                if not isinstance(cat_cfg, dict):
                    errors.append(f"Category '{cat_name}' must be a dict")
                    continue
                kws = cat_cfg.get("keywords")
                if kws is not None and not isinstance(kws, list):
                    errors.append(f"Category '{cat_name}': 'keywords' must be a list of strings")
                elif isinstance(kws, list) and not all(isinstance(k, str) for k in kws):
                    errors.append(f"Category '{cat_name}': all keywords must be strings")
                emoji = cat_cfg.get("emoji")
                if emoji is not None and not isinstance(emoji, str):
                    errors.append(f"Category '{cat_name}': 'emoji' must be a string")
                buttons = cat_cfg.get("buttons")
                if buttons is not None:
                    if not isinstance(buttons, list):
                        errors.append(f"Category '{cat_name}': 'buttons' must be a list")
                    else:
                        for i, btn in enumerate(buttons):
                            if not isinstance(btn, dict):
                                errors.append(f"Category '{cat_name}': button {i} must be a dict")
                            elif "text" not in btn or "action" not in btn:
                                errors.append(f"Category '{cat_name}': button {i} must have 'text' and 'action'")

        # Validate actions
        if not isinstance(self._actions, dict):
            errors.append("'actions' must be a dict")
        else:
            required_action_keys = {"status", "response", "next"}
            for act_name, act_cfg in self._actions.items():
                if not isinstance(act_cfg, dict):
                    errors.append(f"Action '{act_name}' must be a dict")
                    continue
                missing = required_action_keys - set(act_cfg.keys())
                if missing:
                    errors.append(f"Action '{act_name}': missing required fields: {', '.join(sorted(missing))}")

        # Validate button actions reference valid action keys
        # Only cross-reference when both categories and actions are from config file
        # (mixing custom categories with default actions or vice versa is expected)
        valid_actions = set(self._actions.keys()) if isinstance(self._actions, dict) else set()
        if self._raw_categories and self._raw_actions and isinstance(self._categories, dict):
            for cat_name, cat_cfg in self._categories.items():
                if not isinstance(cat_cfg, dict):
                    continue
                for btn in cat_cfg.get("buttons", []):
                    if isinstance(btn, dict) and btn.get("action") not in valid_actions:
                        errors.append(
                            f"Category '{cat_name}': button action '{btn.get('action')}' "
                            f"is not defined in actions"
                        )

        # Validate engagement_threshold
        threshold = self._config.get("engagement_threshold")
        if threshold is not None:
            if not isinstance(threshold, int) or threshold <= 0:
                errors.append(f"'engagement_threshold' must be a positive integer, got {threshold!r}")

        if errors:
            raise ValueError("Invalid configuration:\n  - " + "\n  - ".join(errors))

    # --- Public API ---

    @property
    def categories(self) -> dict:
        """All configured categories."""
        return self._categories

    @property
    def category_regexes(self) -> dict:
        """Pre-compiled regex patterns for keyword-based categories."""
        return self._category_regexes

    @property
    def actions(self) -> dict:
        """All configured action definitions."""
        return self._actions

    @property
    def valid_actions(self) -> set[str]:
        """Set of all valid action names."""
        return set(self._actions.keys())

    def get_category_emoji(self, category: str) -> str:
        """Get emoji for a category, falling back to special categories then default."""
        cat_upper = category.upper()
        cat_config = self._categories.get(cat_upper)
        if cat_config and "emoji" in cat_config:
            return cat_config["emoji"]
        return SPECIAL_CATEGORY_EMOJI.get(cat_upper, "📌")

    def get_category_buttons(self, category: str) -> list[dict]:
        """Get button definitions for a category, falling back to defaults."""
        cat_upper = category.upper()
        cat_config = self._categories.get(cat_upper)
        if cat_config and "buttons" in cat_config:
            return cat_config["buttons"]
        return self._default_buttons

    def get_action_config(self, action: str) -> dict | None:
        """Get the config for a specific action."""
        return self._actions.get(action)

    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def __getattr__(self, key: str) -> Any:
        if key.startswith("_"):
            raise AttributeError(key)
        return self._config.get(key)

    def __getitem__(self, key: str) -> Any:
        return self._config[key]

    def to_dict(self) -> dict:
        """Export full configuration as a dict."""
        result = self._config.copy()
        result["categories"] = self._categories
        result["actions"] = self._actions
        result["default_buttons"] = self._default_buttons
        return result


def load_config(skill_dir: str | None = None, config_path: str | None = None) -> Config:
    """Load configuration."""
    return Config(skill_dir=skill_dir, config_path=config_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    cfg = load_config()
    print(json.dumps(cfg.to_dict(), indent=2))
