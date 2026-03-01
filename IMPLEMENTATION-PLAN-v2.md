# Implementation Plan v2: X Bookmark Worker — Lobster + llm-task Architecture

**Date:** 2026-07-09
**Status:** Ready for execution — council approved, blocking issues resolved
**Supersedes:** IMPLEMENTATION-PLAN.md (v1, 7-session Python-heavy plan)

---

## Architecture Shift

### v1 Plan (7 sessions, Python-heavy)
Custom Python modules for everything: `analyzer.py`, `delivery.py`, `profile.py`, `callbacks.py`, `migrations.py`. The LLM intelligence was invoked via spawned sub-agents or Poe API calls from Python. 146 tests, ~2 weeks estimated.

### v2 Plan (Lobster + llm-task, OpenClaw-native)
**Python = tiny JSON-speaking CLIs.** Lobster orchestrates the pipeline. `llm-task` handles all intelligence with schema-validated JSON output. `web_fetch` extracts linked content. Approval gates replace custom callback machinery.

**What we keep from v1 work (Sessions 1-2 already done):**
- ✅ `lock.py` — RunLock with PID-based stale detection
- ✅ `bookmark_queue.py` — SQLite queue with v2 schema (analysis, buttons_json, batch_id, etc.)
- ✅ `migrations.py` — idempotent schema migration
- ✅ `fetcher.py` — bird CLI wrapper + dedup
- ✅ `config.py` — configuration loading
- ✅ 92 passing tests

**What we replace:**
- ❌ `analyzer.py` (custom Python analysis) → `llm-task` with JSON schema
- ❌ `delivery.py` (custom Telegram formatting) → Lobster pipeline step + message tool
- ❌ `processor.py` / `digest.py` (legacy) → already deprecated
- ❌ Custom sub-agent spawning for analysis → `llm-task` plugin
- ❌ Manual callback button routing → Lobster approval gates + compact callbacks

**What's new:**
- `bookmark-pipeline.lobster` — main workflow file
- `bookmark-analyze.lobster` — analysis sub-workflow
- Small CLI wrappers that emit JSON (thin shells around existing modules)

---

## Prerequisites

### Enable Lobster + llm-task

```json
{
  "plugins": {
    "entries": {
      "llm-task": {
        "enabled": true,
        "config": {
          "defaultModel": "anthropic/claude-opus-4-6",
          "maxTokens": 4000,
          "timeoutMs": 120000
        }
      }
    }
  },
  "tools": {
    "alsoAllow": ["lobster", "llm-task"]
  }
}
```

### Install Lobster CLI
```bash
# Per https://github.com/openclaw/lobster
npm install -g @openclaw/lobster
# Verify
lobster --version
```

---

## Pipeline Design

### Main Pipeline: `bookmark-pipeline.lobster`

```yaml
name: bookmark-digest
args:
  batch_size:
    default: "5"
  limit:
    default: "50"
steps:
  - id: fetch
    command: python3 -m bookmark_digest fetch --json --limit $limit
    # Output: {"bookmarks": [...], "new_count": N, "skipped": N}

  - id: analyze
    command: python3 -m bookmark_digest build-llm-task-request --json
    stdin: $fetch.stdout
    # Reads bookmarks from stdin, builds llm-task request JSON with
    # schema, prompt, few-shot examples, and user profile context.
    # Output piped to llm-task invocation in next step.

  - id: llm-analyze
    command: openclaw.invoke --tool llm-task --action json --stdin json
    stdin: $analyze.stdout
    # Output: {"analyses": [{id, category, analysis, buttons, relevance_score}, ...]}

  - id: store
    command: python3 -m bookmark_digest store-analyses --json
    stdin: $analyze.stdout
    # Writes analyses back to SQLite queue

  - id: enrich
    command: python3 -m bookmark_digest enrich --json --batch-size $batch_size
    stdin: $store.stdout
    # For items with URLs: web_fetch content, re-analyze with full context
    # Output: {"enriched": N, "batch": [{...items ready for delivery...}]}

  - id: deliver
    command: python3 -m bookmark_digest deliver --json --batch-size $batch_size
    stdin: $enrich.stdout
    # No approval gate — cron runs overnight unattended. User controls
    # pacing via "Next 5 ▶" batch buttons in Telegram.
    # Output: {"delivered": N, "remaining": N, "batch_id": "..."}
```

### Analysis Schema (for llm-task)

```json
{
  "type": "object",
  "properties": {
    "analyses": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "item_id": { "type": "string" },
          "category": { "type": "string" },
          "why_bookmarked": { "type": "string", "maxLength": 200 },
          "analysis": { "type": "string", "maxLength": 1000 },
          "relevance_score": { "type": "number", "minimum": 0, "maximum": 1 },
          "content_type": {
            "type": "string",
            "enum": ["tweet", "thread", "article", "video", "repo", "paper", "tool", "other"]
          },
          "buttons": {
            "type": "array",
            "items": {
              "type": "string",
              "enum": ["dd", "im", "fc", "sn", "rm", "fs", "rs"]
            },
            "minItems": 1,
            "maxItems": 5
          },
          "needs_enrichment": { "type": "boolean" },
          "enrichment_urls": {
            "type": "array",
            "items": { "type": "string", "format": "uri" }
          }
        },
        "required": ["item_id", "category", "analysis", "buttons", "content_type"],
        "additionalProperties": false
      }
    }
  },
  "required": ["analyses"],
  "additionalProperties": false
}
```

### Button Palette (unchanged from v1)

| Code | Label | When |
|------|-------|------|
| `dd` | 🔬 Deep Dive | Always available |
| `im` | ⚡ Implement | Actionable idea/tool |
| `fc` | 📊 Fact Check | Health/science claims |
| `sn` | 💾 Save Notes | Worth remembering |
| `rm` | ⏰ Remind Me | Habits/routines |
| `fs` | 📝 Full Summary | Long-form content |
| `rs` | 🔗 Read Source | URL is the value |

### Callback Format: `q|{code}|{id}` (compact, ≤64 bytes)

---

---

## Artifact 1: Lifecycle Spec — State Ownership

### State Diagram

```
                    Lobster Pipeline Boundary
                    ┌─────────────────────────────────────────┐
                    │                                         │
  bird CLI          │   llm-task        web_fetch   message   │   Telegram
  ───────►  FETCH ──┼──► ANALYZE ──────► ENRICH ──► DELIVER ──┼──► buttons
                    │       │               │          │      │      │
                    └───────┼───────────────┼──────────┼──────┘      │
                            │               │          │             │
                    ════════╪═══════════════╪══════════╪═════════════╪════
                    SQLite  ▼               ▼          ▼             ▼
                    ┌──────────┐     ┌──────────┐  ┌─────────┐  ┌──────────┐  ┌──────────┐
                    │ pending  │────►│ analyzed │─►│ sending │─►│ delivered│─►│ acted_on │──► completed
                    └──────────┘     └──────────┘  └─────────┘  └──────────┘  └──────────┘
                         │                              │             │
                         └──────► skipped               │             └──► failed (→ DLQ)
                                                        └──► analyzed (rollback on send failure)
```

### Ownership Rules

| Concern | Owner | Rationale |
|---------|-------|-----------|
| **Execution flow** | Lobster | Pipeline definition, step ordering, retry, resume |
| **Data persistence** | SQLite | Bookmark content, analysis results, delivery state |
| **Status transitions** | SQLite (triggered by Lobster) | Single source of truth for item lifecycle |
| **Concurrency guard** | `lock.py` (RunLock) | Lobster doesn't prevent duplicate cron invocations |
| **Batch assignment** | SQLite (`get_next_batch`) | Atomic batch_id assignment in DB, not Lobster state |
| **Callback routing** | SQLite lookup | `q|code|id` → DB row → action handler |

### Invariants

1. **No status without persistence:** Every Lobster step that changes item state MUST call the corresponding SQLite function before proceeding. If the DB write fails, the step fails.
2. **SQLite is the recovery point:** If Lobster crashes mid-pipeline, the next run queries SQLite for `status='pending'` or `status='analyzed'` items and resumes from there. Lobster resume tokens are a bonus, not the recovery mechanism.
3. **No dual writes:** A step either writes to SQLite OR sends to Telegram, never both. The `deliver` step writes `telegram_message_id` to SQLite AND sends the message — this is the one exception, and it uses `telegram_message_id IS NULL` as the idempotency guard.
4. **Lock.py stays:** Lobster doesn't prevent two cron invocations from overlapping. RunLock ensures single-writer.

---

## Artifact 2: Idempotency Matrix

| Side Effect | Idempotency Key | Guard Mechanism | On Duplicate |
|-------------|----------------|-----------------|--------------|
| Insert bookmark to queue | `UNIQUE(source, source_id)` | SQLite constraint | `INSERT OR IGNORE` — skip silently |
| Write analysis to item | `item_id` + `status='pending'` | `UPDATE WHERE status='pending'` | Only writes if not yet analyzed |
| Enrich (web_fetch) | `item_id` + URL + `enriched_content` key | Check if analysis blob already has `enriched_content` | Skip fetch, reuse cached |
| Assign batch_id | `item_id` + `batch_id IS NULL` | `UPDATE WHERE batch_id IS NULL` | Skip if already batched |
| Set `sending` status | `item_id` + `status='analyzed'` | `UPDATE WHERE status='analyzed'` | Reject if not in `analyzed` state |
| Send Telegram message | `item_id` + `status='sending'` | Two-phase: set `sending` → send → set `delivered` | On crash: `sending` items logged as potential dupes |
| Store batch footer msg | `batch_id` + `footer_message_id` | `batches` table UPSERT | Update if exists |
| Mark delivered | `item_id` + `status='sending'` | Transition guard | Reject if not in `sending` state |
| Handle callback action | `item_id` + `action` + `status='delivered'` | `UPDATE WHERE status='delivered'` | Reject if already acted on |
| Callback reply message | `item_id` + `action` | Guarded by `mark_acted_on` transition | Second tap returns "Already processed" |
| Write to memory (Save Notes) | `item_id` in note header | Check if note file exists | Skip or append |
| Create cron (Remind Me) | Job name includes `item_id` | `cron list` check | Skip if exists |
| Profile weight update | `item_id` + `action` + `status='delivered'` | Guarded by callback transition | Accepted: dropped 0.05 increment is negligible for single-user |

### Hard Acceptance Criterion

> **Re-running any pipeline step N times from the same state produces no duplicate deliveries, no mutated completed items, and no detached callback-to-item mappings.**

---

## Artifact 3: Failure Policy Table

| Error Domain | Error Type | Retry | Backoff | Max Attempts | On Exhaust |
|-------------|------------|-------|---------|--------------|------------|
| **bird CLI** | Rate limited | Yes | 60s fixed | 3 | Skip run, alert user |
| **bird CLI** | Auth expired | No | — | 1 | Alert user, abort run |
| **llm-task** | Schema validation fail | Yes | 5s | 3 | Mark item `failed`, log to `error_count` |
| **llm-task** | Timeout (>120s) | Yes | 30s | 2 | Mark item `failed`, continue batch |
| **llm-task** | Model unavailable | Yes | 60s | 2 | Fall back to sonnet, then fail |
| **web_fetch** | Site blocked/timeout | Yes | 10s | 2 | Skip enrichment, analyze tweet text only |
| **web_fetch** | Firecrawl quota | No | — | 1 | Skip enrichment gracefully |
| **Telegram** | Rate limited (429) | Yes | Exponential 1-30s | 5 | Pause batch, resume on next trigger |
| **Telegram** | Message too long (>4096) | No | — | 1 | Truncate + "..." + "Read Source" button |
| **Telegram** | Bot blocked by user | No | — | 1 | Alert, abort delivery |
| **SQLite** | Lock timeout | Yes | 1s | 5 | Abort step, Lobster retries pipeline |
| **SQLite** | Disk full | No | — | 1 | Alert user, abort run |
| **Callback** | Item not found | No | — | 1 | Reply "Item not found" to user |
| **Callback** | Wrong status | No | — | 1 | Reply "Already processed" to user |

### Dead Letter Queue

Items that fail 3+ times get:
1. `status = 'failed'`
2. `error_count` incremented, `last_error` populated
3. Logged to `data/dlq.log` with timestamp + error
4. Excluded from future batch runs until manually reset
5. Daily DLQ summary in heartbeat if count > 0

---

## Artifact 4: Schema Contract (llm-task)

### Version: `analysis_schema_v1`

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "bookmark-analysis-v1",
  "type": "object",
  "properties": {
    "analyses": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "item_id": {
            "type": "string",
            "description": "SQLite row ID from the queue (e.g. 'bk_a7f3c2')"
          },
          "category": {
            "type": "string",
            "description": "Dynamic category inferred from content + user profile",
            "examples": ["AI/Agents", "Health/Supplements", "Programming/Rust", "Startups"]
          },
          "why_bookmarked": {
            "type": "string",
            "maxLength": 200,
            "description": "Inference of why the user bookmarked this, based on their profile"
          },
          "analysis": {
            "type": "string",
            "maxLength": 1000,
            "description": "2-4 sentence deep analysis: key takeaway, relevance, actionability"
          },
          "relevance_score": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "How relevant to user's interests (0=noise, 1=must-act)"
          },
          "content_type": {
            "type": "string",
            "enum": ["tweet", "thread", "article", "video", "repo", "paper", "tool", "other"]
          },
          "buttons": {
            "type": "array",
            "items": {
              "type": "string",
              "enum": ["dd", "im", "fc", "sn", "rm", "fs", "rs"]
            },
            "minItems": 1,
            "maxItems": 5,
            "description": "Contextual action buttons selected from the palette"
          },
          "needs_enrichment": {
            "type": "boolean",
            "description": "True if linked URL content should be fetched for deeper analysis"
          },
          "enrichment_urls": {
            "type": "array",
            "items": { "type": "string", "format": "uri" },
            "description": "URLs to fetch via web_fetch for enrichment"
          }
        },
        "required": ["item_id", "category", "analysis", "buttons", "content_type"],
        "additionalProperties": false
      }
    }
  },
  "required": ["analyses"],
  "additionalProperties": false
}
```

### Versioning Strategy

1. Schema version stored in SQLite column `analysis_schema_version` (not in JSON blob — avoids `additionalProperties: false` conflict)
2. When schema changes, create `analysis_schema_v2` — old items keep their version
3. Reader code dispatches on column value: `parse_v1()`, `parse_v2()`
4. Migration path: re-analyze items with old schema on next run (opt-in via `--reanalyze`)
5. Version pinned in `config.json`: `"analysis_schema_version": "v1"`

### Few-Shot Examples (injected into llm-task prompt)

```json
{
  "item_id": "bk_a7f3c2",
  "category": "Health/Supplements",
  "why_bookmarked": "Ori tracks supplement research, especially with peer-reviewed backing",
  "analysis": "Thread claims creatine loading (20g single dose) improves cognitive processing speed by 24.5%. The cited study (Watanabe et al. 2002) is real but small (n=45). Worth fact-checking the specific 24.5% claim and checking for replication studies.",
  "relevance_score": 0.8,
  "content_type": "thread",
  "buttons": ["fc", "sn", "dd"],
  "needs_enrichment": false,
  "enrichment_urls": []
}
```

---

## Artifact 5: Integration Diagram

### Data Flow with Ownership

```
┌─────────────────────────────────────────────────────────────────────┐
│                        LOBSTER PIPELINE                             │
│                                                                     │
│  ┌─────────┐    ┌───────────┐    ┌──────────┐    ┌──────────────┐  │
│  │  FETCH   │───►│  ANALYZE  │───►│  ENRICH  │───►│   DELIVER    │  │
│  │ (Python) │    │(llm-task) │    │(web_fetch│    │  (Python +   │  │
│  │          │    │           │    │+ llm-task)│    │   message)   │  │
│  └────┬─────┘    └─────┬─────┘    └────┬─────┘    └──────┬───────┘  │
│       │                │               │                 │          │
└───────┼────────────────┼───────────────┼─────────────────┼──────────┘
        │                │               │                 │
   ┌────▼────┐      ┌────▼────┐     ┌────▼────┐      ┌────▼────┐
   │ SQLite  │      │ SQLite  │     │ SQLite  │      │ SQLite  │
   │ INSERT  │      │ UPDATE  │     │ UPDATE  │      │ UPDATE  │
   │ pending │      │analyzed │     │analyzed+│      │delivered│
   │         │      │+analysis│     │enriched │      │+msg_id  │
   └─────────┘      └─────────┘     └─────────┘      └────┬────┘
                                                          │
                                                     ┌────▼────┐
                                                     │Telegram │
                                                     │ message │
                                                     │+buttons │
                                                     └────┬────┘
                                                          │
                          ┌───────────────────────────────┘
                          │ User taps button
                          ▼
                    ┌──────────┐
                    │ CALLBACK │  (NOT in Lobster pipeline —
                    │ HANDLER  │   triggered by Telegram webhook)
                    └────┬─────┘
                         │
                    ┌────▼─────────────────────────────────┐
                    │ Parse: q|{code}|{id}                 │
                    │ Lookup: SQLite WHERE id={id}         │
                    │ Validate: status='delivered'         │
                    │ Execute: dispatch to action handler   │
                    │ Update: SQLite → acted_on            │
                    │ Reply: Telegram reply-to-original    │
                    └──────────────────────────────────────┘
```

### Callback Chain (fully specified)

```
Telegram button tap
    │
    ▼
callback_data: "q|fc|42"          ← 10 bytes (well under 64-byte limit)
    │
    ├─► Parse: action="fc", item_id=42
    │
    ├─► SQLite: SELECT * FROM queue WHERE id=42
    │   └─► Validate: item exists AND status='delivered'
    │       └─► On fail: reply "Item not found" or "Already processed"
    │
    ├─► Dispatch: action="fc" → Fact Check handler
    │   └─► llm-task with web_search + item content
    │
    ├─► SQLite: UPDATE status='acted_on', action='fc'
    │
    └─► Telegram: reply-to original message with results
```

### Key Design Decisions

1. **Lobster does NOT own callbacks.** Callbacks are asynchronous user actions that happen outside the pipeline. They route through the existing OpenClaw callback webhook → Python handler → SQLite lookup → action dispatch. Lobster approval gates are used only for the delivery preview step (optional).

2. **No Lobster resume tokens in callback_data.** Resume tokens are large and opaque. Callback data stays as `q|{code}|{id}` (compact, debuggable, fits 64 bytes). Lobster resume tokens are only used if the pipeline itself is paused mid-delivery for user approval.

3. **web_fetch is called by the enrich step, not by llm-task.** llm-task can't call tools. The enrich step fetches URL content via CLI (`web_fetch` or `python3 -m bookmark_digest enrich`), then passes the enriched content back to llm-task for re-analysis.

4. **Profile injection is a CLI step.** `python3 -m bookmark_digest profile --context` outputs the user profile as a JSON string. This is prepended to the llm-task prompt in the Lobster workflow via shell interpolation or `$step.stdout` piping.

---

## Implementation Sessions

### Session 3: State Ownership + llm-task Schema + CLI Wrappers (1.5 hours)
**Goal:** Formalize state ownership, wire llm-task schema, make modules speak JSON.

**State ownership formalization:**
- [ ] Add `analysis_schema_v1` JSON Schema file: `schemas/bookmark-analysis-v1.json`
- [ ] Add `analysis_schema_version TEXT DEFAULT 'v1'` column to queue table (migration)
- [ ] Add `sending` status to `_VALID_TRANSITIONS`: `analyzed → sending → delivered` (B2 fix)
- [ ] Add `batches` table: `batch_id TEXT PK, footer_message_id TEXT, item_count INT, delivered_at TEXT` (B3 fix)
- [ ] Add `store_analyses(analyses: list[dict])` bulk method to `bookmark_queue.py` with idempotency guard (`UPDATE WHERE status='pending'`)
- [ ] Add `reset_failed(db_path)` → `UPDATE queue SET status='pending', error_count=0 WHERE status='failed'` (DLQ recovery)
- [ ] Add comment block documenting v1 vs v2 status lifecycle in `bookmark_queue.py`
- [ ] Keep `_processor_legacy.py` and `_digest_legacy.py` importable as fallbacks (defer removal to Session 7)

**CLI subcommands** (added to `__main__.py`):
```
python3 -m bookmark_digest fetch --json [--limit N]
python3 -m bookmark_digest store-analyses --json  (reads from stdin)
python3 -m bookmark_digest enrich --json [--batch-size N]
python3 -m bookmark_digest deliver --json [--batch-size N]
python3 -m bookmark_digest callback --json --action CODE --item-id ID
python3 -m bookmark_digest profile --json [--context | --rebuild]
python3 -m bookmark_digest reset-failed --json
```

Each subcommand: reads JSON from stdin (where applicable), writes JSON to stdout, exits 0/1, stderr only for errors.

**Tests:**
- [ ] JSON output tests for each subcommand
- [ ] Idempotency tests: `store_analyses` twice with same data → no change
- [ ] Transition guard tests: `store_analyses` on already-analyzed item → rejected

**Exit criteria:** All 6 subcommands produce valid JSON. `analysis_schema_v1` validates. Existing 92 tests still pass. Legacy modules broken.

### Session 4: Lobster + llm-task Pipeline (1.5 hours)
**Goal:** Wire the full pipeline as a `.lobster` workflow with working llm-task integration.

- [ ] Install Lobster CLI, verify on PATH
- [ ] Enable `llm-task` plugin + `lobster` tool in gateway config
- [ ] Write `workflows/bookmark-pipeline.lobster` (main workflow — fetch → analyze → enrich → deliver)
- [ ] Write analysis prompt template with few-shot examples (from Artifact 4)
- [ ] Profile injection: `$profile_context` from `python3 -m bookmark_digest profile --context` (optional — if unavailable, omit user profile section from prompt; Session 6 wires it in fully)
- [ ] Enrichment step: for items with `needs_enrichment=true`, call `web_fetch` per URL, re-analyze with content
- [ ] Delivery step: format messages, send via `message` tool, store `telegram_message_id`
- [ ] Two-phase delivery (B2 fix): set `status='sending'` → send Telegram → set `status='delivered'` + `telegram_message_id`. On crash, `sending` items are logged as potential dupes on next run.
- [ ] Idempotent delivery: skip items where `status != 'analyzed'` (already sending/delivered)
- [ ] Batch footer with "Next 5 ▶" button (`q|nb|{batch_id}`)
- [ ] Test with 5 real bookmarks end-to-end
- [ ] Verify: re-running pipeline produces zero duplicate messages

**Exit criteria:** `lobster run workflows/bookmark-pipeline.lobster` fetches, analyzes, and delivers to Telegram. Re-run is idempotent.

### Session 5: Callback Hardening + Delivery Polish (1 hour)
**Goal:** Rock-solid button handling, Telegram constraints, failure handling.

**Callback system:**
- [ ] Compact callback parser: `q|{code}|{id}` (supports all 7 codes + `nb`)
- [ ] Backward compat: also parse old `queue_{action}_{id}` format
- [ ] SQLite lookup with status validation (`status='delivered'`)
- [ ] Action dispatch for each code:
  - `dd` → spawns research sub-agent, replies when done
  - `im` → spawns coding sub-agent with implementation plan
  - `fc` → `llm-task` with web_search, replies with fact-check
  - `sn` → writes to `memory/daily/` + summary file
  - `rm` → creates one-shot cron reminder
  - `fs` → Fabric `extract_wisdom` or `llm-task` full summary
  - `rs` → `web_fetch` + `llm-task` summarize, reply with content
  - `nb` → trigger next batch delivery
- [ ] All callbacks: reply-to-original Telegram message
- [ ] Telegram rate limiting: 1 message/second within batch delivery
- [ ] Message truncation at 4000 chars + "..." + `rs` button for overflow

**Failure handling (per Artifact 3):**
- [ ] `error_count` + `last_error` populated on failures
- [ ] Items with `error_count >= 3` → `status='failed'` (DLQ)
- [ ] `data/dlq.log` written for failed items

**Exit criteria:** Every button code resolves correctly. 0% "item not found". No Telegram rate limit errors on batch of 5.

### Session 6: Profile System + Callbacks (1 hour, parallel with S5)
**Goal:** User profiling and profile-informed analysis.

- [ ] `profile.py` — `build_profile(bookmarks)`: analyze last 200 bookmarks via `llm-task`
- [ ] `user-profile.json` schema + `.gitignore` entry
- [ ] `get_context()` → returns profile summary string for prompt injection
- [ ] Profile auto-update: after callback actions, adjust topic weights (clicked `fc` on health → health weight +0.1)
- [ ] `--context` flag outputs profile as JSON for Lobster piping
- [ ] `--rebuild` flag re-bootstraps from scratch
- [ ] Tests: profile build, weight update, context output format

**Exit criteria:** Profile builds from 200 bookmarks. Weights update on actions. Context string injects into analysis prompt.

### Session 7: E2E Tests + Docs + Cron + v2.0.0 (1.5 hours)
**Goal:** Production readiness, documentation, cron cutover.

**Tests:**
- [ ] E2E integration test: full pipeline with mocked `bird` + mocked `llm-task` + mocked `message`
- [ ] Callback test for each of 8 button codes
- [ ] Batch pagination test (15 items, batch_size=5 → 3 batches)
- [ ] Idempotency replay test: run pipeline twice, assert 0 duplicate messages
- [ ] Failure mode tests: llm-task timeout, Telegram 429, SQLite lock
- [ ] DLQ test: item fails 3x → status='failed', appears in dlq.log

**Documentation:**
- [ ] Rewrite `SKILL.md` — Lobster pipeline, llm-task config, all subcommands, button palette
- [ ] Write `ARCHITECTURE.md` — integration diagram from Artifact 5, module responsibilities
- [ ] Write `SECURITY.md` — no credentials in code, .gitignore rules, callback trust boundary, DLQ

**Production cutover:**
- [ ] Update cron job `c8f517a9` to invoke Lobster pipeline
- [ ] Remove dead code: `_processor_legacy_DEAD.py`, `_digest_legacy_DEAD.py`, old `analyzer.py`
- [ ] Version bump: tag `v2.0.0`
- [ ] Final commit + push

**Exit criteria:** All tests pass (target: 200+). Cron runs successfully end-to-end. v2.0.0 tagged and pushed.

---

## Dependency Graph

```
Sessions 1-2 (DONE: lock, queue, migrations, 92 tests)
    ↓
Session 3 (state ownership + CLI wrappers)
    ↓
Session 4 (Lobster pipeline + llm-task)
    ↓
  ┌─┴─┐
  S5   S6  (parallel: callbacks + profile)
  └─┬─┘
    ↓
Session 7 (E2E tests + docs + cron + v2.0.0)
```

**Estimated total:** 5 remaining sessions, ~7 hours of sub-agent time, ~1 week calendar.

---

## What This Buys Us Over v1 Plan

| Dimension | v1 (7 sessions) | v2 (6 sessions) |
|-----------|-----------------|-----------------|
| Custom Python code | ~800 LOC new | ~300 LOC new (CLI wrappers only) |
| Analysis logic | Custom `analyzer.py` calling Poe | `llm-task` with JSON schema validation |
| Pipeline orchestration | Python `__main__.py` | Lobster `.lobster` workflow |
| Content extraction | Custom web scraping | `web_fetch` (built-in, Firecrawl fallback) |
| Approval UX | Custom callback machinery | Lobster approval gates |
| Retry/resilience | Custom retry loops | Lobster + tenacity (already in place) |
| Testability | Mock everything | CLI commands are independently testable |
| Open-source friendliness | Hardcoded Poe dependency | Any `llm-task` provider works |

**Key insight:** The v1 plan was rebuilding infrastructure that Lobster/llm-task already provide. The v2 plan focuses Python on what it's good at (plumbing) and delegates intelligence to OpenClaw's native tools.

---

## Risk Register

| Risk | Mitigation |
|------|-----------|
| Lobster not installed / unavailable | Session 1 validates; fallback to direct `llm-task` calls |
| `llm-task` output doesn't match schema | JSON Schema validation built into llm-task; retry on failure |
| Large bookmark batches exceed llm-task token limit | Chunk into groups of 10 items per llm-task call |
| Lobster approval gates don't map to Telegram buttons | Keep compact callback system for button UX; use Lobster for pipeline flow only |
| `web_fetch` blocked on some sites | Firecrawl fallback (if configured); skip enrichment gracefully |
| Opus 4.6 slow on analysis | Configurable model; can use Sonnet for speed, Opus for depth |

---

## Migration from Current State

1. v1 Sessions 1-2 work is **fully preserved** (lock, queue, migrations, tests)
2. v1 Session 3 `analyzer.py` → replaced by `llm-task` (cleaner, schema-validated)
3. v1 Sessions 4-7 → collapsed into Sessions 3-6 here (Lobster does the heavy lifting)
4. Existing `data/queue.db` (74 items) untouched — same schema
5. Cron job `c8f517a9` updated in Session 6 to use Lobster pipeline

---

## Council Review Prompt

After this plan is finalized, run the LLM council with:
- This implementation plan
- The PRD-v2.md
- A codemap of the current codebase
- The diagnostic report
- Question: "Review this Lobster-based implementation plan. Is the architecture sound? What gaps remain? What would you change?"
