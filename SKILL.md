---
name: x-bookmark-worker
aliases: [bookmark]
description: Process Twitter/X bookmarks into analyzed digests with interactive Telegram buttons. Fetches, categorizes, summarizes, and queues bookmarks for action.
user-invocable: true
---

# X Bookmark Worker v2

**LLM-first bookmark processor**: Fetches Twitter/X bookmarks via `bird` CLI, performs deep AI analysis via `llm-task`, and delivers batches of 5 items to Telegram with smart action buttons.

## Invocation

- `/bookmark` or `process my bookmarks`

---

## Architecture

The v2 pipeline uses **Lobster** for orchestration and **llm-task** for analysis. Python handles fetch/dedup/delivery, the LLM handles all analysis and decision-making.

```
bird CLI → fetch → build-llm-task-request → [llm-task] → store-analyses → deliver
                                                                              ↓
                                                              Telegram messages with buttons
                                                                              ↓
                                                              callback-v2 → action dispatch
```

### Pipeline File

`workflows/bookmark-pipeline.lobster` defines the 4-step pipeline:

1. **fetch** — Pull new bookmarks from Twitter/X, dedup, store as `pending`
2. **build-prompt** — Build the LLM analysis prompt with schema + profile context
3. **store** — Store LLM analysis results, transition items to `analyzed`
4. **deliver** — Two-phase delivery to Telegram (analyzed → sending → delivered)

The LLM analysis step runs **between** build-prompt and store — the calling agent invokes `llm-task` with the prompt output and pipes the result to store.

### Item Lifecycle (FSM)

```
pending → analyzed → sending → delivered → acted_on → completed
   ↓                    ↓
 skipped            analyzed (rollback on send failure)
   ↓
 failed (DLQ after 3 errors, recoverable via reset-failed)
```

---

## CLI Reference

All commands support `--json` for JSON output and `--verbose` for debug logging.

### Pipeline Commands (Simplified)

```bash
# Combined fetch + prep: fetches bookmarks, inserts to DB, builds llm-task prompt
python3 -m bookmark_digest fetch-and-prep --json [--limit N]

# Combined analyze + deliver: reads analysis from stdin, stores, delivers to Telegram
echo '{"analyses": [...]}' | python3 -m bookmark_digest analyze-and-deliver --json [--batch-size 5]
```

### Pipeline Commands (Granular)

```bash
# Fetch new bookmarks and queue them
python3 -m bookmark_digest fetch --json [--limit N]

# Build LLM analysis prompt (reads fetch JSON from stdin)
echo '{"items": [...]}' | python3 -m bookmark_digest build-llm-task-request --json

# Store LLM analysis results (reads analysis JSON from stdin)
echo '{"analyses": [...]}' | python3 -m bookmark_digest store-analyses --json

# Deliver next batch to Telegram (two-phase: analyzed → sending → delivered)
python3 -m bookmark_digest deliver --json [--batch-size 5]
```

### Callback & Action Commands

```bash
# Handle v2 button callback (recommended)
python3 -m bookmark_digest callback-v2 --action dd --item-id bk_abc123

# Handle raw callback string (legacy + v2 format)
python3 -m bookmark_digest callback "q|dd|bk_abc123"
```

### Profile Commands

```bash
# Show current profile
python3 -m bookmark_digest profile-v2 --json

# Output profile context string for LLM prompt injection
python3 -m bookmark_digest profile-v2 --context

# Rebuild profile from queue DB bookmarks
python3 -m bookmark_digest profile-v2 --rebuild [--limit 200]
```

### Utility Commands

```bash
# Queue statistics
python3 -m bookmark_digest stats --json

# Reset failed/DLQ items back to pending
python3 -m bookmark_digest reset-failed

# Fix items stuck in transitional states (queued→pending, sending→analyzed, triaged→completed)
python3 -m bookmark_digest fix-stuck --json

# Show current configuration
python3 -m bookmark_digest config --json

# Initialize config.json from template
python3 -m bookmark_digest init [--force]
```

---

## Callback Button Codes

Telegram buttons use compact callback format: `q|{code}|{item_id}` (max 64 bytes).

| Code | Button | Action | When to Use |
|------|--------|--------|-------------|
| `dd` | 🔬 Deep Dive | Research sub-agent | In-depth content worth exploring |
| `im` | ⚡ Implement | Coding sub-agent | Code/tool with implementation plan |
| `fc` | 📊 Fact Check | Web search + LLM | Health claims, stats, "studies show" |
| `sn` | 💾 Save Notes | Write to daily notes | Reference material worth saving |
| `rm` | ⏰ Remind Me | Cron job spec | Habits, routines, time-based actions |
| `fs` | 📝 Full Summary | fabric/llm-task | Long threads, complex articles |
| `rs` | 🔗 Read Source | web_fetch spec | Linked article/paper/repo |
| `nb` | ▶ Next Batch | Trigger next delivery | Batch footer button |

### Button Selection Rules

1. Always include at least `dd` or `sn`
2. Include `fc` for health/science claims
3. Include `im` for actionable code/tools
4. Include `fs` for long-form content (10+ tweets)
5. Choose 2-5 buttons, ordered by likely user priority

---

## Analysis Schema

LLM analysis output must match `schemas/bookmark-analysis-v1.json`:

```json
{
  "analyses": [
    {
      "item_id": "bk_a7f3c2",
      "category": "AI/Agents",
      "analysis": "2-4 sentence analysis with key takeaway and actionability.",
      "why_bookmarked": "Inference of why user saved this (max 200 chars)",
      "relevance_score": 0.85,
      "content_type": "thread",
      "buttons": ["dd", "im", "rs"],
      "needs_enrichment": true,
      "enrichment_urls": ["https://example.com/article"]
    }
  ]
}
```

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `item_id` | string | Queue item ID (e.g., `bk_a7f3c2`) |
| `category` | string | Dynamic category (e.g., "AI/Agents", "Health/Supplements") |
| `analysis` | string | 2-4 sentence analysis (max 1000 chars) |
| `buttons` | array | 1-5 action codes from the palette |
| `content_type` | enum | tweet, thread, article, video, repo, paper, tool, other |

### Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `why_bookmarked` | string | Why user bookmarked this (max 200 chars) |
| `relevance_score` | float | 0.0 (noise) to 1.0 (must-act) |
| `needs_enrichment` | boolean | Whether to fetch linked URLs |
| `enrichment_urls` | array | URLs to fetch for deeper analysis |

---

## Profile System

The profile tracks user interests and adjusts weights based on callback actions.

**Profile location:** `data/profile.json`

### Profile Schema

```json
{
  "version": "v1",
  "topics": {"AI/Agents": 0.8, "Health": 0.5, "Rust": 0.3},
  "content_types": {"thread": 0.6, "tweet": 1.0},
  "generated_at": "2026-02-26T12:00:00Z",
  "bookmark_count": 50
}
```

### Weight Updates

When a user taps a callback button, the category weight is boosted:

| Action | Weight Increment |
|--------|-----------------|
| `fc` (Fact Check) | +0.05 |
| `im` (Implement) | +0.04 |
| `dd` (Deep Dive) | +0.03 |
| `sn` (Save Notes) | +0.02 |
| `fs` (Full Summary) | +0.02 |
| `rm` (Remind Me) | +0.02 |
| `rs` (Read Source) | +0.01 |

All weights are normalized to 0-1 after each update.

### Profile Context in Analysis

When `data/profile.json` exists, `build-llm-task-request` injects a context string into the LLM prompt:
```
User interests: AI/Agents (high), Health (medium), Rust (low).
```

---

## Two-Phase Delivery Protocol

Delivery uses a two-phase commit to prevent duplicate Telegram messages:

1. **Phase 1**: `analyzed` → `sending` (via `set_sending`)
   - Marks intent to send. If the process crashes here, item stays in `sending`
2. **Phase 2**: `sending` → `delivered` (via `mark_delivered_with_message`)
   - Stores `telegram_message_id` and `batch_id` for later editing/replying

If Phase 2 fails, the item stays in `sending`. On retry, `set_sending` returns False (idempotent guard), preventing duplicate sends.

---

## Troubleshooting

### Check queue status
```bash
python3 -m bookmark_digest stats --json
```

### Items stuck in `sending`
Items stuck in `sending` state indicate a delivery failure between phases. To unstick:
1. Check the item: find it via stats
2. Either complete delivery or rollback to `analyzed`

### Items in DLQ (failed)
```bash
# Check DLQ log
cat data/dlq.log

# Reset all failed items back to pending
python3 -m bookmark_digest reset-failed
```

### No new bookmarks
- Verify bird CLI auth: `bird bookmarks --json -n 1`
- Check `data/bookmark-state.json` for processed IDs (dedup window)
- Delete state file to re-fetch all: `rm data/bookmark-state.json`

### Run lock preventing execution
- Check for stale lock: `ls -la data/.run.lock`
- If process crashed, remove manually: `rm data/.run.lock`

### Database issues
- DB location: `data/queue.db`
- Schema version: check `schema_version` table (current: v3)
- Migrations run automatically on init

---

## Data Storage

| File | Purpose |
|------|---------|
| `data/queue.db` | SQLite queue (items, analysis, status, batches) |
| `data/bookmark-state.json` | Last fetch timestamp and processed IDs |
| `data/profile.json` | User interest profile with topic weights |
| `data/.run.lock` | Run lock to prevent concurrent execution |
| `data/dlq.log` | Dead letter queue log (items that failed 3+ times) |

---

## Dependencies

- **bird CLI** — Twitter/X API wrapper (`npm install -g bird-cli`)
  - Requires auth cookies: `AUTH_TOKEN` + `CT0` in `~/.clawdbot/.env`
- **Python 3.10+** — for dataclasses, type hints, and match statements
- **SQLite** — queue database (built-in)
- **Lobster** — pipeline orchestration (optional, for automated runs)

---

## Safety Notes

### Bird CLI
- **NEVER use `bird tweet` or `bird reply`** — read-only operations ONLY
- Safe commands: `bird read`, `bird thread`, `bird bookmarks`, `bird likes`

### Concurrent Runs
- Run lock prevents overlapping cron + manual `/bookmark` invocations
- If locked, CLI exits cleanly with "Another run in progress"

### Idempotency
- Re-running analysis on pending items overwrites; analyzed items are skipped
- Re-running delivery on already-delivered items is a no-op
- Safe to retry any pipeline step

---

## Example Session

```
User: /bookmark

Agent:
# Step 1: Fetch bookmarks and build LLM prompt
→ python3 -m bookmark_digest fetch-and-prep --json --limit 30
  → Fetched 12 new bookmarks, 15 total pending
  → Returns: prompt, schema, and bookmark data for llm-task

# Step 2: Pipe prompt to llm-task for analysis (done by calling agent)
→ llm-task --schema schemas/bookmark-analysis-v1.json < prompt.json
  → LLM analyzed 15 bookmarks

# Step 3: Store results and deliver first batch
→ echo '<analysis_json>' | python3 -m bookmark_digest analyze-and-deliver --json --batch-size 5
  → Stored 15 analyses, delivered batch 1 (5 items) to Telegram with action buttons

User clicks "▶ Next 5":
→ python3 -m bookmark_digest callback-v2 --action nb --item-id batch_abc123
  → Sent batch 2 (5 items) to Telegram

User clicks "🔬 Deep Dive" on an item:
→ python3 -m bookmark_digest callback-v2 --action dd --item-id bk_a7f3c2
  → Returns: {"action": "deep_dive", "agent": "research", ...}
  → Agent spawns research sub-agent for deep dive

Done. 15 bookmarks processed, 10 delivered, 5 remaining.
```

---

## Version History

- **v2.0.0** — Lobster pipeline, llm-task analysis, two-phase delivery, callback system, profile weights, DLQ
- **v1.x** — Heuristic categorization, monolithic digest (removed)
