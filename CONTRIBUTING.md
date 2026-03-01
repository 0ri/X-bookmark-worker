# Contributing to X Bookmark Worker

Thank you for your interest in contributing! This document provides guidelines and instructions for contributing to the X Bookmark Worker project.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Environment Setup](#development-environment-setup)
- [Code Style](#code-style)
- [Running Tests](#running-tests)
- [Pull Request Process](#pull-request-process)
- [Reporting Bugs](#reporting-bugs)
- [Suggesting Enhancements](#suggesting-enhancements)

---

## Code of Conduct

Be respectful, constructive, and kind. We're all here to make this project better.

**Golden rules:**
- Be patient with newcomers
- Provide constructive feedback
- Focus on the code, not the person
- Respect different perspectives and experiences

If you encounter unacceptable behavior, please report it via GitHub Issues.

---

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/x-bookmark-worker.git
   cd x-bookmark-worker
   ```
3. **Create a branch** for your changes:
   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b fix/issue-123
   ```

---

## Development Environment Setup

### Prerequisites

- **Python 3.10+** (required)
- **bird CLI** (for Twitter/X bookmark fetching)
- **Git** for version control

### Installation

1. **Create a virtual environment and install:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e .
   pip install pytest
   ```

2. **Install bird CLI:**
   ```bash
   npm install -g bird-cli
   ```

3. **Set up Twitter authentication** (for testing with real bookmarks):
   ```bash
   export AUTH_TOKEN="your_token_here"
   export CT0="your_ct0_here"
   ```

4. **Initialize configuration:**
   ```bash
   bookmark-digest init
   ```

5. **Run tests to verify setup:**
   ```bash
   python3 -m pytest bookmark_digest/ -v
   ```

### Project Structure

```
x-bookmark-worker/
├── bookmark_digest/       # Main package
│   ├── __init__.py        # Package init
│   ├── __main__.py        # CLI entry point
│   ├── config.py          # Configuration loading + validation
│   ├── bird.py            # bird CLI wrapper
│   ├── fetcher.py         # Bookmark fetching + dedup
│   ├── processor.py       # Content processor + categorization
│   ├── bookmark_queue.py  # SQLite queue CRUD
│   ├── digest.py          # Telegram message formatter
│   ├── callbacks.py       # Button callback handler
│   ├── test_utils.py      # Shared test helpers
│   └── test_*.py          # Unit tests
├── data/                  # Runtime data (auto-created, gitignored)
├── config.json            # Your local config (generated via init)
├── config.example.json    # Config template with full schema
├── README.md
├── CONTRIBUTING.md        # You are here!
├── LICENSE
├── pyproject.toml
└── .gitignore
```

---

## Code Style

We follow **PEP 8** with a few project-specific conventions:

### Python Style

- **Indentation:** 4 spaces (no tabs)
- **Line length:** 88 characters (Black-compatible)
- **Quotes:** Double quotes `"` for strings
- **Type hints:** Encouraged (use `str | None` instead of `Optional[str]`)
- **Docstrings:** Use for all public functions; triple-quoted `"""`
- **Imports:** Use relative imports within the package (`from .config import ...`)

### No External Dependencies

**Critical:** This project intentionally avoids external pip dependencies to maximize portability and simplify installation.

**Use Python stdlib only:**
- Yes: `sqlite3`, `json`, `subprocess`, `re`, `pathlib`, `logging`, `secrets`, `datetime`
- No: `requests`, `beautifulsoup4`, `click`, `pydantic`, etc.

If your feature absolutely requires an external library, discuss it in an issue first.

### SQL Style

- Use **parameterized queries** (never string interpolation)
- Use **uppercase** for SQL keywords
- Use **lowercase** for column/table names

---

## Running Tests

All tests are in `bookmark_digest/` and work with pytest:

```bash
# Run all tests
python3 -m pytest bookmark_digest/ -v

# Run a specific test file
python3 -m pytest bookmark_digest/test_config.py -v

# Run a single test
python3 -m pytest bookmark_digest/test_queue.py::test_add_and_get -v
```

Tests are designed to run without the `bird` CLI installed — all external calls are mocked or skipped.

### Writing Tests

- **Location:** `bookmark_digest/test_*.py`
- **Use temp files:** Never write to the real `data/` directory in tests
- **Use helpers:** See `test_utils.py` for `temp_db()`, `temp_state()`, `sample_bookmark()`, etc.

---

## Pull Request Process

1. **Ensure tests pass:**
   ```bash
   python3 -m pytest bookmark_digest/ -v
   ```

2. **Update documentation** if you changed functionality

3. **Write a clear commit message:**
   ```
   feat: add support for Reddit bookmarks
   fix: handle null author field in tweets
   docs: improve installation instructions
   test: add test for thread detection
   ```

4. **Push to your fork** and open a Pull Request on GitHub

### PR Checklist

- [ ] Tests pass locally
- [ ] Code follows PEP 8 style
- [ ] No external dependencies added (or discussed in issue first)
- [ ] Docstrings added for new functions
- [ ] README updated if needed

---

## Reporting Bugs

**Bug report should include:**
- **Description:** What happened vs. what you expected
- **Steps to reproduce:** Minimal example
- **Environment:** OS, Python version, bird CLI version
- **Logs:** Relevant error messages (sanitize any personal data!)

---

## Suggesting Enhancements

We love new ideas! Before suggesting an enhancement:

1. **Check existing issues** to avoid duplicates
2. **Consider if it fits the project scope**
3. **Think about backward compatibility**

---

## Questions?

- **General questions:** [GitHub Discussions](https://github.com/openclaw-community/x-bookmark-worker/discussions)
- **Bugs:** [GitHub Issues](https://github.com/openclaw-community/x-bookmark-worker/issues)

---

**Thank you for contributing!**
