# PRD: X Bookmark Worker v2 — Intelligent Second Brain

**Date:** 2026-02-26
**Author:** Ori Neidich + Clawdio
**Status:** Draft

---

## Problem Statement

When scrolling through Twitter/X, I frequently bookmark tweets that catch my attention — health claims, AI tool announcements, OpenClaw features, coding techniques, interesting threads, long videos, research papers. Currently, these bookmarks accumulate and either:

1. **Never get processed** — I forget what I bookmarked and why
2. **Get processed inconsistently** — the bookmark worker's output quality varies wildly between runs because the analysis logic lives in ephemeral LLM sessions with no persistent intelligence
3. **Produce overwhelming digests** — 20-30 items dumped at once, impossible to act on from a mobile Telegram UI
4. **Lose button associations** — clicking "Deep Dive" or "Implement" returns errors because the system lost track of which button connects to which tweet
5. **Offer generic, fixed actions** — every item gets the same 3 buttons regardless of what would actually be useful

The whole point of bookmarking is to **not interrupt my scroll** — I shouldn't have to stop, open Telegram, write a custom message, and explain what I want. Bookmark it, move on, and trust that my AI second brain will figure out why I found it interesting and what to do about it.

## Solution

An intelligent bookmark processing pipeline that:

1. **Fetches** new bookmarks from Twitter/X via the `bird` CLI
2. **Deeply analyzes** each bookmark using a state-of-the-art LLM (Opus 4.6) that:
   - Reads the full tweet AND entire thread (not just the head tweet)
   - Follows and summarizes linked content (articles, repos, videos)
   - Infers *why the user bookmarked this* based on their known interests and persona
   - Performs proactive research (fact-checking health claims, comparing tools to existing setup, summarizing long-form content)
3. **Delivers** a paginated Telegram digest in manageable batches (default: 5 items)
4. **Offers contextual actions** via inline buttons that change based on what makes sense for each item
5. **Maintains rock-solid button↔item tracking** — every button click resolves to the correct item, always

## User Stories

1. As a Twitter user, I want my bookmarks automatically processed overnight so I wake up to an actionable digest without any manual effort.

2. As a user, I want to trigger bookmark processing on-demand via `/bookmark` so I can process bookmarks whenever I want, not just on the cron schedule.

3. As a user, I want the system to read the FULL thread (not just the bookmarked tweet) so I get complete context for multi-tweet threads.

4. As a user, I want linked content (articles, GitHub repos, YouTube videos) to be fetched and summarized inline so I don't have to click through to understand the bookmark.

5. As a user, I want health claims to be automatically fact-checked against peer-reviewed research so I can quickly assess if a bookmarked health tweet is credible.

6. As a user, I want OpenClaw/agent-related bookmarks to include a comparison against my current setup so I can see what's new vs what I already have.

7. As a user, I want long videos and podcasts to be transcribed and summarized so I can get key takeaways without watching the full content.

8. As a user, I want implementation plans for technical bookmarks so I can decide with one tap whether to build something.

9. As a user, I want the digest delivered in batches of N items (configurable, default 5) so I'm not overwhelmed by a wall of 20-30 messages.

10. As a user, I want a "Show Next Batch" button after each batch so I can control the pace of my review.

11. As a user, I want each digest item to be a separate Telegram message so I can reply directly to any item to give feedback or instructions.

12. As a user, I want contextual action buttons that change based on the content type — not the same 3 buttons on every item.

13. As a user, I want buttons to display in rows that don't truncate text — if 4-5 buttons are needed, use multiple rows of 2-3 buttons each.

14. As a user, I do NOT want a "Skip" button — I can simply scroll past items I'm not interested in.

15. As a user, I want an "Implement" button on any item where the system proposes a plan, so I can kick off implementation with one tap.

16. As a user, I want to reply to any digest item message to give custom instructions (modify a plan, ask a follow-up question, etc.) instead of relying on a "Modify" button.

17. As a user, I want every button click to resolve correctly to its item — 100% of the time, no "item not found" errors, no acting on the wrong tweet.

18. As a user, I want the system to build a profile of my interests from my past bookmarks and likes so it can better predict why I bookmarked something.

19. As a user, I want categories to be dynamic and inferred by the LLM rather than hardcoded keywords so the system adapts to new topics I start caring about.

20. As a user, I want a "Deep Dive" button that spawns an async research sub-agent and sends me results when done.

21. As a user, I want a "Save to Notes" button that extracts key points and saves them to my memory system.

22. As a user, I want a "Remind Me" button for health/exercise items that creates a cron reminder.

23. As a user, I want a "Fact Check" button for health/science claims that triggers a Perplexity-powered research pipeline.

24. As a user who may publish this skill on GitHub, I want all personal data (chat IDs, file paths, categories) to be configurable, not hardcoded.

25. As an open-source user installing this skill, I want a first-run "profile builder" that analyzes my recent bookmarks/likes to bootstrap category detection.

26. As a user, I want the system to use a `user-profile.json` that captures my interests, preferences, and recurring topics so analysis is personalized across sessions.

27. As a user, I want queue items to have a clear lifecycle (pending → analyzed → delivered → acted-on → completed) so nothing falls through the cracks.

28. As a user, I want to see a completion summary after each batch ("5/23 reviewed, 18 remaining") so I know how much is left.

## Implementation Decisions

### Architecture: LLM-First with Python Infrastructure

The Python CLI handles deterministic infrastructure:
- Fetching bookmarks from Twitter via `bird` CLI
- Deduplication against processed-ID state
- SQLite queue storage (insert, query, status updates)
- Button callback parsing and routing
- Configuration loading

The LLM handles all intelligence:
- Content analysis and summarization
- Thread reading and URL content extraction
- Category inference (dynamic, not keyword-based)
- Button selection per item (contextual)
- Research and fact-checking
- Telegram message composition and delivery

**Key principle:** The Python code never tries to be smart. It's plumbing. All "thinking" happens in the LLM phase.

### Module Design

**Module 1: Fetcher** (`fetcher.py`)
- Calls `bird bookmarks --json -n <limit>`
- Deduplicates against `bookmark-state.json`
- Returns raw bookmark dicts
- No changes needed from current implementation

**Module 2: Queue** (`bookmark_queue.py`)
- SQLite CRUD with WAL mode
- Schema: id, source, source_id, canonical_url, title, category, status, action, summary, analysis (NEW), raw_content, engagement, buttons_json (NEW), batch_id (NEW), created_at, updated_at, triaged_at, completed_at
- New fields: `analysis` (LLM-generated deep analysis), `buttons_json` (stored button config per item), `batch_id` (which delivery batch)
- Lifecycle statuses: `pending` → `analyzed` → `delivered` → `acted_on` → `completed` / `skipped`
- `get_pending()` returns items with `status = 'pending'`
- `get_undelivered()` returns items with `status = 'analyzed'` not yet sent to Telegram
- `get_batch()` returns next N undelivered items

**Module 3: Analyzer** (`analyzer.py`) — NEW, replaces `processor.py`
- Called by the LLM, not by Python
- Provides structured prompts/templates for the LLM to fill
- Stores analysis results back to queue via `update_analysis()`
- The actual analysis logic is in SKILL.md instructions, not Python

**Module 4: Digest Delivery** (`delivery.py`) — NEW, replaces `digest.py`
- `get_next_batch(batch_size=5)` → returns N analyzed items
- `format_item(item)` → returns message text (Telegram-ready)
- `get_item_buttons(item)` → returns button rows from `buttons_json`
- `mark_delivered(item_ids, batch_id)` → updates status
- `format_batch_footer(delivered, remaining)` → "5/23 reviewed, Show Next ▶"

**Module 5: Callbacks** (`callbacks.py`)
- Parse `queue_{action}_{itemId}` format
- Look up item from SQLite by ID
- Return item data + action config for LLM to execute
- **Critical:** IDs come from SQLite, not ad-hoc generation. Every callback_data references a real DB row.

**Module 6: User Profile** (`profile.py`) — NEW
- Loads/saves `user-profile.json`
- Stores: interest categories, topic weights, bookmark patterns
- `build_profile(bookmarks)` — analyze N bookmarks to bootstrap
- `get_context()` — returns profile summary for LLM prompt injection

**Module 7: Config** (`config.py`)
- Existing config system (config.json + env vars + defaults)
- New config keys: `batch_size` (default 5), `analysis_model` (default "opus"), `profile_path`
- Categories become optional hints, not rigid classifiers
- Actions and buttons defined as a palette the LLM can pick from

### Button Design

Buttons are NOT fixed per category. The LLM selects from a palette based on its analysis:

**Available button palette:**
| Button | When to show |
|--------|-------------|
| 🔬 Deep Dive | Always available — spawns async research |
| ⚡ Implement | When a plan or actionable idea is proposed |
| 📊 Fact Check | Health/science claims that need verification |
| 💾 Save Notes | Any item worth remembering |
| ⏰ Remind Me | Health routines, exercises, habits |
| 📝 Full Summary | Long-form content (videos, podcasts, articles) |
| 🔗 Read Source | When the linked content is key |

**Layout rules:**
- Max 3 buttons per row
- Use multiple rows if needed (2 rows of 2-3 is fine)
- No "Skip" button — scrolling past = skipping
- No "Modify" button — reply-to-message is the modify UX

### Batch Delivery

1. LLM analyzes ALL pending items in one pass (or multiple passes for large batches)
2. Analysis stored to DB with `status = 'analyzed'`
3. Delivery happens in batches:
   - Send items 1-5 as individual messages
   - Send batch footer: "📋 Batch 1/5 (5 of 23) — tap below for next batch"
   - Footer has button: `[▶ Next 5]` with callback `queue_nextbatch_{batch_id}`
4. User reviews at their own pace
5. When "Next 5" is tapped, send items 6-10 + new footer
6. This continues until all items delivered

### Cron Integration

The cron job (`c8f517a9`) payload should be updated to:
1. Specify `model: "opus"` for Opus 4.6 quality
2. Include user profile context
3. Instruct: fetch → analyze ALL → deliver first batch of 5
4. Remaining batches delivered on-demand via "Next 5" button

### Profile Builder (First-Run / Refresh)

Command: `/bookmark profile` or `python3 -m bookmark_digest profile`

1. Fetch last 200 bookmarks + last 200 likes via `bird`
2. Pass to LLM for analysis: "What patterns do you see? What topics does this person care about?"
3. Output structured `user-profile.json`:
   ```json
   {
     "interests": [
       {"topic": "AI agents & automation", "weight": 0.9, "keywords": ["openclaw", "claude", "agent"]},
       {"topic": "Health & biohacking", "weight": 0.7, "keywords": ["supplement", "study", "protocol"]},
       ...
     ],
     "bookmark_patterns": {
       "tends_to_bookmark": "threads with actionable advice, health claims, tool announcements",
       "rarely_bookmarks": "memes, political content, self-promotion"
     },
     "analysis_preferences": {
       "health": "fact-check against peer-reviewed literature",
       "tech": "compare against current setup, propose implementation",
       "content": "summarize key takeaways, skip fluff"
     }
   }
   ```

## Testing Decisions

### What makes a good test
Tests should verify **external behavior through the public interface**, not implementation details. If the internal algorithm changes but the output is correct, tests should still pass. Mock external dependencies (bird CLI, Telegram API) but don't mock internal module interactions.

### Modules to test

1. **Queue module** — CRUD operations, lifecycle transitions, batch retrieval, ID stability
   - Test: add item → get pending → mark analyzed → get undelivered → mark delivered → verify lifecycle
   - Test: callback resolves to correct item
   - Test: batch_size pagination returns correct chunks
   - Test: duplicate source_id rejected

2. **Fetcher module** — dedup logic, state file management
   - Test: new bookmarks filtered against processed set
   - Test: state file cap (rolling window)
   - Test: corrupt state file recovery

3. **Delivery module** — message formatting, button layout, batch footer
   - Test: buttons_json round-trips correctly
   - Test: batch footer shows correct counts
   - Test: multi-row button layout when >3 buttons

4. **Callback module** — parsing, routing
   - Test: valid callback parsed correctly
   - Test: invalid callback returns error
   - Test: item lookup by ID succeeds

5. **Config module** — loading priority (env > file > defaults)
   - Test: batch_size configurable
   - Test: validation catches bad config

### Prior art
56 existing unit tests in the current codebase (test_*.py files). Follow the same pytest + unittest patterns.

## Out of Scope

- **Multi-source support** (Reddit, YouTube, newsletters) — future v2
- **Web UI or dashboard** — Telegram-only for now
- **Real-time webhook processing** — batch processing only (cron + on-demand)
- **Automatic bookmark deletion** from Twitter after processing
- **Collaborative features** — single-user tool
- **LLM-powered auto-categorization in Python** — categorization happens in the LLM phase, not via embeddings or classifiers in Python code
- **Payment or rate limiting** — personal tool / open-source freeware

## Further Notes

### Why LLM-First?

The previous architecture tried to be clever in Python (keyword categorization, heuristic summarization). This produced fast but shallow results. The whole value proposition is that a state-of-the-art LLM analyzes your bookmarks with the same intelligence you'd bring — it understands context, nuance, and can do genuine research. The Python layer should be invisible plumbing.

### The Reply-to-Message UX

Telegram's reply-to-message feature is the best "modify" UX because:
- It preserves context (the item being discussed)
- It doesn't require a button (which just asks "what do you want to change?")
- The LLM can see the quoted message and understand what's being referenced
- It works naturally in the chat flow

### Migration from v1

1. Existing `queue.db` data should be preserved (add new columns, don't drop tables)
2. Old `processor.py` summarization can stay as fallback for when LLM analysis isn't available
3. Old `digest.py` formatting replaced by new `delivery.py`
4. `config.json` remains backward compatible — new keys have defaults

### Open-Source Considerations

- `user-profile.json` should be in `.gitignore`
- `config.json` should be in `.gitignore` (use `config.example.json`)
- `data/` directory should be in `.gitignore`
- README should document the profile builder as the recommended first step
- SKILL.md is the "brain" — document that this skill requires an LLM with tool-use capabilities
