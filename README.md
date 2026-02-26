# X Bookmark Worker

> **Turn Twitter/X bookmarks into actionable research.**  
> Save tweets throughout the day. Wake up to a categorized, AI-analyzed digest. One tap to act on each item.

![GitHub stars](https://img.shields.io/github/stars/openclaw-community/x-bookmark-worker?style=social)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.10+-blue.svg)

**X Bookmark Worker** is an OpenClaw skill that transforms your Twitter/X bookmarks into intelligent, categorized digests with interactive action buttons. Never lose track of saved tweets again — get AI-powered summaries, thread detection, and one-tap workflows delivered straight to Telegram.

---

## Features

- **Auto-fetch** — Pulls new bookmarks from Twitter/X via the `bird` CLI with smart deduplication
- **Thread detection** — Automatically identifies and expands multi-tweet threads
- **Smart categorization** — AI, TECH, INTERESTING, GENERAL, TOO_LONG (configurable via config.json)
- **Engagement scoring** — High-engagement tweets (viral content) flagged as INTERESTING
- **Interactive buttons** — Deep Dive, Save Notes, Skip, Backlog, Implement, Research, and more
- **Persistent queue** — SQLite-backed with WAL mode for reliable concurrent access
- **Queue stats** — View status breakdown (pending/processing/completed/skipped) at any time
- **Privacy-first** — All data stays local, no third-party APIs required
- **Zero dependencies** — Pure Python stdlib (sqlite3, subprocess, json, re)
- **Configurable** — Categories, buttons, and actions are all customizable via config.json

---

## Quick Example

Here's a real-world workflow showing how X Bookmark Worker transforms your saved tweets:

### Step 1: Fetch New Bookmarks
```bash
bookmark-digest fetch --limit 10
```
**Output:**
```
2026-01-30 10:23:15 [bookmark-digest] INFO: Fetching new bookmarks (limit=10)...
2026-01-30 10:23:18 [bookmark-digest] INFO: Processing 8 new bookmarks...
2026-01-30 10:23:19 [bookmark-digest] INFO:   ok bk_a7f3c2e1: @swyx: New Claude 4.5 Opus is incredible for...
2026-01-30 10:23:20 [bookmark-digest] INFO:   ok bk_92d8b4f6: @levelsio: Just shipped a feature in 2 hours...
...
Fetched and processed 8/8 bookmarks.
```

### Step 2: View the Digest
```bash
bookmark-digest digest
```
**Output:**
```
Twitter Bookmarks — Jan 30 (8 items)

1. [AI] @swyx: New Claude 4.5 Opus is incredible for agentic workflows
   @Swyx | 1.2K likes | 342 RTs — Claude 4.5 Opus benchmarks are insane...
   https://x.com/swyx/status/123...

2. [TECH] @levelsio: Just shipped a feature in 2 hours using Cursor AI
   @Levelsio | 2.1K likes | 567 RTs — Cursor AI + Claude is a game changer...
   https://x.com/levelsio/status/456...

3. [THREAD] @sama: Why AGI will arrive sooner than you think (1/12)
   @Sam Altman | 8.5K likes | 1.2K RTs — Thread: AGI timeline predictions...
   https://x.com/sama/status/789...

(8 items with action buttons)
```

### Step 3: Take Action via Buttons
When sent to Telegram, each item includes inline buttons:
- **Deep Dive** — Queues for deep AI research
- **Save Notes** — Extracts key points to your knowledge base
- **Skip** — Marks as not interesting

### Full Pipeline (All Steps)
```bash
bookmark-digest run
# or just:
bookmark-digest
```

---

## Quick Start

### As an OpenClaw Skill (Recommended)

1. **Install the skill:**
   ```bash
   openclaw install x-bookmark-worker
   ```

2. **Set up Twitter authentication:**
   ```bash
   # Extract cookies from your browser (logged into Twitter/X)
   # Add to your environment or .env file:
   export AUTH_TOKEN=your_twitter_auth_token
   export CT0=your_twitter_ct0_cookie
   ```

3. **Install bird CLI:**
   ```bash
   npm install -g bird-cli
   ```

4. **Initialize configuration:**
   ```bash
   bookmark-digest init
   ```

5. **Run it:**
   ```bash
   # From Telegram: "/bookmark"
   # Or manually:
   bookmark-digest
   ```

### Standalone CLI (Without OpenClaw)

1. **Clone the repo:**
   ```bash
   git clone https://github.com/openclaw-community/x-bookmark-worker.git
   cd x-bookmark-worker
   ```

2. **Install:**
   ```bash
   # Python 3.10+ required
   pip install -e .

   # Install bird CLI
   npm install -g bird-cli

   # Add Twitter auth cookies to environment
   export AUTH_TOKEN="your_auth_token"
   export CT0="your_ct0_token"
   ```

3. **Initialize and run:**
   ```bash
   # Generate config.json from template
   bookmark-digest init

   # Full pipeline: fetch -> process -> digest
   bookmark-digest run

   # Just show pending items
   bookmark-digest digest

   # View queue statistics
   bookmark-digest stats
   ```

---

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `AUTH_TOKEN` | Twitter/X authentication token cookie | Yes |
| `CT0` | Twitter/X CSRF token cookie | Yes |
| `BIRD_CLI` | Path to bird CLI executable (auto-detected if in PATH) | No |

### Config File

All categories, buttons, and actions are configurable via `config.json`. Run `bookmark-digest init` to generate a config from the template, or copy `config.example.json` and customize it.

```bash
# Generate config from template
bookmark-digest init

# View current configuration
bookmark-digest config

# Use a custom config file
bookmark-digest --config /path/to/config.json run
```

### Data Directory

All data is stored in `data/` (created automatically):
- `queue.db` — SQLite database with queue items
- `bookmark-state.json` — Deduplication state (processed tweet IDs)

### Customization

Categories and their button actions are defined in `config.json`:

```json
{
  "categories": {
    "AI": {
      "keywords": ["ai", "llm", "gpt", "claude", "model", "neural"],
      "buttons": [
        {"text": "Deep Dive", "action": "deepdive"},
        {"text": "Save Notes", "action": "savenotes"},
        {"text": "Skip", "action": "skip"}
      ]
    },
    "TECH": {
      "keywords": ["api", "framework", "library", "tool"],
      "buttons": [
        {"text": "Deep Dive", "action": "deepdive"},
        {"text": "Save Notes", "action": "savenotes"},
        {"text": "Skip", "action": "skip"}
      ]
    }
  }
}
```

See `config.example.json` for the full schema with all default categories and actions.

---

## Architecture

```
+---------------+
|   bird CLI    | <-- Twitter/X bookmark fetcher (npm package)
+-------+-------+
        |
        v
+---------------+
|  fetcher.py   | <-- Deduplication, state management
+-------+-------+
        |
        v
+----------------+
| processor.py   | <-- Content extraction, categorization, thread detection
+-------+--------+
        |
        v
+----------------+
|   queue.db     | <-- SQLite queue (pending/processing/completed/skipped)
| (bookmark_     |
|  queue.py)     |
+-------+--------+
        |
        v
+----------------+
|  digest.py     | <-- Telegram message formatting with inline buttons
+-------+--------+
        |
        v
+----------------+
|   Telegram     | <-- Deliver digest with interactive buttons
+----------------+
        |
        | (button press)
        v
+----------------+
| callbacks.py   | <-- Parse callback_data, update queue status
+----------------+
```

### Data Flow

1. **Fetch:** `bird` CLI pulls bookmarks, `fetcher.py` filters out already-processed IDs
2. **Process:** Each bookmark is analyzed by `processor.py` (URL extraction, thread detection, categorization)
3. **Queue:** Items are added to SQLite queue with status `pending`
4. **Digest:** `digest.py` formats pending items into a Telegram message with inline buttons
5. **Callback:** When user taps a button, `callbacks.py` updates the queue status and triggers actions

---

## How Categorization Works

Each bookmark is categorized based on keyword matching in the tweet text and author handle. Default categories (configurable via config.json):

| Category | Description | Keywords (examples) |
|----------|-------------|---------------------|
| **AI** | AI/ML research, models, techniques | ai, llm, gpt, claude, model, neural, prompt, fine-tune |
| **TECH** | Developer tools, frameworks, APIs | api, framework, library, tool, sdk, cli, github |
| **INTERESTING** | High engagement (>=1000 likes) | *(auto-detected based on likeCount)* |
| **TOO_LONG** | Threads or very long tweets (>1500 chars) | *(auto-detected)* |
| **GENERAL** | Everything else | *(default fallback)* |

The categorization uses **word-boundary matching** to avoid false positives (e.g., "ai" won't match "email").

Categories are fully customizable. Edit `config.json` to add, remove, or modify categories and their keyword lists.

---

## How Interactive Buttons Work

Each digest item includes action buttons. Buttons are configurable per category via config.json.

| Button | Action | Status Change | Behavior |
|--------|--------|---------------|----------|
| **Deep Dive** | `deepdive` | `pending` -> `queued` | Queues item for deep research |
| **Save Notes** | `savenotes` | `pending` -> `completed` | Immediately saves to notes/knowledge base |
| **Skip** | `skip` | `pending` -> `skipped` | Removes from digest, marks as not interesting |
| **File Away** | `fileaway` | `pending` -> `completed` | Archives without action |
| **Full Summary** | `fullsummary` | `pending` -> `processing` | Generates extended AI summary |
| **Implement** | `implement` | `pending` -> `processing` | Immediate implementation (for code/tools) |
| **Research** | `research` | `pending` -> `processing` | Deep research |
| **Backlog** | `backlog` | `pending` -> `queued` | Add to backlog for later |
| **Act On** | `acton` | `pending` -> `processing` | Context-aware execution |

**Callback flow:**
1. User taps button -> Telegram sends `callback_query`
2. `callbacks.py` parses `callback_data` (format: `queue_{action}_{itemId}`)
3. Queue status is updated in SQLite
4. Confirmation message is sent (e.g., "Queued for deep dive")
5. Items marked `processing` or `queued` are handled by background jobs

---

## Running Tests

Tests use pytest and run without the bird CLI installed:

```bash
# Install test dependencies
pip install pytest

# Run all tests
python3 -m pytest bookmark_digest/ -v

# Run a specific test file
python3 -m pytest bookmark_digest/test_config.py -v
```

---

## Queue Stats

Check the status of your bookmark queue at any time:

```bash
bookmark-digest stats
```

Output:
```
Queue Stats (42 total):
  pending: 8
  processing: 2
  queued: 5
  completed: 20
  skipped: 7
```

---

## Roadmap

### v1.2
- [ ] LLM-powered categorization (replace keyword heuristics)
- [ ] Retry mechanism for failed processing
- [ ] "Mark all as read" bulk button
- [ ] Export to Markdown/Obsidian/Notion

### v2.0
- [ ] Multi-source support (Reddit, YouTube, GitHub, Hacker News, Pocket)
- [ ] Background worker for queued actions (deepdive, research, etc.)
- [ ] Engagement scoring with log scaling
- [ ] Link expansion (fetch page titles, detect GitHub/arXiv/YouTube)
- [ ] Digest pagination for high-volume users

---

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for:
- How to set up your development environment
- Code style guidelines
- How to run tests
- Pull request process

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Acknowledgments

- **[bird-cli](https://github.com/steipete/bird)** — Twitter/X API client by @steipete
- **OpenClaw** — AI agent framework powering this skill
- **The community** — Feature requests, bug reports, and contributions

---

## Support

- **Issues:** [GitHub Issues](https://github.com/openclaw-community/x-bookmark-worker/issues)
- **Discussions:** [GitHub Discussions](https://github.com/openclaw-community/x-bookmark-worker/discussions)

---

**Made with care for people who bookmark too much and read too little.**
