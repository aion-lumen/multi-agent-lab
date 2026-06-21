#!/usr/bin/env python3
"""imap_cleanup.py — Bauteil 6 (2026-06-09): IMAP-Aufräum-Aktion.

Nach Pipeline-Klassifikation (production_worker + validator_batch +
auto_uebernahme) wertet dieses Stand-alone-Script den DB-Zustand aus
und führt Yahoo-IMAP-Aktionen:

- Konsens-immo (4 Stimmen einig + immo + actionable + kein Block)
    → verschieben in target_folders.immo, mark-read
- Konsens-job (4 Stimmen einig + job + actionable + kein Block)
    → verschieben in target_folders.job, mark-read
- User-verworfen (object_status_override.status_tag='verworfen')
- User-stumm (mail_actionability_override='archive-silent')
- Korrigiert-zu-stumm (corrections.corrected_actionability='archive-silent')
    → vor Move: corrections-Snapshot (heuristic_markers_snapshot)
    → verschieben in Yahoo-Papierkorb (Trash)
- actionable / disagreement → keine IMAP-Aktion

Sanity-Check: max_per_run aus regelwerk.yaml limit. Bei Überschreitung
abort + warn-log in folio.worker_run_logs.

Reuse:
- auto_uebernahme._is_eligible_for_uebernommen (Konsens-immo)
- folio_log_writer.write_log (Warn-Log)
- scripts/imap_actions.py (F2 — ensure_folder, move_to_folder,
  mark_as_read, move_to_trash)

Cross-DB-Write-Ausnahme: schreibt in folio.corrections direct (analog
folio_log_writer-Pattern). Eintrag in
multi-agent/docs/cross-db-write-ausnahmen.md ergänzt.

Usage:
    python imap_cleanup.py --dry-run     # zeigt aktionen ohne IMAP-touch
    python imap_cleanup.py                # live
    python imap_cleanup.py --max 5        # sanity-check-trigger-test
"""
from __future__ import annotations

import argparse
import imaplib
import json
import logging
import os
import sqlite3
import ssl
import subprocess
import sys
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auto_uebernahme import _is_eligible_for_uebernommen, _parse_markers, _validator_opinions_for  # noqa: E402
from domain_actionability import TIER1_BLOCKER_MARKERS  # noqa: E402
from imap_actions import (  # noqa: E402
    ensure_folder, folder_exists, mark_as_read, merge_folder,
    move_to_folder, move_to_trash, rename_folder,
)

log = logging.getLogger("imap_cleanup")

from paths import ACCOUNTS_TOML, FEEDBACK_DB, FOLIO_DB, REGELWERK_YAML  # noqa: E402

REGELWERK = REGELWERK_YAML

YAHOO_ACCOUNT_ID = "yahoo"  # siehe accounts.toml [accounts.yahoo]


def _load_config() -> dict:
    """Lädt imap_cleanup-Block aus regelwerk.yaml."""
    import yaml  # lazy import — nur bei script-execution
    with REGELWERK.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("imap_cleanup", {})


def _load_yahoo_account() -> dict:
    """Lädt yahoo-account-config aus accounts.toml."""
    with ACCOUNTS_TOML.open("rb") as f:
        data = tomllib.load(f)
    accounts = data.get("accounts", {})
    yahoo = accounts.get(YAHOO_ACCOUNT_ID)
    if not yahoo:
        raise RuntimeError(f"account '{YAHOO_ACCOUNT_ID}' not in {ACCOUNTS_TOML}")
    return yahoo


def _get_password(bw_item: str) -> str:
    """Bitwarden-Lookup via life-mail-passwd shell-helper."""
    result = subprocess.run(
        ["life-mail-passwd", bw_item],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _imap_connect(account: dict) -> imaplib.IMAP4_SSL:
    """Verbindet sich, loggt ein, SELECTet INBOX (für UID-Operationen)."""
    ctx = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(account["host"], account["port"], ssl_context=ctx)
    conn.login(account["login"], _get_password(account["bw_item"]))
    typ, _ = conn.select("INBOX")
    if typ != "OK":
        raise RuntimeError("Cannot SELECT INBOX")
    return conn


# ---- Klassifikations-Pfade ---------------------------------------------------


def _is_eligible_for_job(
    heur_domain: str, heur_action: str,
    opinions: list[tuple[str, str]],
    markers: list[str],
) -> bool:
    """Analog _is_eligible_for_uebernommen, aber für job-domain.

    Block-Marker greift unverändert (sind immo-spezifisch, aber tier1-
    Marker auf job-Mails wären verdächtig — vorerst gleicher Filter).
    """
    if heur_domain != "job" or heur_action != "actionable":
        return False
    if len(opinions) < 3:
        return False
    for (d, a) in opinions:
        if d != "job" or a != "actionable":
            return False
    for m in markers:
        if m in TIER1_BLOCKER_MARKERS:
            return False
        if m.startswith("out_of_corridor:"):
            return False
    return True


def _classify_mails(fb_conn: sqlite3.Connection, folio_conn: sqlite3.Connection) -> dict:
    """Geht durch alle feedback-rows mit imap_uid und klassifiziert."""
    fb_conn.row_factory = sqlite3.Row
    rows = fb_conn.execute(
        """SELECT id, imap_uid, domain, actionability, heuristic_markers, account_id
           FROM feedback
           WHERE imap_uid IS NOT NULL AND account_id = ?""",
        (YAHOO_ACCOUNT_ID,),
    ).fetchall()

    # Cross-DB-overrides
    override_rows = folio_conn.execute(
        """SELECT feedback_id, overridden_actionability
           FROM mail_actionability_override
           WHERE id IN (
             SELECT MAX(id) FROM mail_actionability_override
             GROUP BY feedback_id
           )"""
    ).fetchall()
    override_map = {fid: act for (fid, act) in override_rows}

    correction_rows = folio_conn.execute(
        """SELECT feedback_id, corrected_actionability, correction_marker
           FROM corrections
           WHERE id IN (
             SELECT MAX(id) FROM corrections
             GROUP BY feedback_id
           )"""
    ).fetchall()
    correction_map = {
        fid: {"actionability": act, "marker": mark}
        for (fid, act, mark) in correction_rows
    }

    # object_status_override-Pfad: hier nicht direkt — verworfen-Aktion
    # passiert über Council-UI, nicht über Mail-Tab. Wenn ein Mail-feedback
    # zu einem Council-Object führt das verworfen wurde, ist das
    # transitiv — wir erfassen es nicht in dieser Schleife.

    buckets = {
        "consensus_immo": [],   # list[(feedback_id, imap_uid, markers)]
        "consensus_job": [],
        # Bauteil-7 G5/Schärfung (2026-06-09): Auto-Reply-Bucket fuer
        # Makler-Korrespondenz. Wird in _AionLumen/Korrespondenz
        # verschoben (NICHT Trash) — bleibt fuer Bauteil 8 Mail-
        # Council-Verlinkung erhalten. V1 ohne Konsens-Anforderung:
        # feedback.actionability='auto_reply' alleine reicht.
        "consensus_auto_reply": [],
        # Bauteil-12 (2026-06-10) Shopping + Werbung:
        # - shopping + archive-silent → _AionLumen/Shopping (behalten
        #   für Buchhaltung). shopping + actionable bleibt in INBOX
        #   (User soll Lieferungs-Probleme direkt sehen).
        # - werbung (unabhängig actionability) → Trash (werbung ist
        #   per Architekt-Definition stumm).
        # User-Override-Vorrang (user_dismissed) bleibt vor beiden.
        "consensus_shopping": [],
        "consensus_werbung": [],
        "user_dismissed": [],   # archive-silent via correction oder override
        "no_action": [],
    }

    for r in rows:
        fid = r["id"]
        markers = _parse_markers(r["heuristic_markers"])
        uid = r["imap_uid"]

        # User-Action hat Vorrang (corrections / overrides → stumm)
        corr = correction_map.get(fid)
        ovr = override_map.get(fid)
        # Latest-wins zwischen correction und override würde parseTs-Logik
        # brauchen (siehe 2.7c). Für Bauteil 6 reichen die zwei Marker
        # für stumm-Detektion — wenn beides existiert und beides stumm
        # → stumm. Sonst weniger streng.
        is_dismissed = False
        if corr and corr.get("actionability") == "archive-silent":
            is_dismissed = True
        if ovr == "archive-silent":
            is_dismissed = True
        if is_dismissed:
            buckets["user_dismissed"].append({
                "feedback_id": fid,
                "imap_uid": uid,
                "markers": markers,
                "correction_marker": corr.get("marker") if corr else None,
            })
            continue

        # Bauteil-7 G5: Auto-Reply-Pfad (Heuristik allein, ohne Konsens).
        # Wirkt nach user_dismissed (User-Override hat Vorrang).
        if r["actionability"] == "auto_reply":
            buckets["consensus_auto_reply"].append({
                "feedback_id": fid, "imap_uid": uid, "markers": markers,
            })
            continue

        # Bauteil-12 (2026-06-10): Werbung → Trash (unabhängig
        # actionability — werbung ist per Definition stumm).
        if r["domain"] == "werbung":
            buckets["consensus_werbung"].append({
                "feedback_id": fid, "imap_uid": uid, "markers": markers,
            })
            continue

        # Bauteil-12 (2026-06-10): Shopping + archive-silent
        # → _AionLumen/Shopping (Buchhaltung). Shopping + actionable
        # bleibt INBOX (no_action) damit User Lieferungs-Probleme/
        # Reklamationen direkt sieht.
        if r["domain"] == "shopping" and r["actionability"] == "archive-silent":
            buckets["consensus_shopping"].append({
                "feedback_id": fid, "imap_uid": uid, "markers": markers,
            })
            continue

        # Bauteil-8 (2026-06-09): Auto-Promotion-Pfad. auto_uebernahme
        # setzt feedback.actionability='uebernommen' wenn 4/4-Konsens
        # erreicht ist — danach kommt _is_eligible_for_uebernommen mit
        # heur_action='uebernommen' nicht mehr durch (checkt nur
        # 'actionable'). Direkter Bucket-Eintrag für promoted Mails:
        # per Definition consensus-immo, gehört nach _AionLumen/Immo.
        if r["domain"] == "immo" and r["actionability"] == "uebernommen":
            buckets["consensus_immo"].append({
                "feedback_id": fid, "imap_uid": uid, "markers": markers,
            })
            continue

        # Konsens-Pfade (nur wenn nicht user-action und kein auto_reply
        # und nicht bereits promoted)
        opinions = _validator_opinions_for(folio_conn, fid)

        if _is_eligible_for_uebernommen(
            r["domain"], r["actionability"], opinions, markers,
        ):
            buckets["consensus_immo"].append({
                "feedback_id": fid, "imap_uid": uid, "markers": markers,
            })
            continue

        if _is_eligible_for_job(
            r["domain"], r["actionability"], opinions, markers,
        ):
            buckets["consensus_job"].append({
                "feedback_id": fid, "imap_uid": uid, "markers": markers,
            })
            continue

        buckets["no_action"].append({"feedback_id": fid})

    return buckets


# ---- corrections-Snapshot ----------------------------------------------------


def _write_correction_snapshot(
    folio_conn: sqlite3.Connection,
    feedback_id: int,
    imap_uid: int,
    markers: list[str],
    correction_marker_csv: str | None,
    source: str,
) -> None:
    """Snapshot in folio.corrections vor dem Trash-Move.

    Cross-DB-write — analog folio_log_writer-Pattern.
    """
    now = datetime.now(timezone.utc).isoformat()
    folio_conn.execute(
        """INSERT INTO corrections
           (feedback_id, imap_uid, previous_action, corrected_action,
            corrected_domain, corrected_actionability, note,
            correction_marker, heuristic_markers_snapshot, source, corrected_at)
           VALUES (?, ?, NULL, 'immo/archive-silent',
                   NULL, 'archive-silent', NULL,
                   ?, ?, ?, ?)""",
        (
            feedback_id, imap_uid,
            correction_marker_csv,
            json.dumps(markers),  # JSON-array (siehe F1)
            source,
            now,
        ),
    )
    folio_conn.commit()


# ---- Sanity-Check + Warn-Log ------------------------------------------------


def _write_warn_log(folio_conn: sqlite3.Connection, run_uuid: str, message: str) -> None:
    """Warn-INSERT in folio.worker_run_logs."""
    folio_conn.execute(
        """INSERT INTO worker_run_logs
           (run_uuid, seq, voice, mail_id, object_id, event_type, message, level)
           VALUES (?, COALESCE((SELECT MAX(seq) FROM worker_run_logs WHERE run_uuid = ?), 0) + 1,
                   'imap_cleanup', NULL, NULL, 'info', ?, 'warn')""",
        (run_uuid, run_uuid, message),
    )
    folio_conn.commit()


# ---- Main -------------------------------------------------------------------


def _handle_paketzustellung_migration(conn, target_shopping_folder: str) -> None:
    """Bauteil-12 (2026-06-10): einmalige Yahoo-Ordner-Umbenennung
    Packetzustellung → _AionLumen/Shopping. Idempotent: nach
    erfolgreichem Run ist Source weg, nächster Run macht silent no-op.

    Drei Fälle:
    1. Packetzustellung existiert nicht → nichts zu tun (silent).
    2. Packetzustellung existiert, Shopping nicht → RENAME.
    3. Beide existieren → MERGE (COPY Mails + DELETE Source).
    """
    # Bauteil-12 Bug-Fix (2026-06-10): Yahoo-Folder heißt "Paketzustellung"
    # (richtig deutsch), nicht "Packetzustellung" (Direktive-Tippfehler).
    PAKET = "Paketzustellung"
    if not folder_exists(conn, PAKET):
        log.debug("paketzustellung migration: source not present — skip")
        return
    if not folder_exists(conn, target_shopping_folder):
        log.info("paketzustellung migration: RENAME %s → %s",
                 PAKET, target_shopping_folder)
        rename_folder(conn, PAKET, target_shopping_folder)
        return
    log.info("paketzustellung migration: MERGE %s → %s (target existed)",
             PAKET, target_shopping_folder)
    moved = merge_folder(conn, PAKET, target_shopping_folder)
    log.info("paketzustellung migration: merged %d mails", moved)


def run(dry_run: bool = False, max_override: int | None = None) -> dict:
    config = _load_config()
    if not config.get("enabled", False) and not dry_run:
        log.warning("imap_cleanup disabled in regelwerk.yaml — exit")
        return {"enabled": False}

    max_per_run = max_override or int(config.get("max_per_run", 50))
    folders = config.get("target_folders", {})
    folder_immo = folders.get("immo", "_AionLumen/Immo")
    folder_job = folders.get("job", "_AionLumen/Job")
    # Bauteil-7 G5/Schärfung (2026-06-09): Korrespondenz-Ordner fuer
    # Makler-Auto-Replies. Bewusst NICHT Trash — Mails bleiben fuer
    # Bauteil 8 Mail-Council-Verlinkung verfuegbar.
    folder_auto_reply = folders.get("auto_reply", "_AionLumen/Korrespondenz")
    # Bauteil-12 (2026-06-10) Shopping-Ordner fuer Zahlungsbestätigungen
    # (Buchhaltungs-Aufbewahrung). Werbung geht in Papierkorb, kein
    # eigener Ordner-Eintrag.
    folder_shopping = folders.get("shopping", "_AionLumen/Shopping")

    fb_conn = sqlite3.connect(f"file:{FEEDBACK_DB}?mode=ro", uri=True)
    folio_conn = sqlite3.connect(str(FOLIO_DB))
    buckets = _classify_mails(fb_conn, folio_conn)
    fb_conn.close()

    log.info("classified: %s",
             {k: len(v) for k, v in buckets.items()})

    if dry_run:
        log.info("--- DRY-RUN: keine IMAP-aktionen ---")
        return {
            "enabled": True, "dry_run": True,
            **{k: len(v) for k, v in buckets.items()},
        }

    # ---- LIVE: IMAP-Verbindung ----
    yahoo = _load_yahoo_account()
    conn = _imap_connect(yahoo)
    run_uuid = f"imap-cleanup-{uuid.uuid4().hex[:8]}"
    moved_total = 0

    def _within_limit(want: int) -> bool:
        nonlocal moved_total
        if moved_total + want > max_per_run:
            _write_warn_log(
                folio_conn, run_uuid,
                f"imap_cleanup aborted at {moved_total} moved + {want} pending, "
                f"threshold {max_per_run} reached",
            )
            log.warning("sanity-check trip at %d + %d > %d", moved_total, want, max_per_run)
            return False
        return True

    try:
        # Bauteil-12 (2026-06-10): einmalige Migration Packetzustellung
        # → _AionLumen/Shopping. Idempotent: nach erfolgreichem
        # Rename/Merge ist Source weg, nächster Run macht no-op.
        _handle_paketzustellung_migration(conn, folder_shopping)

        # Konsens-Folders idempotent anlegen
        ensure_folder(conn, folder_immo)
        ensure_folder(conn, folder_job)
        ensure_folder(conn, folder_auto_reply)
        ensure_folder(conn, folder_shopping)

        # Bauteil-12 Bug-Fix (2026-06-10): nach ensure_folder/CREATE
        # kann der selected-folder-state bei Yahoo verloren gehen.
        # Re-select INBOX vor allen UID-COPY-Operationen damit die
        # UIDs vom richtigen Source-Folder gelesen werden.
        conn.select("INBOX")

        # --- Konsens-immo ---
        immo_uids = [m["imap_uid"] for m in buckets["consensus_immo"]]
        if immo_uids and _within_limit(len(immo_uids)):
            move_to_folder(conn, immo_uids, folder_immo)
            mark_as_read(conn, immo_uids)
            moved_total += len(immo_uids)

        # --- Konsens-job ---
        job_uids = [m["imap_uid"] for m in buckets["consensus_job"]]
        if job_uids and _within_limit(len(job_uids)):
            move_to_folder(conn, job_uids, folder_job)
            mark_as_read(conn, job_uids)
            moved_total += len(job_uids)

        # --- Bauteil-7 G5: Auto-Reply → _AionLumen/Korrespondenz ---
        auto_reply_uids = [m["imap_uid"] for m in buckets["consensus_auto_reply"]]
        if auto_reply_uids and _within_limit(len(auto_reply_uids)):
            move_to_folder(conn, auto_reply_uids, folder_auto_reply)
            mark_as_read(conn, auto_reply_uids)
            moved_total += len(auto_reply_uids)

        # --- Bauteil-12: Shopping → _AionLumen/Shopping (Buchhaltung) ---
        shopping_uids = []
        for m in buckets["consensus_shopping"]:
            _write_correction_snapshot(
                folio_conn,
                feedback_id=m["feedback_id"],
                imap_uid=m["imap_uid"],
                markers=m["markers"],
                correction_marker_csv=None,
                source="imap_cleanup_shopping",
            )
            shopping_uids.append(m["imap_uid"])
        if shopping_uids and _within_limit(len(shopping_uids)):
            move_to_folder(conn, shopping_uids, folder_shopping)
            mark_as_read(conn, shopping_uids)
            moved_total += len(shopping_uids)

        # --- Bauteil-12: Werbung → Trash (kein eigener Ordner) ---
        werbung_uids = []
        for m in buckets["consensus_werbung"]:
            _write_correction_snapshot(
                folio_conn,
                feedback_id=m["feedback_id"],
                imap_uid=m["imap_uid"],
                markers=m["markers"],
                correction_marker_csv=None,
                source="imap_cleanup_werbung",
            )
            werbung_uids.append(m["imap_uid"])
        if werbung_uids and _within_limit(len(werbung_uids)):
            move_to_trash(conn, werbung_uids)
            moved_total += len(werbung_uids)

        # --- User-stumm → corrections-snapshot + trash ---
        dismissed_uids = []
        for m in buckets["user_dismissed"]:
            _write_correction_snapshot(
                folio_conn,
                feedback_id=m["feedback_id"],
                imap_uid=m["imap_uid"],
                markers=m["markers"],
                correction_marker_csv=m.get("correction_marker"),
                source="imap_cleanup",
            )
            dismissed_uids.append(m["imap_uid"])
        if dismissed_uids and _within_limit(len(dismissed_uids)):
            move_to_trash(conn, dismissed_uids)
            moved_total += len(dismissed_uids)

    finally:
        try:
            conn.logout()
        except Exception:
            pass
        folio_conn.close()

    log.info("done. moved_total=%d", moved_total)
    return {
        "enabled": True, "dry_run": False,
        "moved_total": moved_total,
        **{k: len(v) for k, v in buckets.items()},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="kein IMAP-touch, nur classify+log")
    parser.add_argument("--max", type=int, help="override max_per_run aus regelwerk.yaml")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    result = run(dry_run=args.dry_run, max_override=args.max)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
