#!/usr/bin/env python3
"""migrate_feedback_add_body_excerpt.py — I2-Fix: add body_excerpt column.

Adds TEXT column `body_excerpt` to feedback.db.feedback so the worker can
persist the first 1000 chars of env.body_text alongside body_hash. Validator
reads from this column instead of mis-using body_hash as a body excerpt.

Direktive: ~/Projects/direktive-bugfix-i2-cleanup.md (Block 1, I2 forward-only).

Idempotent: PRAGMA table_info check skips ALTER if the column already exists.

Pre-migration backup (manual, prior to running this script):
  ~/Projects/backups/pre-i2-migration-2026-05-26/feedback.db.online-backup

Usage:
    python3 migrate_feedback_add_body_excerpt.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


FEEDBACK_DB = Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "state" / "feedback.db"


def main() -> int:
    if not FEEDBACK_DB.exists():
        print(f"✗ {FEEDBACK_DB} not found")
        return 1
    conn = sqlite3.connect(str(FEEDBACK_DB))
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(feedback)").fetchall()]
        if "body_excerpt" in cols:
            print("✓ body_excerpt column already exists — nothing to do (idempotent)")
            return 0
        conn.execute("ALTER TABLE feedback ADD COLUMN body_excerpt TEXT")
        conn.commit()
        cols_after = [r[1] for r in conn.execute("PRAGMA table_info(feedback)").fetchall()]
        if "body_excerpt" not in cols_after:
            print("✗ post-ALTER schema check failed — column not present")
            return 1
        null_count = conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE body_excerpt IS NULL"
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        print(f"✓ added body_excerpt column to feedback")
        print(f"  total rows: {total} (all {null_count} legacy rows have body_excerpt = NULL)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
