#!/usr/bin/env python3
"""migrate_feedback_schema_v4_fresh.py — F.8 Fresh-Start Migration.

Drops all existing feedback rows and re-creates the table with v4 schema
(domain + actionability + effective_actionability columns).

Architekt-Decision: alte 190 Rows unter Telegram-Friction + 5-Action-Schema
sind nicht repräsentativ für domain×actionability-Lerndatensatz. Fresh-Start.

Backup: feedback.db.pre-f8-fresh-start-<UTC-timestamp>
Idempotent: re-run on already-v4-DB exits clean (detected via domain-column).

Usage:
    python3 migrate_feedback_schema_v4_fresh.py --confirm-reset [path/to/feedback.db]

The --confirm-reset flag is REQUIRED to prevent accidental data-loss.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB = Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "state" / "feedback.db"


SCHEMA_V4 = """
CREATE TABLE feedback (
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
    mail_date TEXT,
    domain TEXT,
    actionability TEXT,
    effective_actionability TEXT,
    UNIQUE(account_id, imap_uid)
)
"""

INDEXES = [
    "CREATE INDEX idx_feedback_account ON feedback(account_id)",
    "CREATE INDEX idx_feedback_sender ON feedback(sender)",
    "CREATE INDEX idx_feedback_user_final_action ON feedback(user_final_action)",
    "CREATE INDEX idx_feedback_domain ON feedback(domain)",
    "CREATE INDEX idx_feedback_actionability ON feedback(actionability)",
]


def has_domain_column(conn: sqlite3.Connection) -> bool:
    rows = conn.execute("PRAGMA table_info(feedback)").fetchall()
    return any(r[1] == "domain" for r in rows)


def migrate(db_path: Path, confirm: bool) -> int:
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        if has_domain_column(conn):
            count = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
            print(f"OK: domain column already present in {db_path}. Schema-v4. Rows: {count}.")
            return 0
        pre_count = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        print(f"Pre-migration row count: {pre_count}")
    finally:
        conn.close()

    if not confirm:
        print(
            "ERROR: --confirm-reset flag required. This DROPS all "
            f"{pre_count} existing feedback rows. Re-run with --confirm-reset.",
            file=sys.stderr,
        )
        return 1

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    backup_path = db_path.with_name(f"{db_path.name}.pre-f8-fresh-start-{timestamp}")
    shutil.copy2(db_path, backup_path)
    print(f"Backup: {backup_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("BEGIN")
        conn.execute("DROP TABLE feedback")
        conn.execute(SCHEMA_V4)
        for idx_sql in INDEXES:
            conn.execute(idx_sql)
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
        post_count = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        has_col = has_domain_column(conn)
    finally:
        conn.close()

    print(f"PRAGMA integrity_check: {integrity}")
    print(f"Post-migration row count: {post_count} (expected 0)")
    print(f"domain + actionability columns added: {has_col}")

    if integrity != "ok" or post_count != 0 or not has_col:
        print("ERROR: verification failed", file=sys.stderr)
        return 1
    print(f"Fresh-Start v4 successful. {pre_count} legacy rows dropped (backup persisted).")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="migrate_feedback_schema_v4_fresh")
    ap.add_argument(
        "db_path",
        nargs="?",
        type=Path,
        default=DEFAULT_DB,
        help="Path to feedback.db (default: ~/Projects/aion-lumen/multi-agent/state/feedback.db)",
    )
    ap.add_argument(
        "--confirm-reset",
        action="store_true",
        help="REQUIRED to confirm data-loss intent (drops all legacy rows).",
    )
    args = ap.parse_args(argv[1:])
    return migrate(args.db_path, args.confirm_reset)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
