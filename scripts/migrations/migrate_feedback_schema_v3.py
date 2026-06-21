#!/usr/bin/env python3
"""migrate_feedback_schema_v3.py — F.7-BUG-2 Schema-Additive.

Adds `mail_date TEXT` column to feedback table — IMAP envelope-Date-Header.
Legacy rows backfill with NULL; Folio reads `mail_date ?? created_at` fallback.

Architekt-Sign-off: additive ALTER ADD COLUMN ist erlaubt unter Schema-
Konstanz-Update (F.7-Final-Report §X). Refactoring-Schema-Edits brauchen
weiterhin explizite Architekt-Sign-off.

Usage:
    python3 migrate_feedback_schema_v3.py [path/to/feedback.db]

Idempotent: re-running on already-migrated DB exits clean.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB = Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "state" / "feedback.db"


def has_mail_date_column(conn: sqlite3.Connection) -> bool:
    rows = conn.execute("PRAGMA table_info(feedback)").fetchall()
    return any(r[1] == "mail_date" for r in rows)


def count_rows(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]


def migrate(db_path: Path) -> int:
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        if has_mail_date_column(conn):
            print(f"OK: mail_date column already present in {db_path}. Skipping migration.")
            return 0
        pre_count = count_rows(conn)
        print(f"Pre-migration row count: {pre_count}")
    finally:
        conn.close()

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    backup_path = db_path.with_name(f"{db_path.name}.pre-f7-schema-v3-{timestamp}")
    shutil.copy2(db_path, backup_path)
    print(f"Backup: {backup_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("BEGIN")
        conn.execute("ALTER TABLE feedback ADD COLUMN mail_date TEXT")
        conn.execute("COMMIT")
    except Exception as e:
        conn.execute("ROLLBACK")
        print(f"FAILED: {e}", file=sys.stderr)
        print(f"DB unchanged. Backup at {backup_path}.", file=sys.stderr)
        return 1
    finally:
        conn.close()

    conn = sqlite3.connect(str(db_path))
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        post_count = count_rows(conn)
        has_col = has_mail_date_column(conn)
    finally:
        conn.close()

    print(f"PRAGMA integrity_check: {integrity}")
    print(f"Post-migration row count: {post_count} (expected {pre_count})")
    print(f"mail_date column added: {has_col}")

    if integrity != "ok" or post_count != pre_count or not has_col:
        print("ERROR: verification failed", file=sys.stderr)
        return 1
    print(f"Migration v3 successful. Legacy rows have mail_date=NULL.")
    return 0


def main(argv: list[str]) -> int:
    db_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_DB
    return migrate(db_path)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
