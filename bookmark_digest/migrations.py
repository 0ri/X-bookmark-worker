"""
Database schema migrations for X Bookmark Worker.

Handles incremental schema upgrades with idempotent, safe operations.
"""

import logging
import sqlite3
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Schema version history
# v1: Initial schema (id, source, source_id, ... completed_at)
# v2: Add analysis, buttons_json, batch_id, telegram_message_id, error_count, last_error, engagement


@contextmanager
def _get_connection(db_path: str):
    """Context manager for SQLite connections."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Check if schema_version table exists and return current version.
    
    Args:
        conn: Active SQLite connection
        
    Returns:
        Schema version number (0 if no schema_version table exists)
    """
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    )
    if cursor.fetchone() is None:
        return 0
    
    cursor = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
    row = cursor.fetchone()
    return row[0] if row else 0


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    return column in columns


def _index_exists(conn: sqlite3.Connection, index_name: str) -> bool:
    """Check if an index exists."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,)
    )
    return cursor.fetchone() is not None


def migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Migrate from schema v1 to v2.
    
    Adds columns for analysis workflow:
    - analysis: LLM-generated analysis JSON
    - buttons_json: Button configuration for Telegram
    - batch_id: Groups items delivered together
    - telegram_message_id: Track sent messages for editing
    - error_count: Failure tracking (renamed from attempt_count)
    - last_error: Most recent error message
    - engagement: Twitter engagement metrics
    
    Creates UNIQUE index on (source, source_id) for deduplication.
    
    This migration is idempotent - safe to run multiple times.
    
    Args:
        conn: Active SQLite connection
    """
    logger.info("Running migration v1 → v2...")
    
    # Add new columns if they don't exist
    if not _column_exists(conn, "queue", "analysis"):
        logger.info("Adding column: analysis")
        conn.execute("ALTER TABLE queue ADD COLUMN analysis TEXT")
    
    if not _column_exists(conn, "queue", "buttons_json"):
        logger.info("Adding column: buttons_json")
        conn.execute("ALTER TABLE queue ADD COLUMN buttons_json TEXT")
    
    if not _column_exists(conn, "queue", "batch_id"):
        logger.info("Adding column: batch_id")
        conn.execute("ALTER TABLE queue ADD COLUMN batch_id TEXT")
    
    if not _column_exists(conn, "queue", "telegram_message_id"):
        logger.info("Adding column: telegram_message_id")
        conn.execute("ALTER TABLE queue ADD COLUMN telegram_message_id TEXT")
    
    if not _column_exists(conn, "queue", "error_count"):
        logger.info("Adding column: error_count")
        conn.execute("ALTER TABLE queue ADD COLUMN error_count INTEGER NOT NULL DEFAULT 0")
    
    if not _column_exists(conn, "queue", "last_error"):
        logger.info("Adding column: last_error")
        conn.execute("ALTER TABLE queue ADD COLUMN last_error TEXT")
    
    if not _column_exists(conn, "queue", "engagement"):
        logger.info("Adding column: engagement")
        conn.execute("ALTER TABLE queue ADD COLUMN engagement TEXT")
    
    # Create UNIQUE index on (source, source_id) for deduplication
    if not _index_exists(conn, "idx_queue_source_id"):
        logger.info("Creating UNIQUE index on (source, source_id)")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_source_id ON queue(source, source_id)"
        )
    
    # Create schema_version table if it doesn't exist
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL,
            applied_at TEXT NOT NULL,
            description TEXT
        )
    """)
    
    # Check if v2 is already recorded
    cursor = conn.execute("SELECT version FROM schema_version WHERE version = 2")
    if cursor.fetchone() is None:
        # Record this migration
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO schema_version (version, applied_at, description) VALUES (?, ?, ?)",
            (2, now, "Add analysis workflow columns and dedup index")
        )
        logger.info("Recorded schema version 2")
    
    conn.commit()
    logger.info("Migration v1 → v2 complete")


def migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Migrate from schema v2 to v3.

    Adds:
    - analysis_schema_version column to queue table (tracks which schema version
      was used to analyze each item, enabling forward-compatible schema evolution)
    - batches table for tracking delivery batch metadata (footer messages, counts)

    This migration is idempotent - safe to run multiple times.

    Args:
        conn: Active SQLite connection
    """
    logger.info("Running migration v2 → v3...")

    # Add analysis_schema_version column
    if not _column_exists(conn, "queue", "analysis_schema_version"):
        logger.info("Adding column: analysis_schema_version")
        conn.execute(
            "ALTER TABLE queue ADD COLUMN analysis_schema_version TEXT DEFAULT 'v1'"
        )

    # Create batches table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS batches (
            batch_id            TEXT PRIMARY KEY,
            footer_message_id   TEXT,
            item_count          INTEGER,
            delivered_at        TEXT
        )
    """)

    # Create schema_version table if it doesn't exist (in case v2 migration was skipped)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL,
            applied_at TEXT NOT NULL,
            description TEXT
        )
    """)

    # Check if v3 is already recorded
    cursor = conn.execute("SELECT version FROM schema_version WHERE version = 3")
    if cursor.fetchone() is None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO schema_version (version, applied_at, description) VALUES (?, ?, ?)",
            (3, now, "Add analysis_schema_version column and batches table")
        )
        logger.info("Recorded schema version 3")

    conn.commit()
    logger.info("Migration v2 → v3 complete")


def run_migrations(db_path: str) -> None:
    """Run all pending migrations on the database.

    This is the main entry point called from init_db().
    Automatically detects current version and applies necessary upgrades.

    Args:
        db_path: Path to SQLite database file
    """
    with _get_connection(db_path) as conn:
        current_version = get_schema_version(conn)
        logger.info(f"Current schema version: {current_version}")

        if current_version < 2:
            migrate_v1_to_v2(conn)

        if current_version < 3:
            migrate_v2_to_v3(conn)

        logger.info("All migrations complete")
