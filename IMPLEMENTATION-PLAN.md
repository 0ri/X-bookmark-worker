# Implementation Plan: X Bookmark Worker v2

**Based on:** PRD-v2 + Council Review (2026-02-26)
**Approach:** Claude Code CLI sessions, one phase at a time, TDD where applicable
**Total estimate:** 7 sessions across ~2 weeks

---

## Pre-Implementation: Cleanup (10 min, manual)

Delete dormant artifacts before any code changes:

```
rm -rf skills/.bookmark-retired/
rm scripts/clawdio-bookmarks.sh scripts/twitter-bookmarks.sh
rm memory/clawdio-bookmarks-state.json memory/twitter-bookmarks-state.json
```

Git commit: `cleanup: remove legacy bookmark artifacts`

---

## Session 1: Safety Rails + .gitignore + Logging

**Time:** ~30 min
**Risk:** Low — no behavior changes, pure infrastructure

### Tasks
1. **`.gitignore` hardening**
   - Add: `config.json`, `user-profile.json`, `data/`, `*.db`, `*.db-wal`, `*.db-shm`, `.env`, `*.log`
   - Verify `config.json` is NOT tracked (may need `git rm --cached`)

2. **Run lock** — prevent concurrent cron + `/bookmark` overlap
   - File lock at `data/.run.lock` using `fcntl.flock()`
   - Acquired at pipeline start, released at end
   - Stale lock detection (check PID alive, auto-clear if dead)
   - Add to `__main__.py` as decorator/context manager around `cmd_fetch`, `cmd_run`

3. **Structured logging with `run_id`**
   - Generate UUID `run_id` per invocation
   - Thread through all log messages: `[run_id=abc123] Fetching bookmarks...`
   - Add `--log-file` CLI option (default: stderr only)

4. **`.env.example`** — create with placeholders:
   ```
   BIRD_CLI=bird
   TELEGRAM_BOT_TOKEN=
   TELEGRAM_CHAT_ID=
   # ANTHROPIC_API_KEY=  # Only needed for direct API analysis mode
   ```

### Tests
- Lock prevents concurrent execution
- Lock auto-clears on stale PID
- run_id appears in log output

### Exit Criteria
- `git status` shows no tracked secrets/data files
- Two concurrent `python3 -m bookmark_digest run` → second one exits cleanly with "Another run in progress"

---

## Session 2: Database Schema Migration

**Time:** ~45 min
**Risk:** Medium — modifies live data, needs migration script
**Depends on:** Session 1

### Tasks
1. **Migration script** (`bookmark_digest/migrations.py`)
   - Detect current schema version (check for `analysis` column)
   - `ALTER TABLE queue ADD COLUMN analysis TEXT`
   - `ALTER TABLE queue ADD COLUMN buttons_json TEXT`
   - `ALTER TABLE queue ADD COLUMN batch_id TEXT`
   - `ALTER TABLE queue ADD COLUMN telegram_message_id TEXT`
   - `ALTER TABLE queue ADD COLUMN error_count INTEGER NOT NULL DEFAULT 0`
   - `ALTER TABLE queue ADD COLUMN last_error TEXT`
   - Ensure `completed_at` column exists (already in schema but verify)
   - Add `engagement` column if missing
   - Create `UNIQUE` index on `(source, source_id)` — handle existing duplicates first
   - Add `schema_version` table to track migrations
   - Auto-run on `init_db()` — idempotent, safe to re-run

2. **Consolidate dedup into SQLite**
   - `bookmark-state.json` processed_ids → check against `queue.source_id` directly
   - Keep JSON file as write-through cache for fast startup, but SQLite is source of truth
   - `fetcher.py` checks `SELECT 1 FROM queue WHERE source='twitter' AND source_id=?` before inserting

3. **New lifecycle statuses + transition guards**
   - Valid transitions: `pending → analyzed → delivered → acted_on → completed`
   - Also: `pending → skipped`, `analyzed → failed`, any → `archived`
   - `update_status()` validates transition is legal, raises ValueError if not
   - Add `get_undelivered(limit)` — returns items with `status='analyzed'`
   - Add `get_next_batch(batch_size)` — returns next N undelivered, assigns `batch_id`
   - Add `mark_delivered(item_id, telegram_message_id)` — stores message ID for later edits
   - Add `update_analysis(item_id, analysis_json, buttons_json)` — saves LLM output

4. **Compact ID format**
   - Switch from `bk_{hex8}` to shorter format for callback safety
   - Use `bk{hex6}` (6 hex chars = 16M combinations, plenty) — saves 2 bytes
   - Or use auto-increment integer PK for callbacks, keep text ID for display

### Tests (TDD — write first)
- Migration runs idempotently on fresh DB
- Migration runs idempotently on v1 DB with existing data (74 items preserved)
- Lifecycle transitions: valid ones succeed, invalid ones raise ValueError
- `get_next_batch(5)` returns exactly 5, assigns same batch_id
- Duplicate `(source, source_id)` rejected
- `mark_delivered` stores telegram_message_id
- Dedup via SQLite matches dedup via JSON state file

### Exit Criteria
- Run migration on live `queue.db` — all 74 items preserved, new columns added
- All existing tests still pass (backward compat)
- New lifecycle tests pass

---

## Session 3: Analyzer Contract + SKILL.md Rewrite

**Time:** ~1 hour
**Risk:** Medium — defines the LLM↔Python interface, must get schema right
**Depends on:** Session 2

### Tasks
1. **`analyzer.py`** — the Python side of the LLM contract
   - Define analysis JSON schema (Pydantic model or jsonschema):
     ```python
     class BookmarkAnalysis(BaseModel):
         title: str                    # Descriptive title: "@username — Topic"
         summary: str                  # 2-5 sentence analysis
         category: str                 # LLM-inferred category (free-form)
         rationale: str                # Why user likely bookmarked this
         content_type: Literal["tweet", "thread", "article", "video", "podcast", "repo", "paper"]
         buttons: list[ButtonChoice]   # Selected from palette
         confidence: float             # 0-1, how confident in analysis
         sources: list[str]            # URLs consulted during analysis
         
     class ButtonChoice(BaseModel):
         text: str                     # Display text with emoji
         action: str                   # Action code from palette
     ```
   - `validate_analysis(raw_json: str) -> BookmarkAnalysis | ValidationError`
   - `save_analysis(db_path, item_id, analysis: BookmarkAnalysis)` — writes to DB
   - `get_button_palette() -> list[dict]` — returns available buttons for SKILL.md prompt
   - `get_analysis_prompt(item: dict, profile: dict) -> str` — builds the prompt template

2. **Button palette definition** (in config, not hardcoded):
   ```python
   BUTTON_PALETTE = [
       {"code": "dd", "text": "🔬 Deep Dive", "action": "deepdive", "when": "always"},
       {"code": "im", "text": "⚡ Implement", "action": "implement", "when": "has_plan"},
       {"code": "fc", "text": "📊 Fact Check", "action": "factcheck", "when": "health_claim"},
       {"code": "sn", "text": "💾 Save Notes", "action": "savenotes", "when": "always"},
       {"code": "rm", "text": "⏰ Remind Me", "action": "remind", "when": "habit_or_routine"},
       {"code": "fs", "text": "📝 Full Summary", "action": "fullsummary", "when": "long_content"},
       {"code": "rs", "text": "🔗 Read Source", "action": "readsource", "when": "has_url"},
   ]
   ```

3. **SKILL.md rewrite** — the actual "brain" instructions
   - Clear pipeline: fetch → get pending as JSON → for each item, output analysis JSON → save via CLI → deliver batch
   - Exact prompt template for analysis (including profile context injection point)
   - JSON output format with example
   - Button selection rules: "Pick 2-5 buttons from the palette based on content type"
   - Error handling: "If analysis fails, mark as failed with reason"
   - Batch delivery instructions: "Send 5 items, then footer with Next button"

4. **Deprecate `processor.py`**
   - Rename to `_processor_legacy.py`
   - Keep as fallback import in `__main__.py` for `--legacy` flag
   - New default pipeline skips it entirely

### Tests
- Valid analysis JSON validates successfully
- Invalid JSON (missing fields, wrong types, unknown buttons) raises clear errors
- `save_analysis` writes to DB and is retrievable
- Button palette round-trips through config
- Prompt generation includes profile context when available

### Exit Criteria
- `python3 -m bookmark_digest analyze --item bk_abc123 --dry-run` shows the prompt that would be sent to LLM
- Schema validation catches bad LLM output with actionable error messages

---

## Session 4: Delivery Engine

**Time:** ~1 hour
**Risk:** Medium — Telegram message formatting is fiddly
**Depends on:** Session 2, Session 3

### Tasks
1. **`delivery.py`** — replaces `digest.py`
   - `get_next_batch(db_path, batch_size=5) -> list[dict]` — fetches analyzed, undelivered items
   - `format_item(item: dict) -> str` — Telegram message text (plain text + bold markdown)
     - Line 1: emoji + category + title
     - Line 2: engagement stats (if available)
     - Lines 3-7: analysis summary
     - Last line: 🔗 URL
   - `build_button_rows(buttons_json: str) -> list[list[dict]]` — from stored analysis
     - Max 3 buttons per row
     - Multiple rows if needed
     - Compact callback_data: `q|{action_code}|{item_id}` (well within 64 bytes)
   - `format_batch_footer(batch_num, delivered_count, total_remaining) -> str`
     - "📋 Batch 1 (5 of 23) — tap below for next batch"
   - `build_next_batch_button(batch_id) -> list[list[dict]]`
     - `[{"text": "▶ Next 5", "callback_data": "q|nb|{batch_id}"}]`
   - **Message chunking** for >4096 chars:
     - Split at paragraph boundaries
     - First chunk = main message with buttons
     - Overflow = reply to first message (no buttons)
   - **Idempotent delivery state machine:**
     - Before send: `UPDATE status='sending' WHERE status='analyzed' AND id=?`
     - After send success: `UPDATE status='delivered', telegram_message_id=? WHERE id=?`
     - On send failure: `UPDATE status='analyzed' WHERE status='sending' AND id=?` (rollback)

2. **Deprecate `digest.py`**
   - Rename to `_digest_legacy.py`
   - Keep as fallback for `--legacy` flag

3. **Update `__main__.py`**
   - New command: `deliver-batch` — sends next batch of analyzed items
   - New command: `deliver-all` — sends all analyzed items in sequential batches (for cron)
   - Existing `digest` command → alias to `deliver-batch` with deprecation warning

### Tests
- `format_item` produces valid Telegram message under 4096 chars for typical items
- `format_item` chunks correctly for long items (>4096 chars)
- `build_button_rows` produces max 3 per row, multiple rows for 4+ buttons
- Compact callback_data is under 64 bytes for all palette buttons
- `get_next_batch(5)` returns 5 items, all with status='analyzed'
- Idempotent: calling deliver twice doesn't send duplicates
- Batch footer shows correct counts

### Exit Criteria
- `python3 -m bookmark_digest deliver-batch --dry-run` shows exactly 5 formatted messages with buttons
- Callback data for every button is under 64 bytes

---

## Session 5: Callback Hardening

**Time:** ~30 min
**Risk:** Low-Medium — refactoring existing module
**Depends on:** Session 4

### Tasks
1. **Compact callback format**
   - New format: `q|{action_code}|{item_id}` (e.g., `q|dd|bk3a7f`)
   - Action codes from palette: `dd`, `im`, `fc`, `sn`, `rm`, `fs`, `rs`, `nb` (next batch)
   - Total max length: `q|` (2) + code (2) + `|` (1) + item_id (8) = 13 bytes ✅ (well under 64)
   - Backward compat: also parse old `queue_{action}_{itemId}` format during transition

2. **Strict item lookup**
   - Every callback → `get_item(db_path, item_id)` → if None, return "❌ Item expired or not found"
   - Never fabricate item data from callback string alone
   - Log every callback hit: `[run_id=X] Callback: action=dd, item=bk3a7f, found=true`

3. **Next batch handler**
   - Callback `q|nb|{batch_id}` → `get_next_batch()` → deliver next 5
   - Duplicate protection: if batch already delivered (check batch_id in DB), respond "Already sent!"
   - Update batch footer of previous batch: edit message to remove "Next 5" button (using stored telegram_message_id)

4. **Deep Dive handler**
   - Callback `q|dd|{item_id}` → update item status to `acted_on`
   - Edit original Telegram message: append "⏳ Deep Dive in progress..."
   - Return item data to LLM for sub-agent spawn
   - When sub-agent completes, reply to original message with results

5. **Error responses**
   - Unknown action code → "❌ Unknown action"
   - Item not found → "❌ Item expired or not found. It may have been archived."
   - Item already acted on → "ℹ️ Already processed (status: {status})"

### Tests
- Compact format parses correctly for all palette action codes
- Old format still parses (backward compat)
- Missing item returns graceful error, not crash
- Duplicate next-batch click returns "Already sent"
- All callback_data strings under 64 bytes

### Exit Criteria
- `python3 -m bookmark_digest callback "q|dd|bk3a7f"` works correctly
- `python3 -m bookmark_digest callback "queue_deepdive_bk3a7f"` also works (backward compat)
- `python3 -m bookmark_digest callback "q|dd|nonexistent"` returns clean error

---

## Session 6: Profile System + Config Updates

**Time:** ~45 min
**Risk:** Low — additive feature, doesn't break existing code
**Depends on:** Session 3

### Tasks
1. **`profile.py`**
   - `load_profile(path) -> dict` — loads `user-profile.json`, returns empty dict if missing
   - `save_profile(path, profile: dict)` — atomic write
   - `build_profile(bookmarks: list[dict]) -> dict` — structures raw data for LLM analysis
   - `get_context(profile: dict) -> str` — returns a prompt-injectable summary string:
     ```
     User interests: AI agents (high), health/biohacking (medium), ...
     Bookmark patterns: tends to bookmark threads with actionable advice
     Analysis preferences: fact-check health claims, compare tech to current setup
     ```
   - `update_from_action(profile, item, action) -> dict` — incremental weight updates when user clicks buttons (e.g., clicking "Fact Check" on health items increases health weight)

2. **`/bookmark profile` CLI command**
   - Fetch last 200 bookmarks via `bird bookmarks --json -n 200`
   - Fetch last 200 likes via `bird likes --json -n 200` (if bird supports it)
   - Output as structured JSON for LLM to analyze
   - LLM fills in `user-profile.json` template
   - Save to `data/user-profile.json`

3. **Config updates**
   - `batch_size`: default 5
   - `analysis_model`: default "opus" (hint for cron payload)
   - `profile_path`: default "data/user-profile.json"
   - `max_analyze_per_run`: default 30 (cap for cost management)
   - `max_message_length`: default 4000 (Telegram safe limit, under 4096)
   - Update `config.example.json` with all new keys

4. **Cron payload update**
   - Update cron `c8f517a9` to specify `model: "opus"`
   - Include profile loading in cron instructions
   - Set `max_analyze_per_run` in payload

### Tests
- `load_profile` returns empty dict for missing file
- `save_profile` + `load_profile` round-trip
- `get_context` produces non-empty string for valid profile
- `update_from_action` adjusts weights
- Config validates new keys with correct types

### Exit Criteria
- `python3 -m bookmark_digest profile --dry-run` outputs the prompt for profile building
- Profile context appears in analyzer prompt when profile exists

---

## Session 7: Test Rewrite + Integration Tests + Polish

**Time:** ~1 hour
**Risk:** Low — testing and cleanup only
**Depends on:** All previous sessions

### Tasks
1. **Delete/rewrite v1-specific tests**
   - Remove tests for heuristic categorization (keyword matching)
   - Remove tests for "Skip" button behavior
   - Remove tests for monolithic digest format
   - Keep tests for fetcher, bird CLI wrapper, state file management

2. **New integration tests**
   - Full pipeline: `fetch → analyze (mock LLM) → deliver-batch → callback → status check`
   - Batch pagination: 23 items → 5 batches of 5 + remainder of 3
   - Concurrent run prevention: two processes, second one locked out
   - Error recovery: analysis fails → item marked failed → retry works

3. **Edge case tests**
   - Empty queue → "No pending items"
   - Single item → batch of 1 with no "Next" button
   - Item with >4096 char analysis → chunked correctly
   - Corrupt `user-profile.json` → graceful fallback
   - Deleted tweet (bird returns error) → item skipped with reason
   - Duplicate bookmark from bird → rejected by UNIQUE constraint

4. **Documentation**
   - `ARCHITECTURE.md` — diagram of LLM-first flow, module boundaries, data lifecycle
   - `SECURITY.md` — callback validation, no telemetry statement
   - Update `README.md` — install guide, first-run profile builder, cost warnings, architecture overview
   - Update `CHANGELOG.md` — v2.0.0 entry

5. **Final deprecation**
   - Move `processor.py` → `_processor_legacy.py`
   - Move `digest.py` → `_digest_legacy.py`
   - Bump version to `2.0.0` in `__init__.py` and `pyproject.toml`
   - Final git commit + tag: `v2.0.0`

### Exit Criteria
- All tests pass: `python3 -m pytest bookmark_digest/ -v`
- `python3 -m bookmark_digest run --dry-run` shows complete v2 pipeline
- README has install instructions a stranger could follow
- `git diff --stat v1.1.0..v2.0.0` shows clean delta

---

## Execution Strategy

Each session = one Claude Code CLI invocation:
```bash
cd ~/clawd/skills/x-bookmark-worker && claude --dangerously-skip-permissions \
  "Read IMPLEMENTATION-PLAN.md. Execute Session N. Follow TDD: write tests first, then implement. 
   Git commit after each task. Run all tests before declaring done."
```

### Session Dependencies
```
Session 1 (Safety Rails)
    ↓
Session 2 (DB Migration)
    ↓
Session 3 (Analyzer) ──→ Session 6 (Profile)
    ↓
Session 4 (Delivery)
    ↓
Session 5 (Callbacks)
    ↓
Session 7 (Tests + Polish)
```

Sessions 3 and 6 can run in parallel. Everything else is sequential.

### Rollback Plan
- Git branch `v2-implementation` before starting
- Each session commits incrementally
- If a session fails badly: `git reset --hard` to last good commit
- v1 code preserved as `_*_legacy.py` files throughout

### Post-Implementation
- Run full pipeline manually: `/bookmark` → verify 5-item batch → click buttons → verify callbacks
- Run cron once: `openclaw cron run c8f517a9 --force` → verify overnight-style batch
- Profile builder: `/bookmark profile` → verify `user-profile.json` created
- If all green: merge to master, tag v2.0.0, prep GitHub repo
