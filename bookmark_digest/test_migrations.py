"""
Tests for database schema migrations.
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from .migrations import get_schema_version, migrate_v1_to_v2, run_migrations
from .bookmark_queue import init_db, add_item


def test_fresh_db_gets_v2_schema():
    """A fresh database should get schema v2 on init."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        
        # Initialize fresh DB
        init_db(db_path)
        
        # Check schema version
        conn = sqlite3.connect(db_path)
        version = get_schema_version(conn)
        conn.close()
        
        assert version == 3, "Fresh DB should be at schema v3"


def test_v1_db_migrates_cleanly():
    """A v1 database should migrate to v2 without data loss."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        
        # Create a v1 schema (without new columns)
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE queue (
                id              TEXT PRIMARY KEY,
                source          TEXT NOT NULL,
                source_id       TEXT NOT NULL,
                canonical_url   TEXT,
                title           TEXT,
                category        TEXT,
                status          TEXT NOT NULL DEFAULT 'pending',
                action          TEXT,
                summary         TEXT,
                result          TEXT,
                error           TEXT,
                attempt_count   INTEGER NOT NULL DEFAULT 0,
                raw_content     TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                triaged_at      TEXT,
                completed_at    TEXT
            );
        """)
        
        # Insert test data
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        
        test_items = [
            ("bk_test001", "twitter", "123456", "pending", now),
            ("bk_test002", "twitter", "234567", "triaged", now),
            ("bk_test003", "twitter", "345678", "completed", now),
        ]
        
        for item_id, source, source_id, status, ts in test_items:
            conn.execute(
                """INSERT INTO queue 
                   (id, source, source_id, status, created_at, updated_at) 
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (item_id, source, source_id, status, ts, ts)
            )
        
        conn.commit()
        conn.close()
        
        # Run migration
        run_migrations(db_path)
        
        # Verify schema upgraded
        conn = sqlite3.connect(db_path)
        version = get_schema_version(conn)
        assert version == 3, "Schema should be upgraded to v3"

        # Verify new columns exist (v2 + v3 columns)
        cursor = conn.execute("PRAGMA table_info(queue)")
        columns = {row[1] for row in cursor.fetchall()}

        expected_new_cols = {
            "analysis", "buttons_json", "batch_id", "telegram_message_id",
            "error_count", "last_error", "engagement", "analysis_schema_version"
        }
        assert expected_new_cols.issubset(columns), "New columns should exist"
        
        # Verify data preserved
        cursor = conn.execute("SELECT COUNT(*) FROM queue")
        count = cursor.fetchone()[0]
        assert count == 3, "All 3 items should be preserved"
        
        # Verify UNIQUE index created
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_queue_source_id'"
        )
        assert cursor.fetchone() is not None, "UNIQUE index should exist"
        
        conn.close()


def test_migration_is_idempotent():
    """Running migration multiple times should be safe."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        
        # Create v1 DB
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE queue (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        conn.close()
        
        # Run migration twice
        run_migrations(db_path)
        run_migrations(db_path)  # Should not error
        
        # Verify at v3
        conn = sqlite3.connect(db_path)
        version = get_schema_version(conn)
        assert version == 3

        # Verify only one v3 entry in schema_version table
        cursor = conn.execute("SELECT COUNT(*) FROM schema_version WHERE version = 3")
        count = cursor.fetchone()[0]
        assert count == 1, "Should only have one v3 record even after multiple runs"
        
        conn.close()


def test_unique_constraint_rejects_duplicates():
    """The UNIQUE index on (source, source_id) should prevent duplicates."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        
        # Initialize with migrations
        init_db(db_path)
        
        # Add first item
        item1 = {
            "source": "twitter",
            "source_id": "999888777",
            "title": "First tweet",
            "status": "pending",
        }
        item_id = add_item(db_path, item1)
        assert item_id is not None, "First item should be added"
        
        # Try to add duplicate
        item2 = {
            "source": "twitter",
            "source_id": "999888777",  # Same source_id
            "title": "Duplicate tweet",
            "status": "pending",
        }
        dup_id = add_item(db_path, item2)
        assert dup_id is None, "Duplicate should be rejected"


def test_migration_preserves_real_data():
    """Migration should preserve all 74 items with real schema."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        
        # Create v1 schema matching production
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE queue (
                id              TEXT PRIMARY KEY,
                source          TEXT NOT NULL,
                source_id       TEXT NOT NULL,
                canonical_url   TEXT,
                title           TEXT,
                category        TEXT,
                status          TEXT NOT NULL DEFAULT 'pending',
                action          TEXT,
                summary         TEXT,
                result          TEXT,
                error           TEXT,
                attempt_count   INTEGER NOT NULL DEFAULT 0,
                raw_content     TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                triaged_at      TEXT,
                completed_at    TEXT,
                UNIQUE(source, source_id)
            );
            CREATE INDEX idx_queue_status ON queue(status);
            CREATE INDEX idx_queue_source ON queue(source);
            CREATE INDEX idx_queue_created_at ON queue(created_at);
        """)
        
        # Insert 74 test items
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        
        for i in range(74):
            conn.execute(
                """INSERT INTO queue 
                   (id, source, source_id, title, status, created_at, updated_at) 
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (f"bk_test{i:03d}", "twitter", f"{1000000 + i}", f"Tweet {i}", "pending", now, now)
            )
        
        conn.commit()
        conn.close()
        
        # Run migration
        run_migrations(db_path)
        
        # Verify all items preserved
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT COUNT(*) FROM queue")
        count = cursor.fetchone()[0]
        assert count == 74, "All 74 items should be preserved"
        
        # Verify schema v3
        version = get_schema_version(conn)
        assert version == 3
        
        conn.close()
