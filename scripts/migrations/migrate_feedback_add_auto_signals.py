#!/usr/bin/env python3
"""migrate_feedback_add_auto_signals.py — P0.3–P0.5 Yahoo-Struktur-Move (2026-07-12).

Adds the auto-vs-personal move signals to feedback.db.feedback so the worker can
persist them from the MailEnvelope (life-mail fetcher) and imap_cleanup's move rule
can read them:
    to_addr, list_id, auto_submitted, precedence, list_unsubscribe   (all TEXT, nullable)

Idempotent: PRAGMA table_info check skips each ALTER if the column already exists.
Non-destructive (ADD COLUMN only). Rows written before this run keep NULL → the
classifier falls back to sender-prefix + salutation.

Usage:  python3 scripts/migrations/migrate_feedback_add_auto_signals.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

FEEDBACK_DB = Path(
    __import__("os").environ.get(
        "FEEDBACK_DB_PATH",
        str(Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "state" / "feedback.db"),
    )
)

NEW_COLUMNS = ("to_addr", "list_id", "auto_submitted", "precedence", "list_unsubscribe")


def main() -> int:
    if not FEEDBACK_DB.exists():
        print(f"✗ {FEEDBACK_DB} not found")
        return 1
    conn = sqlite3.connect(str(FEEDBACK_DB))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(feedback)").fetchall()}
        added = []
        for col in NEW_COLUMNS:
            if col in cols:
                continue
            conn.execute(f"ALTER TABLE feedback ADD COLUMN {col} TEXT")
            added.append(col)
        conn.commit()
        after = {r[1] for r in conn.execute("PRAGMA table_info(feedback)").fetchall()}
        missing = [c for c in NEW_COLUMNS if c not in after]
        if missing:
            print(f"✗ post-ALTER check failed — missing: {missing}")
            return 1
        print(f"✓ added: {added or '(none — already present, idempotent)'}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
