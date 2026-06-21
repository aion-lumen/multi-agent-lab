#!/usr/bin/env python3
"""init_feedback_db.py — create state/feedback.db with the Prompt-O.1 §6.1 schema.

Idempotent: re-running on an existing DB only no-ops the CREATE TABLE/INDEX.
Run once before the first production_worker tranche.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "state" / "feedback.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
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
    UNIQUE(imap_uid)
);

CREATE INDEX IF NOT EXISTS idx_feedback_sender ON feedback(sender);
CREATE INDEX IF NOT EXISTS idx_feedback_user_final_action ON feedback(user_final_action);
"""


def init_db(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def main() -> None:
    init_db()
    print(f"feedback.db initialised at {DB_PATH}")
    # Show the schema as confirmation
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT name, type FROM sqlite_master "
            "WHERE type IN ('table', 'index') AND name LIKE '%feedback%' "
            "ORDER BY type, name"
        ).fetchall()
        for name, kind in rows:
            print(f"  {kind:<6} {name}")


if __name__ == "__main__":
    sys.exit(main())
