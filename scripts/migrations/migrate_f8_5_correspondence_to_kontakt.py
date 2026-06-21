#!/usr/bin/env python3
"""migrate_f8_5_correspondence_to_kontakt.py — F.8.5 Rename Migration.

UPDATEs all existing domain-references from 'correspondence' to 'kontakt' in:
  - feedback.db.feedback.domain
  - folio.db.corrections.corrected_domain
  - folio.db.validator_opinions.validator_domain

Idempotent: Re-Run findet keine Treffer mehr und exitet sauber.

No backup: this is a string-rename, fully reversible via inverse UPDATE.

Usage:
    python3 migrate_f8_5_correspondence_to_kontakt.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


FEEDBACK_DB = Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "state" / "feedback.db"
FOLIO_DB = Path.home() / "Projects" / "folio" / "state" / "folio.db"


def migrate_table(db_path: Path, table: str, column: str) -> int:
    if not db_path.exists():
        print(f"  ⚠ {db_path} not found — skipping {table}.{column}")
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        # Verify column exists (corrections/validator_opinions may have schema-v1)
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if column not in cols:
            print(f"  ⚠ {db_path.name}.{table} has no column '{column}' — skipping")
            return 0
        pre = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} = 'correspondence'"
        ).fetchone()[0]
        if pre == 0:
            print(f"  ✓ {db_path.name}.{table}.{column}: 0 rows to migrate (already clean)")
            return 0
        conn.execute(
            f"UPDATE {table} SET {column} = 'kontakt' WHERE {column} = 'correspondence'"
        )
        conn.commit()
        post = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} = 'correspondence'"
        ).fetchone()[0]
        if post != 0:
            print(f"  ✗ {db_path.name}.{table}.{column}: post-update count is {post}, expected 0")
            return -1
        print(f"  ✓ {db_path.name}.{table}.{column}: migrated {pre} rows correspondence → kontakt")
        return pre
    finally:
        conn.close()


def main() -> int:
    print("F.8.5 Migration — Rename 'correspondence' → 'kontakt'\n")
    total = 0
    for db, table, column in [
        (FEEDBACK_DB, "feedback", "domain"),
        (FOLIO_DB, "corrections", "corrected_domain"),
        (FOLIO_DB, "validator_opinions", "validator_domain"),
    ]:
        result = migrate_table(db, table, column)
        if result < 0:
            return 1
        total += result
    print(f"\nDone. Total rows migrated: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
