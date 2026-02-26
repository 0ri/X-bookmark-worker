"""
SQLite queue CRUD module for bookmark-digest.

Stdlib only (sqlite3). Python 3.10+.
"""

import sqlite3
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Source prefix map
SOURCE_PREFIXES = {
    "twitter": "bk",
    "reddit": "rd",
    "youtube": "yt",
    "github": "gh",
    "hn": "hn",
    "kindle": "kd",
    "newsletter": "nl",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue (
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

CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status);
CREATE INDEX IF NOT EXISTS idx_queue_source ON queue(source);
CREATE INDEX IF NOT EXISTS idx_queue_created_at ON queue(created_at);
"""


def _now() -> str:
    """Return current UTC timestamp as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(cursor: sqlite3.Cursor, row: tuple) -> dict:
    """Convert SQLite row tuple to dict using cursor column names."""
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


def _connect(db_path: str) -> sqlite3.Connection:
    """Create and configure a SQLite connection with WAL mode and timeouts.
    
    Args:
        db_path: Path to SQLite database file
        
    Returns:
        Configured sqlite3.Connection instance
    """
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = _row_to_dict
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _get_connection(db_path: str):
    """Context manager for SQLite connections with proper exception handling."""
    conn = _connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str) -> None:
    """Create DB + tables if they don't exist, then run migrations."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    conn.executescript(_SCHEMA)
    conn.close()
    
    # Run migrations to ensure schema is up to date
    from .migrations import run_migrations
    run_migrations(db_path)


def _generate_id(source: str) -> str:
    """Generate a unique queue item ID with source-specific prefix.
    
    Args:
        source: Source name (twitter, reddit, youtube, etc.)
        
    Returns:
        Unique ID string in format: {prefix}_{hex}
    """
    prefix = SOURCE_PREFIXES.get(source, source[:2])
    return f"{prefix}_{secrets.token_hex(4)}"


def add_item(db_path: str, item_dict: dict) -> str | None:
    """
    Add an item to the queue.
    Returns the item_id on success, or None if duplicate (source, source_id).
    """
    now = _now()
    source = item_dict.get("source", "unknown")
    item_id = _generate_id(source)

    with _get_connection(db_path) as conn:
        try:
            conn.execute(
                """INSERT INTO queue
                   (id, source, source_id, canonical_url, title, category,
                    status, action, summary, raw_content, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item_id,
                    source,
                    item_dict.get("source_id", ""),
                    item_dict.get("canonical_url"),
                    item_dict.get("title"),
                    item_dict.get("category"),
                    item_dict.get("status", "pending"),
                    item_dict.get("action"),
                    item_dict.get("summary"),
                    item_dict.get("raw_content"),
                    now,
                    now,
                ),
            )
            conn.commit()
            return item_id
        except sqlite3.IntegrityError:
            return None


def get_pending(db_path: str, limit: int = 10) -> list[dict]:
    """Return pending items that haven't been triaged yet, oldest first."""
    with _get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM queue WHERE status = 'pending' AND triaged_at IS NULL ORDER BY created_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return rows


def get_queued(db_path: str) -> list[dict]:
    """Return items with status='queued', ordered by creation time.
    
    Args:
        db_path: Path to SQLite database file
        
    Returns:
        List of queue item dicts
    """
    with _get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM queue WHERE status = 'queued' ORDER BY created_at ASC"
        ).fetchall()
        return rows


def claim_item(db_path: str, item_id: str) -> bool:
    """Atomically claim an item (only if queued or pending).
    
    Sets status to 'processing' and increments attempt_count.
    
    Args:
        db_path: Path to SQLite database file
        item_id: Unique item ID to claim
        
    Returns:
        True if item was successfully claimed, False otherwise
    """
    now = _now()
    with _get_connection(db_path) as conn:
        cur = conn.execute(
            """UPDATE queue
               SET status = 'processing', updated_at = ?, attempt_count = attempt_count + 1
               WHERE id = ? AND status IN ('queued', 'pending')""",
            (now, item_id),
        )
        conn.commit()
        changed = cur.rowcount > 0
        return changed


def get_item(db_path: str, item_id: str) -> dict | None:
    """Get a single item by id."""
    with _get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM queue WHERE id = ?", (item_id,)).fetchone()
        return row


def get_stats(db_path: str) -> dict[str, int]:
    """Return counts grouped by status."""
    with _get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as count FROM queue GROUP BY status"
        ).fetchall()
        return {r["status"]: r["count"] for r in rows}


def mark_triaged(db_path: str, item_ids: list[str]) -> int:
    """Mark items as triaged after being shown in a digest.
    
    This prevents items from appearing in subsequent digests until user takes action.
    Returns count of items marked.
    """
    if not item_ids:
        return 0
    now = _now()
    placeholders = ",".join("?" * len(item_ids))
    
    with _get_connection(db_path) as conn:
        cur = conn.execute(
            f"UPDATE queue SET triaged_at = ? WHERE id IN ({placeholders}) AND status = 'pending'",
            [now] + item_ids
        )
        conn.commit()
        count = cur.rowcount
        return count


def archive_completed(db_path: str, older_than_days: int = 7) -> int:
    """Delete completed items older than `older_than_days`. Returns count deleted."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    
    with _get_connection(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM queue WHERE status IN ('completed', 'skipped') AND created_at < ?",
            (cutoff,),
        )
        conn.commit()
        count = cur.rowcount
        return count


# ============================================================================
# New Lifecycle Functions (Session 2)
# ============================================================================

# ============================================================================
# Status Lifecycle
# ============================================================================
#
# v1 (legacy) lifecycle:
#   pending → queued → processing → completed/failed/skipped
#   pending → triaged → processing → completed/failed/skipped
#
# v2 (current) lifecycle:
#   pending → analyzed → sending → delivered → acted_on → completed
#   pending → skipped
#   analyzed → failed (DLQ, recoverable via reset_failed)
#   sending → analyzed (rollback on send failure)
#   sending → delivered (success)
#
# "archived" is a terminal state reachable from any status.
# ============================================================================

_VALID_TRANSITIONS = {
    "pending": {"analyzed", "skipped", "queued", "triaged", "completed"},  # Legacy: queued, triaged, completed
    "queued": {"processing", "completed", "skipped"},  # Legacy workflow
    "triaged": {"processing", "completed", "skipped"},  # Legacy workflow
    "processing": {"completed", "failed", "skipped"},  # Legacy workflow
    "analyzed": {"sending", "delivered", "failed"},  # v2: sending is the new intermediate step
    "sending": {"delivered", "analyzed"},  # v2: delivered on success, analyzed on rollback
    "delivered": {"acted_on"},  # v2 workflow
    "acted_on": {"completed"},  # v2 workflow
    # Any status can go to archived
}


def get_undelivered(db_path: str, limit: int | None = None) -> list[dict]:
    """Get items that have been analyzed but not yet delivered.
    
    Args:
        db_path: Path to SQLite database file
        limit: Maximum number of items to return (None = all)
        
    Returns:
        List of undelivered item dicts, ordered by creation time (oldest first)
    """
    with _get_connection(db_path) as conn:
        query = """
            SELECT * FROM queue 
            WHERE status='analyzed' AND batch_id IS NULL 
            ORDER BY created_at ASC
        """
        if limit is not None:
            query += f" LIMIT {limit}"
        
        rows = conn.execute(query).fetchall()
        return rows


def get_next_batch(db_path: str, batch_size: int = 5) -> list[dict]:
    """Get next batch of undelivered items and assign them a shared batch_id.
    
    Atomically assigns a UUID batch_id to the next N undelivered items.
    
    Args:
        db_path: Path to SQLite database file
        batch_size: Number of items to include in batch
        
    Returns:
        List of item dicts with assigned batch_id
    """
    batch_id = secrets.token_hex(8)  # 16-char hex UUID
    
    with _get_connection(db_path) as conn:
        # Get the next N undelivered items
        items = conn.execute(
            """
            SELECT * FROM queue 
            WHERE status='analyzed' AND batch_id IS NULL 
            ORDER BY created_at ASC 
            LIMIT ?
            """,
            (batch_size,)
        ).fetchall()
        
        if not items:
            return []
        
        # Assign batch_id to these items
        item_ids = [item["id"] for item in items]
        placeholders = ",".join("?" * len(item_ids))
        conn.execute(
            f"UPDATE queue SET batch_id = ?, updated_at = ? WHERE id IN ({placeholders})",
            [batch_id, _now()] + item_ids
        )
        conn.commit()
        
        # Fetch updated items with batch_id
        updated = conn.execute(
            f"SELECT * FROM queue WHERE id IN ({placeholders})",
            item_ids
        ).fetchall()
        
        return updated


def mark_delivered(db_path: str, item_id: str, telegram_message_id: str) -> bool:
    """Mark an item as delivered and store the Telegram message ID.
    
    Args:
        db_path: Path to SQLite database file
        item_id: Queue item ID
        telegram_message_id: Telegram message ID for later editing
        
    Returns:
        True if item was updated, False if not found
    """
    now = _now()
    
    with _get_connection(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE queue 
            SET status = 'delivered', 
                telegram_message_id = ?, 
                updated_at = ?
            WHERE id = ?
            """,
            (telegram_message_id, now, item_id)
        )
        conn.commit()
        return cur.rowcount > 0


def update_analysis(db_path: str, item_id: str, analysis: str, buttons_json: str) -> bool:
    """Save LLM analysis and buttons, set status to 'analyzed'.
    
    Args:
        db_path: Path to SQLite database file
        item_id: Queue item ID
        analysis: JSON string of analysis results
        buttons_json: JSON string of button configuration
        
    Returns:
        True if item was updated, False if not found
    """
    now = _now()
    
    with _get_connection(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE queue 
            SET analysis = ?, 
                buttons_json = ?, 
                status = 'analyzed', 
                updated_at = ?
            WHERE id = ?
            """,
            (analysis, buttons_json, now, item_id)
        )
        conn.commit()
        return cur.rowcount > 0


def mark_acted_on(db_path: str, item_id: str) -> bool:
    """Mark an item as acted on (user clicked a button).
    
    Args:
        db_path: Path to SQLite database file
        item_id: Queue item ID
        
    Returns:
        True if item was updated, False if not found
    """
    now = _now()
    
    with _get_connection(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE queue 
            SET status = 'acted_on', 
                updated_at = ?
            WHERE id = ?
            """,
            (now, item_id)
        )
        conn.commit()
        return cur.rowcount > 0


def update_status(db_path: str, item_id: str, new_status: str, **kwargs) -> bool:
    """Update an item's status with transition validation.

    Valid transitions:
    - pending → analyzed, skipped
    - analyzed → sending, delivered, failed
    - sending → delivered, analyzed (rollback)
    - delivered → acted_on
    - acted_on → completed
    - any → archived

    Args:
        db_path: Path to SQLite database file
        item_id: Queue item ID
        new_status: New status to set
        **kwargs: Additional fields to update (action, result, error, etc.)

    Returns:
        True if item was updated, False if not found

    Raises:
        ValueError: If the status transition is invalid
    """
    # Get current status
    item = get_item(db_path, item_id)
    if item is None:
        return False

    current_status = item["status"]

    # Validate transition (archived is always allowed)
    if new_status != "archived":
        valid_next = _VALID_TRANSITIONS.get(current_status, set())
        if new_status not in valid_next:
            raise ValueError(
                f"Invalid status transition: {current_status} → {new_status}. "
                f"Valid transitions from {current_status}: {', '.join(sorted(valid_next)) if valid_next else 'none (terminal state)'}"
            )

    # Build update query
    now = _now()
    allowed = {"action", "result", "error", "triaged_at", "completed_at", "summary", "last_error"}
    sets = ["status = ?", "updated_at = ?"]
    params: list = [new_status, now]

    for key, val in kwargs.items():
        if key in allowed:
            sets.append(f"{key} = ?")
            params.append(val)

    params.append(item_id)

    with _get_connection(db_path) as conn:
        cur = conn.execute(
            f"UPDATE queue SET {', '.join(sets)} WHERE id = ?",
            params
        )
        conn.commit()
        return cur.rowcount > 0


# ============================================================================
# v2 Pipeline Functions (Session 3)
# ============================================================================

def store_analyses(db_path: str, analyses: list[dict]) -> int:
    """Bulk update items with analysis data from llm-task output.

    Idempotent: only updates items with status='pending'. Items that have
    already been analyzed (or are in any other state) are silently skipped.

    Args:
        db_path: Path to SQLite database file
        analyses: List of analysis dicts, each containing at minimum:
            - item_id: Queue item ID
            - category: Inferred category
            - analysis: Analysis text
            - buttons: List of button codes
            - content_type: Type of content
            Optional fields: why_bookmarked, relevance_score,
            needs_enrichment, enrichment_urls

    Returns:
        Count of items actually updated
    """
    import json as _json
    now = _now()
    updated = 0

    with _get_connection(db_path) as conn:
        for item in analyses:
            item_id = item.get("item_id")
            if not item_id:
                continue

            analysis_blob = _json.dumps(item)
            buttons_json = _json.dumps(item.get("buttons", ["dd"]))
            category = item.get("category", "")

            cur = conn.execute(
                """UPDATE queue
                   SET analysis = ?,
                       buttons_json = ?,
                       category = ?,
                       status = 'analyzed',
                       analysis_schema_version = 'v1',
                       updated_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (analysis_blob, buttons_json, category, now, item_id)
            )
            updated += cur.rowcount

        conn.commit()

    return updated


def reset_failed(db_path: str) -> int:
    """Reset failed items back to pending for reprocessing.

    Clears error_count and last_error, allowing items to re-enter the pipeline.

    Args:
        db_path: Path to SQLite database file

    Returns:
        Count of items reset
    """
    now = _now()

    with _get_connection(db_path) as conn:
        cur = conn.execute(
            """UPDATE queue
               SET status = 'pending',
                   error_count = 0,
                   last_error = NULL,
                   updated_at = ?
               WHERE status = 'failed'""",
            (now,)
        )
        conn.commit()
        return cur.rowcount


def recover_sending(db_path: str) -> int:
    """Reset any items stuck in 'sending' back to 'analyzed'.

    Should be called at pipeline startup. Since RunLock guarantees no
    concurrent execution, any item in 'sending' at startup indicates a
    crashed previous run.

    Args:
        db_path: Path to SQLite database file

    Returns:
        Count of items recovered
    """
    now = _now()

    with _get_connection(db_path) as conn:
        cur = conn.execute(
            """UPDATE queue
               SET status = 'analyzed',
                   updated_at = ?
               WHERE status = 'sending'""",
            (now,)
        )
        conn.commit()
        return cur.rowcount


def set_sending(db_path: str, item_id: str) -> bool:
    """Transition an item from analyzed → sending (two-phase delivery).

    Args:
        db_path: Path to SQLite database file
        item_id: Queue item ID

    Returns:
        True if transition succeeded, False if item not in 'analyzed' state
    """
    now = _now()

    with _get_connection(db_path) as conn:
        cur = conn.execute(
            """UPDATE queue
               SET status = 'sending',
                   updated_at = ?
               WHERE id = ? AND status = 'analyzed'""",
            (now, item_id)
        )
        conn.commit()
        return cur.rowcount > 0


def mark_delivered_with_message(db_path: str, item_id: str,
                                telegram_message_id: str,
                                batch_id: str) -> bool:
    """Mark an item as delivered with Telegram message ID and batch ID.

    Transitions from sending → delivered. This is the completion of the
    two-phase delivery protocol.

    Args:
        db_path: Path to SQLite database file
        item_id: Queue item ID
        telegram_message_id: Telegram message ID for later editing/replying
        batch_id: Batch this item was delivered in

    Returns:
        True if transition succeeded, False if item not in 'sending' state
    """
    now = _now()

    with _get_connection(db_path) as conn:
        cur = conn.execute(
            """UPDATE queue
               SET status = 'delivered',
                   telegram_message_id = ?,
                   batch_id = ?,
                   updated_at = ?
               WHERE id = ? AND status = 'sending'""",
            (telegram_message_id, batch_id, now, item_id)
        )
        conn.commit()
        return cur.rowcount > 0


def save_batch_footer(db_path: str, batch_id: str,
                      footer_message_id: str, item_count: int) -> None:
    """Upsert batch footer metadata.

    Stores the Telegram message ID of the batch footer (containing the
    "Next 5" button) so it can be edited/deleted later.

    Args:
        db_path: Path to SQLite database file
        batch_id: Batch identifier
        footer_message_id: Telegram message ID of the footer message
        item_count: Number of items in this batch
    """
    now = _now()

    with _get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO batches (batch_id, footer_message_id, item_count, delivered_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(batch_id) DO UPDATE SET
                   footer_message_id = excluded.footer_message_id,
                   item_count = excluded.item_count,
                   delivered_at = excluded.delivered_at""",
            (batch_id, footer_message_id, item_count, now)
        )
        conn.commit()


def get_batch_footer(db_path: str, batch_id: str) -> dict | None:
    """Get batch footer metadata.

    Args:
        db_path: Path to SQLite database file
        batch_id: Batch identifier

    Returns:
        Batch dict or None if not found
    """
    with _get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM batches WHERE batch_id = ?",
            (batch_id,)
        ).fetchone()
        return row


# ============================================================================
# Error Recording + Dead Letter Queue (Session 5)
# ============================================================================

DLQ_MAX_ERRORS = 3


def record_error(db_path: str, item_id: str, error_msg: str) -> dict:
    """Record an error for a queue item. Moves to DLQ after 3 failures.

    Increments error_count, sets last_error. If error_count reaches
    DLQ_MAX_ERRORS, transitions to 'failed' status and appends to
    data/dlq.log.

    Args:
        db_path: Path to SQLite database file
        item_id: Queue item ID
        error_msg: Human-readable error description

    Returns:
        dict with keys: recorded (bool), error_count (int), dlq (bool)
    """
    now = _now()

    with _get_connection(db_path) as conn:
        # Increment error_count and set last_error
        conn.execute(
            """UPDATE queue
               SET error_count = error_count + 1,
                   last_error = ?,
                   updated_at = ?
               WHERE id = ?""",
            (error_msg, now, item_id)
        )
        conn.commit()

        # Fetch updated item to check error_count
        row = conn.execute(
            "SELECT error_count, status FROM queue WHERE id = ?",
            (item_id,)
        ).fetchone()

        if row is None:
            return {"recorded": False, "error_count": 0, "dlq": False}

        error_count = row["error_count"]
        moved_to_dlq = False

        # Move to DLQ if threshold reached
        if error_count >= DLQ_MAX_ERRORS and row["status"] != "failed":
            conn.execute(
                """UPDATE queue
                   SET status = 'failed',
                       updated_at = ?
                   WHERE id = ?""",
                (now, item_id)
            )
            conn.commit()
            moved_to_dlq = True

            # Write to dlq.log
            _write_dlq_log(db_path, item_id, error_count, error_msg, now)

    return {
        "recorded": True,
        "error_count": error_count,
        "dlq": moved_to_dlq,
    }


def _write_dlq_log(db_path: str, item_id: str, error_count: int,
                    error_msg: str, timestamp: str) -> None:
    """Append a DLQ entry to data/dlq.log.

    Args:
        db_path: Path to SQLite database file (used to find data dir)
        item_id: Queue item ID
        error_count: Current error count
        error_msg: Last error message
        timestamp: ISO 8601 timestamp
    """
    data_dir = Path(db_path).parent
    dlq_path = data_dir / "dlq.log"
    data_dir.mkdir(parents=True, exist_ok=True)

    line = f"[{timestamp}] item_id={item_id} errors={error_count} last_error={error_msg}\n"
    with open(dlq_path, "a") as f:
        f.write(line)
