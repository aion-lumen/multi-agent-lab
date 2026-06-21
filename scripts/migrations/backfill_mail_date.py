#!/usr/bin/env python3
"""backfill_mail_date.py — F.7-BUG-A One-shot backfill für legacy feedback-rows.

Liest feedback.db rows WHERE mail_date IS NULL. Pro row: findet Kanban-Task
in einem der ~20 boards via direct-sqlite-iter (Pattern aus folio/mail-body.ts).
Parsed `- **Datum:** <RFC2822>` aus task.body, konvertiert ISO, UPDATE feedback.

Idempotent: Re-runs nur betroffen-rows mit mail_date=NULL.

Usage:
    python3 backfill_mail_date.py [path/to/feedback.db]

Defaults to ~/Projects/aion-lumen/multi-agent/state/feedback.db.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import sys
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path


DEFAULT_DB = Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "state" / "feedback.db"
BOARDS_DIR = Path.home() / ".hermes" / "kanban" / "boards"

DATUM_PATTERN = re.compile(r"\*\*(?:Datum|Date):\*\*\s*(.+?)\n")

log = logging.getLogger("backfill_mail_date")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def list_board_dbs() -> list[Path]:
    """Return all per-board kanban.db files."""
    if not BOARDS_DIR.exists():
        return []
    return sorted(p / "kanban.db" for p in BOARDS_DIR.iterdir() if (p / "kanban.db").exists())


# Connection-Pool: open each board-DB once, reuse across rows
_board_conns: dict[str, sqlite3.Connection] = {}


def get_board_conn(db_path: Path) -> sqlite3.Connection:
    key = str(db_path)
    if key not in _board_conns:
        _board_conns[key] = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    return _board_conns[key]


def find_task_body(task_id: str) -> str | None:
    """Iterate board-dbs until task_id is found; return task.body or None."""
    for db_path in list_board_dbs():
        try:
            conn = get_board_conn(db_path)
            row = conn.execute(
                "SELECT body FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row:
                return row[0]
        except sqlite3.OperationalError:
            continue
    return None


def parse_envelope_date(body: str) -> str | None:
    """Extract `**Datum:** <RFC2822>` from body, return ISO-8601 with TZ."""
    m = DATUM_PATTERN.search(body)
    if not m:
        return None
    raw = m.group(1).strip()
    try:
        dt = parsedate_to_datetime(raw)
        # parsedate returns aware datetime if TZ-info present
        return dt.isoformat()
    except (TypeError, ValueError):
        return None


def backfill(db_path: Path) -> int:
    if not db_path.exists():
        log.error("feedback.db not found: %s", db_path)
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        # Fetch all rows missing mail_date OR with non-ISO format (RFC2822 from Worker)
        # ISO begins with year-digit, RFC2822 begins with weekday-letter.
        # SQLite GLOB matches "[A-Z]*" → catch RFC2822 strings starting with letter.
        rows = conn.execute(
            "SELECT id, task_id, sender, subject, mail_date FROM feedback "
            "WHERE mail_date IS NULL OR mail_date GLOB '[A-Za-z]*'"
        ).fetchall()
        log.info("Found %d rows needing backfill (NULL or RFC2822)", len(rows))
        if not rows:
            log.info("Nothing to backfill — exit clean")
            return 0

        succeeded = 0
        normalized = 0  # rows die schon RFC2822 hatten, jetzt ISO
        skipped_task_not_found = 0
        skipped_date_unparsed = 0
        for idx, (fb_id, task_id, sender, subject, existing) in enumerate(rows, start=1):
            if idx % 25 == 1 or idx == len(rows):
                log.info(
                    "Progress %d/%d (succeeded=%d, normalized=%d, no-task=%d, no-date=%d)",
                    idx, len(rows), succeeded, normalized,
                    skipped_task_not_found, skipped_date_unparsed
                )
            iso_date = None
            # Fast-path: if existing is RFC2822-string, parse direct (kein Kanban-Lookup)
            if existing:
                try:
                    iso_date = parsedate_to_datetime(existing).isoformat()
                    normalized += 1
                except (TypeError, ValueError):
                    iso_date = None
            # Slow-path: lookup Kanban-Task-Body if no existing string
            if iso_date is None:
                body = find_task_body(task_id)
                if body is None:
                    skipped_task_not_found += 1
                    continue
                iso_date = parse_envelope_date(body)
                if iso_date is None:
                    skipped_date_unparsed += 1
                    log.warning("uid=%s task=%s: no Datum-Header in body", fb_id, task_id)
                    continue
                succeeded += 1
            conn.execute(
                "UPDATE feedback SET mail_date = ? WHERE id = ?",
                (iso_date, fb_id),
            )
        conn.commit()
        log.info(
            "DONE backfill processed=%d succeeded=%d normalized=%d no-task=%d no-date=%d",
            len(rows), succeeded, normalized, skipped_task_not_found, skipped_date_unparsed,
        )
    finally:
        conn.close()
        for c in _board_conns.values():
            try:
                c.close()
            except Exception:
                pass
        _board_conns.clear()
    return 0


def main(argv: list[str]) -> int:
    db_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_DB
    return backfill(db_path)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
