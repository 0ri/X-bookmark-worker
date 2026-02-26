# Changelog

## v1.1.0 (2026-02-22)

### Open-Source Refactor
- Rebrand from internal skill to OpenClaw community project
- All categories, buttons, and actions are now configurable via `config.json`
- Default categories: AI, TECH, INTERESTING, GENERAL (plus auto-detected THREAD, TOO_LONG)
- Removed hardcoded internal categories
- Added `config.example.json` with full schema documentation
- Updated all documentation for open-source publishing

### Package Restructure
- Renamed `scripts/` to `bookmark_digest/` for proper pip installability
- Added `__init__.py` with version metadata
- All imports converted to relative imports
- Fixed entry point: `bookmark_digest.__main__:main`
- Package installs cleanly via `pip install -e .`

### CLI Changes
- New command: `bookmark-digest init` — generate config from template
- New command: `bookmark-digest config` — show current configuration
- New flag: `--config PATH` — use a custom config file
- New flag: `--dry-run` — preview without marking items triaged
- New flag: `--json` — machine-readable output for all commands

### Config System
- Config validation with clear error messages on invalid structure
- `show_engagement` and `show_urls` digest display settings wired to digest output
- `fetch.dedup_window` properly threaded to fetcher module
- `bird_cli` from config properly passed to bird CLI wrapper
- Three-tier loading: defaults → config.json → environment variables

### Bug Fixes
- Fixed double title in processor summary construction
- Fixed MarkdownV2 header formatting (single `*` for bold per Telegram spec)
- Fixed categorize() fallback to use explicit defaults instead of throwaway Config
- Improved dedup ID lookup from O(n) to O(1) using set

### Quality
- 56 passing tests across 6 test suites
- GitHub Actions CI for Python 3.10/3.11/3.12
- MIT License (OpenClaw Contributors)
- CONTRIBUTING.md with development setup and PR process

---

## v1.0.0 (2026-01-30)

### Features
- Fetch Twitter/X bookmarks via bird CLI
- AI-powered categorization (AI, TECH, INTERESTING, GENERAL, TOO_LONG)
- Word-boundary regex matching for accurate categorization
- Interactive Telegram digest with inline action buttons
- SQLite queue with deduplication and archival
- Thread detection and full thread fetching
- Standalone CLI mode (`python -m bookmark_digest`)
- Multi-source configuration (env vars, config file, defaults)
- Structured logging with configurable levels
- Retry logic with exponential backoff
- Graceful degradation on errors

### Architecture
- Modular design: fetcher -> processor -> queue -> digest -> callbacks
- Zero external dependencies (Python stdlib only)
- Works as OpenClaw skill AND standalone CLI
- SQLite WAL mode for concurrent access
- Atomic state file updates with corruption recovery
- Prefixed queue IDs by source type (bk_, rd_, yt_, etc.)
