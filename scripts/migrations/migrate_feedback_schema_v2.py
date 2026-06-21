#!/usr/bin/env python3
"""migrate_feedback_schema_v2.py — F.6 Schema-Migration.

Adds `account_id TEXT NOT NULL DEFAULT 'yahoo'` to the feedback table
and changes UNIQUE(imap_uid) → UNIQUE(account_id, imap_uid). Required
for Multi-Account-Worker (Yahoo + Gmail + Mirhamed) because IMAP-UIDs
are per-account/per-uidvalidity, not globally unique.

Pattern: sqlite cannot ALTER constraints in-place → CREATE TABLE feedback_new
+ INSERT SELECT + DROP + RENAME, all inside one transaction.

Usage:
    python3 migrate_feedback_schema_v2.py [path/to/feedback.db]

Defaults to ~/Projects/aion-lumen/multi-agent/state/feedback.db.

Idempotent: re-running on an already-migrated DB exits clean with status 0.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB = Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "state" / "feedback.db"

NEW_TABLE_SQL = """
CREATE TABLE feedback_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    account_id TEXT NOT NULL DEFAULT 'yahoo',
    imap_uid INTEGER NOT NULL,
    sender TEXT NOT NULL,
    subject TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    plugin_value TEXT,
    plugin_confidence REAL,
    plugin_evidence TEXT,
    heuristic_suggested_action TEXT,
    heuristic_reason TEXT,
    heuristic_confidence TEXT,
    heuristic_markers TEXT,
    user_classification TEXT,
    user_final_action TEXT,
    suggested_action_confirmed INTEGER,
    response_time_ms INTEGER,
    timeout_occurred INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(account_id, imap_uid)
)
"""

INSERT_BACKFILL_SQL = """
INSERT INTO feedback_new (
    id, task_id, account_id, imap_uid, sender, subject, body_hash,
    plugin_value, plugin_confidence, plugin_evidence,
    heuristic_suggested_action, heuristic_reason, heuristic_confidence,
    heuristic_markers, user_classification, user_final_action,
    suggested_action_confirmed, response_time_ms, timeout_occurred, created_at
)
SELECT
    id, task_id, 'yahoo', imap_uid, sender, subject, body_hash,
    plugin_value, plugin_confidence, plugin_evidence,
    heuristic_suggested_action, heuristic_reason, heuristic_confidence,
    heuristic_markers, user_classification, user_final_action,
    suggested_action_confirmed, response_time_ms, timeout_occurred, created_at
FROM feedback
"""

INDEX_SQLS = [
    "CREATE INDEX IF NOT EXISTS idx_feedback_account ON feedback(account_id)",
    "CREATE INDEX IF NOT EXISTS idx_feedback_sender ON feedback(sender)",
    "CREATE INDEX IF NOT EXISTS idx_feedback_user_final_action ON feedback(user_final_action)",
]


def has_account_id_column(conn: sqlite3.Connection) -> bool:
    rows = conn.execute("PRAGMA table_info(feedback)").fetchall()
    return any(r[1] == "account_id" for r in rows)


def count_rows(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]


def migrate(db_path: Path) -> int:
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        return 1

    # Open + idempotency check
    conn = sqlite3.connect(str(db_path))
    try:
        if has_account_id_column(conn):
            print(f"OK: account_id column already present in {db_path}. Skipping migration.")
            return 0

        pre_count = count_rows(conn)
        print(f"Pre-migration row count: {pre_count}")
    finally:
        conn.close()

    # Backup BEFORE any write
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    backup_path = db_path.with_name(f"{db_path.name}.pre-f6-backup-{timestamp}")
    shutil.copy2(db_path, backup_path)
    print(f"Backup: {backup_path}")

    # Migration in atomic transaction
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("BEGIN")
        conn.execute(NEW_TABLE_SQL)
        rowcount = conn.execute(INSERT_BACKFILL_SQL).rowcount
        conn.execute("DROP TABLE feedback")
        conn.execute("ALTER TABLE feedback_new RENAME TO feedback")
        for sql in INDEX_SQLS:
            conn.execute(sql)
        conn.execute("COMMIT")
    except Exception as e:
        conn.execute("ROLLBACK")
        print(f"FAILED: {e}", file=sys.stderr)
        print(f"DB unchanged. Backup at {backup_path}.", file=sys.stderr)
        return 1
    finally:
        conn.close()

    # Post-migration verify
    conn = sqlite3.connect(str(db_path))
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        post_count = count_rows(conn)
        distinct_accounts = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT account_id FROM feedback"
            ).fetchall()
        ]
    finally:
        conn.close()

    print(f"PRAGMA integrity_check: {integrity}")
    print(f"Post-migration row count: {post_count} (expected {pre_count})")
    print(f"Distinct account_id values: {distinct_accounts}")

    if integrity != "ok":
        print("ERROR: integrity_check failed", file=sys.stderr)
        return 1
    if post_count != pre_count:
        print(f"ERROR: row count changed {pre_count} → {post_count}", file=sys.stderr)
        return 1
    if distinct_accounts != ["yahoo"]:
        print(f"ERROR: unexpected account_id values: {distinct_accounts}", file=sys.stderr)
        return 1

    print(f"Migrated {rowcount} rows successfully.")
    return 0


def main(argv: list[str]) -> int:
    db_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_DB
    return migrate(db_path)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
