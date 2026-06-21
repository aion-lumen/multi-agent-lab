#!/usr/bin/env python3
"""folio_corrections.py — F.6 Reader-Stub für Folio-DB corrections-Table.

Folio (`~/.folio/folio.db` by default, overridable via FOLIO_DB_PATH env-var)
speichert User-Korrekturen der Worker-Klassifikation. Dieses Modul liest sie
read-only, damit der Worker in F.7+ daraus Heuristik-Refinement-Signale
ableiten kann (z.B. "User korrigiert immowelt.de immer von keep →
move_immo_portal").

F.6-Scope: nur Reader-API. Keine Heuristik-Use im Worker noch.

Robust: returnt [] / {} wenn folio.db nicht existiert oder leer ist —
Worker funktioniert auch ohne Folio-Setup.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional


# Path lives outside any project tree by default so dev-tool watchers (vite's
# chokidar) don't trigger reloads on every DB write. Override via FOLIO_DB_PATH.
FOLIO_DB = Path(os.environ.get("FOLIO_DB_PATH", str(Path.home() / ".folio" / "folio.db")))


def _open_readonly() -> Optional[sqlite3.Connection]:
    if not FOLIO_DB.exists():
        return None
    try:
        return sqlite3.connect(f"file:{FOLIO_DB}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return None


def get_corrections_by_sender(sender_domain: str) -> list[dict]:
    """Return all corrections whose feedback-row sender contains the domain.

    Note: corrections table has imap_uid but not sender — sender lives in
    feedback.db. We require cross-DB lookup. For F.6-Stub-Scope: return
    only correction-rows, caller can JOIN if needed.
    """
    conn = _open_readonly()
    if conn is None:
        return []
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT id, feedback_id, imap_uid, previous_action,
                      corrected_action, note, source, corrected_at
               FROM corrections
               ORDER BY corrected_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_correction_stats() -> dict:
    """Summary stats: total corrections, by corrected_action, by source."""
    conn = _open_readonly()
    if conn is None:
        return {"total": 0, "by_action": {}, "by_source": {}}
    try:
        total = conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]
        by_action = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT corrected_action, COUNT(*) FROM corrections GROUP BY corrected_action"
            ).fetchall()
        }
        by_source = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT source, COUNT(*) FROM corrections GROUP BY source"
            ).fetchall()
        }
        return {"total": total, "by_action": by_action, "by_source": by_source}
    finally:
        conn.close()


def get_latest_correction_for_feedback(feedback_id: int) -> Optional[dict]:
    """Return the latest correction for a given feedback row, or None."""
    conn = _open_readonly()
    if conn is None:
        return None
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT id, feedback_id, imap_uid, previous_action,
                      corrected_action, note, source, corrected_at
               FROM corrections
               WHERE feedback_id = ?
               ORDER BY corrected_at DESC
               LIMIT 1""",
            (feedback_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


if __name__ == "__main__":
    # CLI-Smoke: print stats
    stats = get_correction_stats()
    print(f"folio.db corrections stats: {stats}")
