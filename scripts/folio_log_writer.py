#!/usr/bin/env python3
"""folio_log_writer.py — Cross-DB-Write-Helper fuer folio.db.

Pre-Bauteil Pipeline-Persistenz (2026-06-07). Erweitert das etablierte
validator_opinions-Cross-DB-Write-Pattern auf worker_run_logs +
worker_run_summary. Verwendet von:
  - scripts/production_worker.py
  - scripts/validator_batch.py
  - scripts/auto_uebernahme.py

Cross-DB-Write-Ausnahme. Liste der erlaubten Schreibpunkte:
  multi-agent/docs/cross-db-write-ausnahmen.md

Run-uuid kommt via:
  - CLI-arg `--run-uuid <uuid>` (Engineer-Konvention, Caller liest via argparse)
  - env-var `FOLIO_RUN_UUID` (Fallback, von manager.ts gesetzt)
  - None → Helper no-op (Worker laeuft CLI-direkt ohne UI-Spawn)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import FOLIO_DB  # noqa: E402


def get_run_uuid_from_env_or_args(cli_value: str | None = None) -> str | None:
    """Resolve run_uuid via CLI-arg first, then env. None wenn beides leer."""
    if cli_value:
        return cli_value
    env = os.environ.get("FOLIO_RUN_UUID")
    return env if env else None


def write_log(
    run_uuid: str | None,
    voice: str,
    event_type: str,
    message: str | None,
    *,
    mail_id: int | None = None,
    object_id: str | None = None,
    level: str = "info",
) -> None:
    """INSERT in worker_run_logs. No-op wenn run_uuid None (z.B. CLI-
    Direkt-Aufruf ohne UI-Spawn). Robust gegen fehlende folio.db oder
    Schema-Lücke (Pre-Bauteil S1 nicht gemerged): keine Exception, nur
    silent skip mit stderr-Note."""
    if not run_uuid:
        return
    if not FOLIO_DB.exists():
        return
    try:
        conn = sqlite3.connect(str(FOLIO_DB))
        try:
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM worker_run_logs WHERE run_uuid = ?",
                (run_uuid,),
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO worker_run_logs
                   (run_uuid, seq, voice, mail_id, object_id, event_type, message, level)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_uuid, seq, voice, mail_id, object_id, event_type, message, level),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.OperationalError as e:
        # Tabelle existiert noch nicht (Pre-Bauteil-S1 nicht gemerged auf
        # main) — silent skip, kein Exit-Code.
        print(f"[folio_log_writer] skip (table missing?): {e}", flush=True)


def write_summary(
    run_uuid: str | None,
    *,
    geprueft: int = 0,
    uebernommen: int = 0,
    actionable: int = 0,
    archive_silent: int = 0,
    council_objects: int = 0,
    marker_count: int = 0,
    reason_breakdown: dict | None = None,
    worker_imports_sample: list | None = None,
) -> None:
    """INSERT OR REPLACE in worker_run_summary. JSON-Felder werden
    serialisiert. No-op wenn run_uuid None."""
    if not run_uuid:
        return
    if not FOLIO_DB.exists():
        return
    rb = json.dumps(reason_breakdown, ensure_ascii=False) if reason_breakdown is not None else None
    wis = (
        json.dumps(worker_imports_sample, ensure_ascii=False)
        if worker_imports_sample is not None
        else None
    )
    try:
        conn = sqlite3.connect(str(FOLIO_DB))
        try:
            conn.execute(
                """INSERT OR REPLACE INTO worker_run_summary
                   (run_uuid, geprueft, uebernommen, actionable, archive_silent,
                    council_objects, marker_count, reason_breakdown,
                    worker_imports_sample, written_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (
                    run_uuid, geprueft, uebernommen, actionable, archive_silent,
                    council_objects, marker_count, rb, wis,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.OperationalError as e:
        print(f"[folio_log_writer] summary skip (table missing?): {e}", flush=True)
