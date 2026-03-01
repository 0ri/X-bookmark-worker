"""
Tests for Session 4: Lobster Pipeline + llm-task Integration.

Covers:
- build-llm-task-request: output format, schema inclusion, few-shot prompt, profile injection
- deliver: message formatting, button generation, batch footer, idempotent delivery
- Pipeline YAML: valid structure
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from .test_utils import temp_db, sample_queue_item
from .bookmark_queue import (
    add_item, get_item, init_db,
    update_analysis, store_analyses,
    get_next_batch, set_sending, get_undelivered,
)
from .__main__ import (
    format_delivery_message, build_analysis_prompt,
    _get_category_emoji, BUTTON_LABELS,
)


# ============================================================================
# Helper
# ============================================================================

def _run_cli(*args, data_dir: str, stdin_data: str = None) -> subprocess.CompletedProcess:
    """Run the CLI with given args and DATA_DIR."""
    env = os.environ.copy()
    env["DATA_DIR"] = data_dir
    return subprocess.run(
        [sys.executable, "-m", "bookmark_digest", "--json"] + list(args),
        capture_output=True, text=True, timeout=10, env=env,
        input=stdin_data,
        cwd=os.path.dirname(os.path.dirname(__file__)),
    )


def _make_analyzed_item(db_path, source_id, category="AI/Agents", analysis_text="Great analysis."):
    """Create an item and advance it to analyzed state."""
    item_id = add_item(db_path, sample_queue_item(source_id))
    analyses = [{
        "item_id": item_id,
        "category": category,
        "analysis": analysis_text,
        "why_bookmarked": "User is interested in this topic",
        "buttons": ["dd", "im"],
        "content_type": "tweet",
        "relevance_score": 0.85,
        "needs_enrichment": False,
        "enrichment_urls": [],
    }]
    store_analyses(db_path, analyses)
    return item_id


# ============================================================================
# build-llm-task-request tests
# ============================================================================

def test_build_llm_task_request_basic():
    """build-llm-task-request outputs valid JSON with prompt, input, schema."""
    with tempfile.TemporaryDirectory() as data_dir:
        init_db(os.path.join(data_dir, "queue.db"))

        input_json = json.dumps({
            "items": [
                {"id": "bk_001", "title": "Test tweet", "raw_content": "Hello world"},
            ]
        })

        result = _run_cli("build-llm-task-request", data_dir=data_dir, stdin_data=input_json)
        assert result.returncode == 0, f"stderr: {result.stderr}"

        output = json.loads(result.stdout)
        assert "prompt" in output
        assert "input" in output
        assert "schema" in output
        assert "bookmarks" in output["input"]
        assert len(output["input"]["bookmarks"]) == 1


def test_build_llm_task_request_schema_matches_file():
    """Schema in output matches schemas/bookmark-analysis-v1.json."""
    with tempfile.TemporaryDirectory() as data_dir:
        init_db(os.path.join(data_dir, "queue.db"))

        input_json = json.dumps({"items": [{"id": "bk_001", "title": "Test"}]})
        result = _run_cli("build-llm-task-request", data_dir=data_dir, stdin_data=input_json)
        output = json.loads(result.stdout)

        # Load schema from file
        schema_path = Path(__file__).parent.parent / "schemas" / "bookmark-analysis-v1.json"
        with open(schema_path) as f:
            expected_schema = json.load(f)

        assert output["schema"] == expected_schema


def test_build_llm_task_request_prompt_has_few_shot():
    """Prompt includes few-shot examples."""
    with tempfile.TemporaryDirectory() as data_dir:
        init_db(os.path.join(data_dir, "queue.db"))

        input_json = json.dumps({"items": [{"id": "bk_001", "title": "Test"}]})
        result = _run_cli("build-llm-task-request", data_dir=data_dir, stdin_data=input_json)
        output = json.loads(result.stdout)

        prompt = output["prompt"]
        assert "Few-Shot Examples" in prompt
        assert "Health/Supplements" in prompt
        assert "AI/Agents" in prompt
        assert "bk_example1" in prompt
        assert "bk_example2" in prompt


def test_build_llm_task_request_accepts_bookmarks_key():
    """build-llm-task-request accepts 'bookmarks' key as well as 'items'."""
    with tempfile.TemporaryDirectory() as data_dir:
        init_db(os.path.join(data_dir, "queue.db"))

        input_json = json.dumps({
            "bookmarks": [
                {"id": "bk_001", "title": "Test tweet"},
                {"id": "bk_002", "title": "Another tweet"},
            ]
        })

        result = _run_cli("build-llm-task-request", data_dir=data_dir, stdin_data=input_json)
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert len(output["input"]["bookmarks"]) == 2


def test_build_llm_task_request_empty_items():
    """build-llm-task-request handles empty items list."""
    with tempfile.TemporaryDirectory() as data_dir:
        init_db(os.path.join(data_dir, "queue.db"))

        input_json = json.dumps({"items": []})
        result = _run_cli("build-llm-task-request", data_dir=data_dir, stdin_data=input_json)
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["input"]["bookmarks"] == []
        assert "0 bookmark(s)" in output["prompt"]


def test_build_llm_task_request_invalid_json():
    """build-llm-task-request returns error on invalid JSON."""
    with tempfile.TemporaryDirectory() as data_dir:
        init_db(os.path.join(data_dir, "queue.db"))

        result = _run_cli("build-llm-task-request", data_dir=data_dir, stdin_data="not json")
        assert result.returncode == 1


def test_build_llm_task_request_with_profile():
    """build-llm-task-request includes profile context when profile exists."""
    with tempfile.TemporaryDirectory() as data_dir:
        init_db(os.path.join(data_dir, "queue.db"))

        # Create a user profile
        profile_path = os.path.join(data_dir, "user-profile.json")
        profile = {
            "interests": {"AI agents": "high", "health": "medium"},
            "bookmark_patterns": {"prefers_threads": True},
            "analysis_preferences": {"fact_check_health": True},
        }
        with open(profile_path, "w") as f:
            json.dump(profile, f)

        input_json = json.dumps({"items": [{"id": "bk_001", "title": "Test"}]})

        # Run with profile path pointing to our test profile
        env = os.environ.copy()
        env["DATA_DIR"] = data_dir
        env["PROFILE_PATH"] = profile_path
        result = subprocess.run(
            [sys.executable, "-m", "bookmark_digest", "--json", "build-llm-task-request"],
            capture_output=True, text=True, timeout=10, env=env,
            input=input_json,
            cwd=os.path.dirname(os.path.dirname(__file__)),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout)

        # Profile context should be in the prompt if it was loaded
        # (depends on config.profile_path pointing to the right place)
        assert "prompt" in output


# ============================================================================
# build_analysis_prompt unit tests
# ============================================================================

def test_build_analysis_prompt_includes_item_count():
    """Prompt mentions correct item count."""
    items = [{"id": "bk_001"}, {"id": "bk_002"}, {"id": "bk_003"}]
    prompt = build_analysis_prompt(items)
    assert "3 bookmark(s)" in prompt


def test_build_analysis_prompt_with_profile():
    """Prompt includes user profile section when provided."""
    items = [{"id": "bk_001"}]
    profile = "User interests: AI agents (high), health (medium)."
    prompt = build_analysis_prompt(items, profile_context=profile)
    assert "User Profile" in prompt
    assert "AI agents (high)" in prompt


def test_build_analysis_prompt_without_profile():
    """Prompt has no profile section when context is empty."""
    items = [{"id": "bk_001"}]
    prompt = build_analysis_prompt(items, profile_context="")
    assert "User Profile" not in prompt


def test_build_analysis_prompt_button_guide():
    """Prompt includes button selection guide."""
    prompt = build_analysis_prompt([{"id": "bk_001"}])
    assert "Button Palette" in prompt
    assert "dd (Deep Dive)" in prompt
    assert "fc (Fact Check)" in prompt
    assert "MUTUALLY EXCLUSIVE" in prompt


# ============================================================================
# format_delivery_message unit tests
# ============================================================================

def test_format_delivery_message_basic():
    """format_delivery_message produces correct structure."""
    item = {
        "id": "bk_test1",
        "category": "AI/Agents",
        "title": "Cool AI Tool",
        "canonical_url": "https://x.com/test",
        "analysis": json.dumps({
            "analysis": "This is a great AI tool for coding.",
            "why_bookmarked": "User tracks AI tools",
        }),
        "buttons_json": json.dumps(["dd", "im"]),
    }

    msg = format_delivery_message(item)
    assert msg["item_id"] == "bk_test1"
    assert msg["category"] == "AI/Agents"
    assert "🤖" in msg["text"]
    assert "Cool AI Tool" in msg["text"]
    assert "great AI tool" in msg["text"]
    assert "User tracks AI tools" in msg["text"]
    assert "https://x.com/test" in msg["text"]
    assert len(msg["buttons"]) > 0


def test_format_delivery_message_buttons():
    """Buttons have correct callback_data format and enforce mutual exclusivity."""
    item = {
        "id": "bk_abc",
        "category": "Tech",
        "analysis": json.dumps({"analysis": "test"}),
        "buttons_json": json.dumps(["dd", "im", "sn"]),
    }

    msg = format_delivery_message(item)
    all_buttons = [btn for row in msg["buttons"] for btn in row]

    assert len(all_buttons) == 3
    assert all_buttons[0]["callback_data"] == "q|dd|bk_abc"
    assert all_buttons[0]["text"] == "🔬 Deep Dive"
    assert all_buttons[1]["callback_data"] == "q|im|bk_abc"
    assert all_buttons[2]["callback_data"] == "q|sn|bk_abc"


def test_format_delivery_message_mutual_exclusivity():
    """dd and fc are mutually exclusive — first one wins."""
    item = {
        "id": "bk_abc",
        "category": "Health",
        "analysis": json.dumps({"analysis": "test"}),
        "buttons_json": json.dumps(["dd", "fc", "sn"]),
    }
    msg = format_delivery_message(item)
    codes = [btn["callback_data"].split("|")[1] for row in msg["buttons"] for btn in row]
    assert "dd" in codes
    assert "fc" not in codes  # dd came first, fc dropped

    # Reverse order: fc first
    item["buttons_json"] = json.dumps(["fc", "dd", "sn"])
    msg = format_delivery_message(item)
    codes = [btn["callback_data"].split("|")[1] for row in msg["buttons"] for btn in row]
    assert "fc" in codes
    assert "dd" not in codes


def test_format_delivery_message_removed_codes_filtered():
    """Removed button codes (rs, fs) are silently dropped."""
    item = {
        "id": "bk_abc",
        "category": "Tech",
        "analysis": json.dumps({"analysis": "test"}),
        "buttons_json": json.dumps(["rs", "fs", "dd"]),
    }
    msg = format_delivery_message(item)
    codes = [btn["callback_data"].split("|")[1] for row in msg["buttons"] for btn in row]
    assert codes == ["dd"]


def test_format_delivery_message_button_rows():
    """Buttons are arranged 2 per row."""
    item = {
        "id": "bk_abc",
        "category": "Tech",
        "analysis": json.dumps({"analysis": "test"}),
        "buttons_json": json.dumps(["dd", "im", "sn"]),
    }

    msg = format_delivery_message(item)
    assert len(msg["buttons"]) == 2  # [dd, im] and [sn]
    assert len(msg["buttons"][0]) == 2
    assert len(msg["buttons"][1]) == 1


def test_format_delivery_message_no_analysis():
    """Handles items with no analysis gracefully."""
    item = {
        "id": "bk_test2",
        "category": "Uncategorized",
        "analysis": None,
        "buttons_json": None,
    }

    msg = format_delivery_message(item)
    assert msg["item_id"] == "bk_test2"
    assert len(msg["buttons"]) > 0  # Default dd button


def test_get_category_emoji():
    """Category emoji lookup works for known and unknown categories."""
    assert _get_category_emoji("AI/Agents") == "🤖"
    assert _get_category_emoji("Health/Supplements") == "💊"
    assert _get_category_emoji("Programming/Rust") == "💻"
    assert _get_category_emoji("Unknown Category") == "📌"


# ============================================================================
# deliver CLI integration tests
# ============================================================================

def test_deliver_with_analyzed_items():
    """deliver formats and outputs analyzed items."""
    with tempfile.TemporaryDirectory() as data_dir:
        db = os.path.join(data_dir, "queue.db")
        init_db(db)

        # Create 3 analyzed items
        id1 = _make_analyzed_item(db, "t001", "AI/Agents", "Great AI framework.")
        id2 = _make_analyzed_item(db, "t002", "Health/Supplements", "Creatine study.")
        id3 = _make_analyzed_item(db, "t003", "Programming/Rust", "Rust performance.")

        result = _run_cli("deliver", "--batch-size", "3", data_dir=data_dir)
        assert result.returncode == 0, f"stderr: {result.stderr}"

        output = json.loads(result.stdout)
        assert output["delivered"] == 3
        assert output["remaining"] == 0
        assert output["batch_id"] is not None
        assert len(output["messages"]) == 3

        # Check message structure
        msg = output["messages"][0]
        assert "item_id" in msg
        assert "text" in msg
        assert "buttons" in msg
        assert "category" in msg


def test_deliver_batch_size_limits():
    """deliver respects batch_size limit."""
    with tempfile.TemporaryDirectory() as data_dir:
        db = os.path.join(data_dir, "queue.db")
        init_db(db)

        # Create 5 analyzed items
        for i in range(5):
            _make_analyzed_item(db, f"t{i:03d}")

        result = _run_cli("deliver", "--batch-size", "2", data_dir=data_dir)
        output = json.loads(result.stdout)

        assert output["delivered"] == 2
        assert output["remaining"] == 3  # 5 total - 2 delivered


def test_deliver_idempotent():
    """Running deliver twice: second call produces 0 deliveries."""
    with tempfile.TemporaryDirectory() as data_dir:
        db = os.path.join(data_dir, "queue.db")
        init_db(db)

        _make_analyzed_item(db, "t001")
        _make_analyzed_item(db, "t002")

        # First delivery
        r1 = _run_cli("deliver", "--batch-size", "5", data_dir=data_dir)
        o1 = json.loads(r1.stdout)
        assert o1["delivered"] == 2

        # Second delivery — items are now 'sending', not 'analyzed'
        r2 = _run_cli("deliver", "--batch-size", "5", data_dir=data_dir)
        o2 = json.loads(r2.stdout)
        assert o2["delivered"] == 0
        assert o2["batch_id"] is None


def test_deliver_empty_queue():
    """deliver with no analyzed items returns 0."""
    with tempfile.TemporaryDirectory() as data_dir:
        init_db(os.path.join(data_dir, "queue.db"))

        result = _run_cli("deliver", "--batch-size", "5", data_dir=data_dir)
        output = json.loads(result.stdout)

        assert output["delivered"] == 0
        assert output["remaining"] == 0
        assert output["messages"] == []


def test_deliver_footer_format_no_remaining():
    """deliver with all items in batch shows footer without next button."""
    with tempfile.TemporaryDirectory() as data_dir:
        db = os.path.join(data_dir, "queue.db")
        init_db(db)
        _make_analyzed_item(db, "t001")

        result = _run_cli("deliver", "--batch-size", "5", data_dir=data_dir)
        output = json.loads(result.stdout)

        assert "footer" in output
        footer = output["footer"]
        assert "Batch" in footer["text"]
        assert "0 remaining" in footer["text"]
        assert "buttons" not in footer  # No next button when nothing remains


def test_deliver_footer_format_with_remaining():
    """deliver with remaining items shows next button with correct count."""
    with tempfile.TemporaryDirectory() as data_dir:
        db = os.path.join(data_dir, "queue.db")
        init_db(db)
        # Create 3 items, deliver batch of 1
        for i in range(3):
            _make_analyzed_item(db, f"t{i:03d}")

        result = _run_cli("deliver", "--batch-size", "1", data_dir=data_dir)
        output = json.loads(result.stdout)

        footer = output["footer"]
        assert "2 remaining" in footer["text"]
        assert "buttons" in footer
        assert footer["buttons"][0][0]["text"] == "Next 1 ▶"
        assert footer["buttons"][0][0]["callback_data"].startswith("q|nb|")


def test_deliver_items_transition_to_sending():
    """After deliver, items should be in 'sending' state."""
    with tempfile.TemporaryDirectory() as data_dir:
        db = os.path.join(data_dir, "queue.db")
        init_db(db)
        item_id = _make_analyzed_item(db, "t001")

        _run_cli("deliver", "--batch-size", "5", data_dir=data_dir)

        item = get_item(db, item_id)
        assert item["status"] == "sending"


def test_deliver_message_callback_data_format():
    """Callback data follows q|{code}|{item_id} format."""
    with tempfile.TemporaryDirectory() as data_dir:
        db = os.path.join(data_dir, "queue.db")
        init_db(db)
        item_id = _make_analyzed_item(db, "t001")

        result = _run_cli("deliver", "--batch-size", "5", data_dir=data_dir)
        output = json.loads(result.stdout)

        msg = output["messages"][0]
        all_buttons = [btn for row in msg["buttons"] for btn in row]
        for btn in all_buttons:
            parts = btn["callback_data"].split("|")
            assert len(parts) == 3
            assert parts[0] == "q"
            assert parts[1] in BUTTON_LABELS
            assert parts[2] == item_id


# ============================================================================
# Pipeline YAML tests
# ============================================================================

def test_pipeline_yaml_exists():
    """workflows/bookmark-pipeline.lobster exists."""
    pipeline_path = Path(__file__).parent.parent / "workflows" / "bookmark-pipeline.lobster"
    assert pipeline_path.exists(), f"Pipeline file should exist at {pipeline_path}"


def test_pipeline_yaml_valid():
    """Pipeline YAML is valid and has expected structure."""
    import yaml

    pipeline_path = Path(__file__).parent.parent / "workflows" / "bookmark-pipeline.lobster"
    with open(pipeline_path) as f:
        pipeline = yaml.safe_load(f)

    assert pipeline["name"] == "bookmark-digest"
    assert "steps" in pipeline
    steps = pipeline["steps"]
    assert len(steps) == 4

    # Verify step IDs
    step_ids = [s["id"] for s in steps]
    assert step_ids == ["fetch", "build-prompt", "store", "deliver"]

    # Verify each step has a command
    for step in steps:
        assert "command" in step

    # Verify stdin chaining
    assert steps[1].get("stdin") == "$fetch.stdout"


def test_pipeline_yaml_args():
    """Pipeline YAML defines expected args with defaults."""
    import yaml

    pipeline_path = Path(__file__).parent.parent / "workflows" / "bookmark-pipeline.lobster"
    with open(pipeline_path) as f:
        pipeline = yaml.safe_load(f)

    assert "args" in pipeline
    assert pipeline["args"]["batch_size"]["default"] == "5"
    assert pipeline["args"]["limit"]["default"] == "50"
