#!/usr/bin/env python3
"""migrate_folio_schema_v2_2axes.py — F.8 Block-E folio.db Schema-v2.

Adds 2-axis (domain + actionability) columns to validator_opinions and
corrections. Drops legacy F.7 validator_opinions rows that only have
validator_action (no 2-axis data).

Architekt-Decision:
  - validator_opinions: add validator_domain + validator_actionability.
    DROP all 38 alte F.7 rows (5-action schema, no domain). Backup first.
  - corrections: add corrected_domain + corrected_actionability columns.
    corrected_action bleibt im Schema fuer Rueckwaerts-Kompat aber wird
    ab F.8 nicht mehr beschrieben.

Backup-Pattern: validator_opinions.pre-block-e-<UTC-timestamp> (table-copy
innerhalb derselben DB-Datei, analog F.8 feedback.db pattern).

Idempotent: Re-Run auf already-migrated DB exits clean.

Usage:
    python3 migrate_folio_schema_v2_2axes.py --confirm-drop [path/to/folio.db]

--confirm-drop ist erforderlich, weil 38 alte validator_opinions geloescht
werden. Corrections bleiben unangetastet (nur Schema-Add).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB = Path.home() / "Projects" / "folio" / "state" / "folio.db"


def has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def migrate(db_path: Path, confirm_drop: bool) -> int:
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        validator_has_domain = has_column(conn, "validator_opinions", "validator_domain")
        corrections_has_domain = has_column(conn, "corrections", "corrected_domain")
        if validator_has_domain and corrections_has_domain:
            v_count = conn.execute("SELECT COUNT(*) FROM validator_opinions").fetchone()[0]
            c_count = conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]
            print(
                f"OK: 2-axes columns already present in {db_path}. Schema-v2. "
                f"validator_opinions={v_count}, corrections={c_count}."
            )
            return 0

        v_pre = conn.execute("SELECT COUNT(*) FROM validator_opinions").fetchone()[0]
        c_pre = conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]
        print(f"Pre-migration: validator_opinions={v_pre}, corrections={c_pre}")
        print(f"validator_has_domain={validator_has_domain}, corrections_has_domain={corrections_has_domain}")
    finally:
        conn.close()

    if v_pre > 0 and not confirm_drop:
        print(
            "ERROR: --confirm-drop required. Migration DROPS all "
            f"{v_pre} legacy validator_opinions (F.7 5-action schema). "
            "Backup-Table persisted in same DB.",
            file=sys.stderr,
        )
        return 1

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    backup_table = f"validator_opinions__pre_block_e_{timestamp.replace('-', '').replace(':', '')}"

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("BEGIN")

        # ----- Backup validator_opinions (table-copy) -----
        if not validator_has_domain:
            conn.execute(
                f'CREATE TABLE "{backup_table}" AS SELECT * FROM validator_opinions'
            )
            backup_count = conn.execute(
                f'SELECT COUNT(*) FROM "{backup_table}"'
            ).fetchone()[0]
            print(f"Backup-Table: {backup_table} ({backup_count} rows)")

            # Add 2-axes columns
            conn.execute("ALTER TABLE validator_opinions ADD COLUMN validator_domain TEXT")
            conn.execute("ALTER TABLE validator_opinions ADD COLUMN validator_actionability TEXT")

            # Drop legacy F.7 rows (those without domain — i.e. all current 38)
            conn.execute("DELETE FROM validator_opinions WHERE validator_domain IS NULL")

            # Add indexes for 2-axes query patterns
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_validator_domain "
                "ON validator_opinions(validator_domain)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_validator_actionability "
                "ON validator_opinions(validator_actionability)"
            )

        # ----- Add columns to corrections (no drop, parallel pattern) -----
        if not corrections_has_domain:
            conn.execute("ALTER TABLE corrections ADD COLUMN corrected_domain TEXT")
            conn.execute("ALTER TABLE corrections ADD COLUMN corrected_actionability TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_corrections_domain "
                "ON corrections(corrected_domain)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_corrections_actionability "
                "ON corrections(corrected_actionability)"
            )

        conn.execute("COMMIT")
    except Exception as e:
        conn.execute("ROLLBACK")
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    # ----- Verify -----
    conn = sqlite3.connect(str(db_path))
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        v_post = conn.execute("SELECT COUNT(*) FROM validator_opinions").fetchone()[0]
        c_post = conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]
        v_has = has_column(conn, "validator_opinions", "validator_domain") and \
            has_column(conn, "validator_opinions", "validator_actionability")
        c_has = has_column(conn, "corrections", "corrected_domain") and \
            has_column(conn, "corrections", "corrected_actionability")
        backups = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'validator_opinions__pre_block_e_%'"
        ).fetchall()
    finally:
        conn.close()

    print()
    print(f"PRAGMA integrity_check: {integrity}")
    print(f"Post: validator_opinions={v_post} (expected 0), corrections={c_post} (unchanged={c_pre})")
    print(f"validator 2-axes columns added: {v_has}")
    print(f"corrections 2-axes columns added: {c_has}")
    print(f"Backup tables in DB: {[b[0] for b in backups]}")

    if integrity != "ok" or not v_has or not c_has or c_post != c_pre:
        print("ERROR: verification failed", file=sys.stderr)
        return 1

    print()
    print(f"Schema-v2 successful. {v_pre} legacy validator_opinions dropped, backup persisted.")
    print(f"corrections unchanged ({c_pre} rows), corrected_action bleibt fuer Back-Compat.")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="migrate_folio_schema_v2_2axes")
    ap.add_argument(
        "db_path",
        nargs="?",
        type=Path,
        default=DEFAULT_DB,
        help="Path to folio.db (default: ~/Projects/folio/state/folio.db)",
    )
    ap.add_argument(
        "--confirm-drop",
        action="store_true",
        help="REQUIRED if validator_opinions has rows (will be dropped, backup-table persisted).",
    )
    args = ap.parse_args(argv[1:])
    return migrate(args.db_path, args.confirm_drop)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
