"""seed_pipeline_demo.py — Demo-Seed für feedback.db + folio.db.

Erzeugt eine "lived-in" Pipeline-Demo-State, synchronisiert mit der erweiterten
demo_quickstart.json-Fixture (40 Mails, 5 Domains). Synthetisch, deterministisch,
keine LM-Studio- oder Yahoo-IMAP-Calls nötig.

Befüllt:
  feedback.db
    - 40 Rows aus tests/fixtures/imap/demo_quickstart.json (1:1 mit Heuristik-
      Suggested-Action laut Fixture-Subject/Sender). Einige als 'uebernommen'
      markiert für 4/4-Konsens-Demo.

  folio.db
    - 1 silent Worker-Run (5 mails tranche, completed)
    - 1 Validator-Run (parent_run_uuid = Worker-UUID, completed)
    - worker_run_logs: 5 heuristik|classified im Silent + 15 voice|validated im
      Validator (5 mails × 3 voices)
    - worker_run_summary: diverse Block-Gründe (out_of_corridor, decay,
      price_on_request, projektiert) plus uebernommen/actionable/archive_silent
      counts
    - validator_opinions: 15 Rows (5 mails × 3 models)
    - hauskauf_workflow: 3 Rows (offen / in_arbeit / erledigt) referencing
      die Council-Objekte aus seed_council_demo.py

Idempotent: prüft auf den existierenden demo-silent-run-uuid und überspringt
bei Wiederholungen. --force löscht Demo-Rows und seedet neu.

NICHT für Produktion — Fixtures sind fiktiv (Algarve-Persona Alex+Maya).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# --- Defaults --------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_FEEDBACK_DB = _REPO_ROOT / "state" / "feedback.db"
_DEFAULT_FOLIO_DB = Path.home() / ".folio" / "folio.db"
_DEFAULT_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "imap" / "demo_quickstart.json"

# Synthetic UUIDs — same string each run, used for idempotence
DEMO_SILENT_UUID = "demo-silent-worker-20260610"
DEMO_VALIDATOR_UUID = "demo-validator-auto-20260610"

DEMO_ACCOUNT = "demo"
DEMO_BOARD = "silent-demo-2026-06-10"

# --- Heuristic classification (matches LENS_PROMPT axes) ------------------

# Per-UID-Klassifikation — pre-computed um den Worker zu replizieren ohne
# echte Heuristik laufen zu lassen. Outcome-Mix laut Direktive:
#   immo (12): 4 actionable + 5 archive-silent + 3 übernommen-Kandidaten
#   job (6):   2 actionable + 4 archive-silent
#   shopping (8): 6 actionable + 2 archive
#   finance (6): 4 actionable + 2 archive
#   werbung (8): 8 archive-silent
#
# Die "übernommen"-Kandidaten sind die 3 hochwertigsten immo-Mails — die
# bekommen 4/4-Konsens (heuristik + 3 LLMs) und werden via auto_uebernahme
# auf 'uebernommen' promoviert.

UID_CLASSIFICATION: dict[int, dict[str, str | list[str]]] = {
    # immo — actionable (Substanz). plz_coords markers cross-DB feed council-
    # object distance-pill via getDistanceKmForCouncilObject (haversine vs
    # FOLIO_HOME_LAT/LNG). Lat/lng are approximate Algarve municipalities.
    90001: {"domain": "immo", "action": "actionable", "markers": ["plz_coords:37.0194,-7.9322", "plz:8000", "plz_city:Faro"]},
    90002: {"domain": "immo", "action": "actionable", "markers": ["plz_coords:37.1387,-8.0245", "plz:8100", "plz_city:Loulé"]},
    90003: {"domain": "immo", "action": "actionable", "markers": ["plz_coords:37.1281,-7.6497", "plz:8800", "plz_city:Tavira"]},
    90004: {"domain": "immo", "action": "actionable", "markers": ["plz_coords:37.1011,-8.6743", "plz:8600", "plz_city:Lagos"]},
    # immo — übernommen candidates (high-substance)
    90005: {"domain": "immo", "action": "uebernommen", "markers": ["plz_coords:37.0276,-7.8413", "plz:8700", "plz_city:Olhão"]},
    90006: {"domain": "immo", "action": "uebernommen", "markers": ["plz_coords:37.0760,-8.0204", "plz:8135", "plz_city:Almancil"]},
    90007: {"domain": "immo", "action": "uebernommen", "markers": ["plz_coords:37.0817,-8.1184", "plz:8125", "plz_city:Vilamoura"]},
    # immo — newsletter/marketing/tipps → archive-silent (mit Markern)
    90008: {"domain": "immo", "action": "archive-silent", "markers": ["out_of_corridor"]},
    90009: {"domain": "immo", "action": "archive-silent", "markers": ["decay"]},
    90010: {"domain": "immo", "action": "archive-silent", "markers": ["price_on_request"]},
    90011: {"domain": "immo", "action": "archive-silent", "markers": ["decay"]},
    90012: {"domain": "immo", "action": "archive-silent", "markers": ["projektiert"]},
    # job — actionable
    90013: {"domain": "job", "action": "actionable", "markers": []},
    90014: {"domain": "job", "action": "actionable", "markers": []},
    # job — archive-silent
    90015: {"domain": "job", "action": "archive-silent", "markers": ["decay"]},
    90016: {"domain": "job", "action": "archive-silent", "markers": ["decay"]},
    90017: {"domain": "job", "action": "archive-silent", "markers": []},
    90018: {"domain": "job", "action": "archive-silent", "markers": []},
    # shopping — actionable (Paketzustellung)
    90019: {"domain": "shopping", "action": "actionable", "markers": []},
    90020: {"domain": "shopping", "action": "actionable", "markers": []},
    90021: {"domain": "shopping", "action": "actionable", "markers": []},
    90022: {"domain": "shopping", "action": "actionable", "markers": []},
    90023: {"domain": "shopping", "action": "actionable", "markers": []},
    90024: {"domain": "shopping", "action": "actionable", "markers": []},
    # shopping — archive
    90025: {"domain": "shopping", "action": "archive", "markers": []},
    90026: {"domain": "shopping", "action": "archive", "markers": []},
    90027: {"domain": "shopping", "action": "archive", "markers": []},
    # finance — actionable
    90028: {"domain": "finance", "action": "actionable", "markers": []},
    90029: {"domain": "finance", "action": "actionable", "markers": []},
    90030: {"domain": "finance", "action": "actionable", "markers": []},
    90031: {"domain": "finance", "action": "actionable", "markers": []},
    # finance — archive
    90032: {"domain": "finance", "action": "archive", "markers": []},
    90033: {"domain": "finance", "action": "archive", "markers": []},
    # werbung — alle archive-silent
    90034: {"domain": "werbung", "action": "archive-silent", "markers": []},
    90035: {"domain": "werbung", "action": "archive-silent", "markers": []},
    90036: {"domain": "werbung", "action": "archive-silent", "markers": []},
    90037: {"domain": "werbung", "action": "archive-silent", "markers": []},
    90038: {"domain": "werbung", "action": "archive-silent", "markers": []},
    90039: {"domain": "werbung", "action": "archive-silent", "markers": []},
    90040: {"domain": "werbung", "action": "archive-silent", "markers": []},
}

# Die 5 Mails, die in der jüngsten Worker-Tranche verarbeitet wurden (für die
# Live-Detail-Demo und die validated-Logs). Mix aus Aktionsstufen.
RECENT_TRANCHE_UIDS = [90001, 90005, 90013, 90019, 90028]

# --- Validator-Opinion-Synthese -------------------------------------------

# Pro Mail × Voice ein Opinion-Row mit konsistenter domain/actionability.
# Für die 3 übernommen-Kandidaten konvergieren alle 3 Voices auf immo+actionable.
# Andere Mails: voice-Konsens mit Heuristik.

VALIDATOR_MODELS = [
    ("gemma", "gemma-4-26b-a4b-it-mlx"),
    ("qwen", "qwen3.6-35b-a3b-ud-mlx"),
    ("qwen-thinking", "qwen3-30b-a3b-thinking-2507"),
]


def classify_for_voice(uid: int, voice_short: str) -> tuple[str, str, float]:
    """Returns (domain, actionability, confidence). Mostly aligned with heuristik,
    with small per-voice noise for realism."""
    info = UID_CLASSIFICATION[uid]
    domain = info["domain"]
    action = info["action"]
    # uebernommen → all voices agree immo+actionable (that's how 4/4 happens)
    if action == "uebernommen":
        return ("immo", "actionable", 0.95 if voice_short == "qwen-thinking" else 0.88)
    # otherwise mirror the heuristik action
    confidence = {"gemma": 0.82, "qwen": 0.78, "qwen-thinking": 0.91}[voice_short]
    return (domain, action, confidence)


# --- Hauskauf rows ---------------------------------------------------------
# Reference council.db object IDs from seed_council_demo.py.

HAUSKAUF_ROWS = [
    {
        "council_object_id": "demo-loule-moradia",
        "status": "offen",
        "termin": None,
        "verhandlungspreis": None,
        "notes": "Erste Recherche — Lage prüfen, Energieklasse abklären.",
        "verdict": None,
    },
    {
        "council_object_id": "demo-faro-t3",
        "status": "in_arbeit",
        "termin": (datetime.now() + timedelta(days=8)).strftime("%Y-%m-%d"),
        "verhandlungspreis": None,
        "notes": "Besichtigung mit Maya geplant. Vorab: Nebenkostenliste anfordern.",
        "verdict": None,
    },
    {
        "council_object_id": "demo-olhao-cluster-a",
        "status": "erledigt",
        "termin": (datetime.now() - timedelta(days=21)).strftime("%Y-%m-%d"),
        "verhandlungspreis": 270000.0,
        "notes": "Verhandelt von 280k auf 270k. Notartermin in Vorbereitung.",
        "verdict": "favorisiert",
    },
]


def already_seeded_folio(conn: sqlite3.Connection) -> bool:
    cur = conn.execute("SELECT 1 FROM worker_runs WHERE run_uuid = ?", (DEMO_SILENT_UUID,))
    return cur.fetchone() is not None


def already_seeded_feedback(conn: sqlite3.Connection) -> bool:
    cur = conn.execute("SELECT 1 FROM feedback WHERE imap_uid = ?", (90001,))
    return cur.fetchone() is not None


def force_delete_demo_rows(folio: sqlite3.Connection, feedback: sqlite3.Connection) -> None:
    print("Force-mode: deleting existing demo rows…")
    folio.execute("DELETE FROM worker_run_logs WHERE run_uuid IN (?, ?)",
                  (DEMO_SILENT_UUID, DEMO_VALIDATOR_UUID))
    folio.execute("DELETE FROM worker_run_summary WHERE run_uuid IN (?, ?)",
                  (DEMO_SILENT_UUID, DEMO_VALIDATOR_UUID))
    folio.execute("DELETE FROM validator_opinions WHERE imap_uid BETWEEN 90001 AND 90040")
    folio.execute("DELETE FROM worker_runs WHERE run_uuid IN (?, ?)",
                  (DEMO_SILENT_UUID, DEMO_VALIDATOR_UUID))
    folio.execute("DELETE FROM hauskauf_workflow WHERE council_object_id LIKE 'demo-%'")
    folio.execute("DELETE FROM object_status_override WHERE council_object_id LIKE 'demo-%'")
    folio.execute("DELETE FROM object_notes WHERE council_object_id LIKE 'demo-%'")
    feedback.execute("DELETE FROM feedback WHERE imap_uid BETWEEN 90001 AND 90040")
    # Reset autoincrement counters so re-seeded rows get IDs 1, 2, 3…
    # (seed_council_demo.py's from_feedback_ids hardcodes those IDs).
    try:
        feedback.execute("DELETE FROM sqlite_sequence WHERE name = 'feedback'")
    except sqlite3.OperationalError:
        pass  # sqlite_sequence may not exist if table has no inserts ever
    folio.commit()
    feedback.commit()


def insert_feedback(feedback: sqlite3.Connection, fixture: list[dict]) -> int:
    n = 0
    for mail in fixture:
        uid = mail["uid"]
        info = UID_CLASSIFICATION.get(uid)
        if not info:
            continue
        # body_hash kann ein einfacher MD5 oder Hex-String sein
        body_hash = f"demo-{uid:08d}"
        # heuristik_action = action; user_classification + user_final_action = action
        # (Worker hat klassifiziert und User hat bestätigt, im Demo-State)
        # Mail-Queue UI (MailList.svelte:148) checks r.domain to decide
        # between DOMAIN-chip + Action-Emoji rendering vs legacy fallback.
        # We MUST set both `domain` and `actionability` columns.
        # `effective_actionability` is computed at read-time by the loader
        # (time-decay + override + correction merge) but we provide the seed.
        action_for_actionability = "actionable" if info["action"] == "uebernommen" else info["action"]
        feedback.execute(
            """INSERT INTO feedback
                   (task_id, imap_uid, sender, subject, body_hash,
                    plugin_value, plugin_confidence, plugin_evidence,
                    heuristic_suggested_action, heuristic_reason,
                    heuristic_confidence, heuristic_markers,
                    user_classification, user_final_action,
                    suggested_action_confirmed, response_time_ms,
                    timeout_occurred, created_at,
                    domain, actionability, effective_actionability, mail_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"demo-task-{uid}",
                uid,
                mail["from_addr"],
                mail["subject"][:200],
                body_hash,
                info["domain"] + "/" + info["action"],  # plugin_value
                0.85,
                json.dumps({"reason": "demo-seed"}),
                info["action"],
                f"heuristic match for {info['domain']}",
                "0.80",
                json.dumps(info["markers"]) if info["markers"] else None,
                info["action"],
                info["action"],
                1,
                850,
                0,
                mail["date"][:25],  # truncate timezone-aware fragment
                info["domain"],                       # domain column
                action_for_actionability,             # actionability column
                info["action"],                       # effective_actionability (uebernommen kept here)
                mail["date"][:25],                    # mail_date
            ),
        )
        n += 1
    return n


def get_feedback_ids(feedback: sqlite3.Connection, uids: list[int]) -> dict[int, int]:
    """Returns {imap_uid: feedback.id} for the given UIDs."""
    placeholders = ",".join("?" * len(uids))
    rows = feedback.execute(
        f"SELECT imap_uid, id FROM feedback WHERE imap_uid IN ({placeholders})", uids
    ).fetchall()
    return {uid: fid for uid, fid in rows}


def insert_worker_runs(folio: sqlite3.Connection, started_silent: str, ended_silent: str,
                       started_validator: str, ended_validator: str) -> None:
    # Silent Worker (parent)
    folio.execute(
        """INSERT INTO worker_runs
               (run_uuid, parent_run_uuid, account, board, mode, tranche_size,
                pid, status, started_at, ended_at, exit_code, mails_processed)
           VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (DEMO_SILENT_UUID, DEMO_ACCOUNT, DEMO_BOARD, "silent", 5,
         12345, "completed", started_silent, ended_silent, 0, 5),
    )
    # Validator (child of silent)
    folio.execute(
        """INSERT INTO worker_runs
               (run_uuid, parent_run_uuid, account, board, mode, tranche_size,
                pid, status, started_at, ended_at, exit_code, mails_processed)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (DEMO_VALIDATOR_UUID, DEMO_SILENT_UUID, DEMO_ACCOUNT, "last-tranche", "validator", 5,
         12346, "completed", started_validator, ended_validator, 0, 5),
    )


def insert_worker_logs(folio: sqlite3.Connection, feedback_ids: dict[int, int],
                       silent_start: str, validator_start: str) -> tuple[int, int]:
    # Silent: 5 heuristik|classified logs
    n_silent = 0
    for seq, uid in enumerate(RECENT_TRANCHE_UIDS, start=1):
        info = UID_CLASSIFICATION[uid]
        recorded = (datetime.strptime(silent_start, "%Y-%m-%d %H:%M:%S")
                    + timedelta(seconds=seq * 18)).strftime("%Y-%m-%d %H:%M:%S")
        folio.execute(
            """INSERT INTO worker_run_logs
                   (run_uuid, seq, recorded_at, voice, mail_id, event_type, message, level)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (DEMO_SILENT_UUID, seq, recorded, "heuristik",
             feedback_ids[uid], "classified",
             f"#{feedback_ids[uid]} → {info['domain']}/{info['action']}", "info"),
        )
        n_silent += 1

    # Validator: 15 voice|validated logs (5 mails × 3 voices)
    n_validator = 0
    seq = 0
    val_start_dt = datetime.strptime(validator_start, "%Y-%m-%d %H:%M:%S")
    for voice_short, _model_id in VALIDATOR_MODELS:
        for uid in RECENT_TRANCHE_UIDS:
            seq += 1
            domain, action, _ = classify_for_voice(uid, voice_short)
            # uebernommen → all-immo-actionable; validator schreibt domain/action
            v_action = "actionable" if action == "uebernommen" else action
            v_domain = "immo" if action == "uebernommen" else domain
            recorded = (val_start_dt + timedelta(seconds=seq * 22)).strftime("%Y-%m-%d %H:%M:%S")
            folio.execute(
                """INSERT INTO worker_run_logs
                       (run_uuid, seq, recorded_at, voice, mail_id, event_type, message, level)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (DEMO_VALIDATOR_UUID, seq, recorded, voice_short,
                 feedback_ids[uid], "validated",
                 f"#{feedback_ids[uid]} → {v_domain}/{v_action}", "info"),
            )
            n_validator += 1

    # Plus auto_uebernahme-Logs (2 promoted aus den 3 uebernommen-Kandidaten,
    # einer landet bei no_consensus weil ein Validator-Voice abweicht — Realismus)
    promoted_uids = [90005, 90006]  # 2 promoted
    no_consensus_uids = [90007]      # 1 no_consensus
    seq_auto = seq
    for uid in promoted_uids:
        seq_auto += 1
        recorded = (val_start_dt + timedelta(seconds=seq_auto * 22)).strftime("%Y-%m-%d %H:%M:%S")
        if uid in feedback_ids:
            folio.execute(
                """INSERT INTO worker_run_logs
                       (run_uuid, seq, recorded_at, voice, mail_id, event_type, message, level)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (DEMO_VALIDATOR_UUID, seq_auto, recorded, "auto",
                 feedback_ids[uid], "promoted",
                 f"4/4 einig · #{feedback_ids[uid]} → uebernommen", "info"),
            )
            n_validator += 1
    for uid in no_consensus_uids:
        seq_auto += 1
        recorded = (val_start_dt + timedelta(seconds=seq_auto * 22)).strftime("%Y-%m-%d %H:%M:%S")
        if uid in feedback_ids:
            folio.execute(
                """INSERT INTO worker_run_logs
                       (run_uuid, seq, recorded_at, voice, mail_id, event_type, message, level)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (DEMO_VALIDATOR_UUID, seq_auto, recorded, "auto",
                 feedback_ids[uid], "no_consensus",
                 f"#{feedback_ids[uid]} · 3/4 einig (qwen-thinking abweichend)", "info"),
            )
            n_validator += 1

    return n_silent, n_validator


def insert_validator_opinions(folio: sqlite3.Connection, feedback_ids: dict[int, int],
                              now_iso: str) -> int:
    n = 0
    for uid in RECENT_TRANCHE_UIDS:
        info = UID_CLASSIFICATION[uid]
        if uid not in feedback_ids:
            continue
        fb_id = feedback_ids[uid]
        for voice_short, model_id in VALIDATOR_MODELS:
            domain, action, confidence = classify_for_voice(uid, voice_short)
            # uebernommen wird in der Opinion als 'actionable' geschrieben — die
            # 4/4-Logik in auto_uebernahme.py promoviert das nachträglich.
            v_action = "actionable" if action == "uebernommen" else action
            v_domain = "immo" if action == "uebernommen" else domain
            folio.execute(
                """INSERT INTO validator_opinions
                       (feedback_id, account_id, imap_uid, validator_model,
                        validator_action, validator_domain, validator_actionability,
                        validator_confidence, validator_reasoning, evaluated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (fb_id, DEMO_ACCOUNT, uid, model_id,
                 f"{v_domain}/{v_action}", v_domain, v_action,
                 confidence, f"demo opinion: {v_domain}/{v_action}",
                 now_iso),
            )
            n += 1
    return n


def insert_worker_summary(folio: sqlite3.Connection,
                          fixture: list[dict],
                          feedback_ids: dict[int, int]) -> None:
    # Block-Marker-Aggregation aus UID_CLASSIFICATION quer durch alle Mails
    # für die "diverse Block-Gründe"-Demo. Real-World würde der Worker das
    # während der Heuristik-Phase aufaddieren.
    #
    # Filter: nur echte Block-Reasons in reason_breakdown — plz_coords/plz/
    # plz_city sind Lokations-Metadaten, keine Veto-Gründe.
    BLOCK_REASONS = {
        "out_of_corridor", "decay", "projektiert", "price_on_request",
        "zwangsversteigerung", "blocked_by",
    }
    breakdown: dict[str, int] = {}
    for info in UID_CLASSIFICATION.values():
        for marker in info.get("markers", []):
            if marker.split(":", 1)[0] in BLOCK_REASONS:
                breakdown[marker] = breakdown.get(marker, 0) + 1
    reason_breakdown_json = json.dumps(breakdown)
    marker_count = sum(breakdown.values())
    # Sample-Mails für Lauf-Spur-UI: 5 letzte Tranche.
    # Schema laut UI (ImportRow.svelte:5-11): {id, subject, sender, tag}
    fixture_by_uid = {m["uid"]: m for m in fixture}
    sample = []
    for uid in RECENT_TRANCHE_UIDS:
        info = UID_CLASSIFICATION[uid]
        mail = fixture_by_uid.get(uid, {})
        sample.append({
            "id": feedback_ids.get(uid),
            "subject": mail.get("subject", ""),
            "sender": mail.get("from_addr", ""),
            "tag": f"{info['domain']}/{info['action']}",
        })
    worker_imports_sample_json = json.dumps(sample, ensure_ascii=False)

    folio.execute(
        """INSERT INTO worker_run_summary
               (run_uuid, geprueft, uebernommen, actionable, archive_silent,
                council_objects, marker_count, reason_breakdown,
                worker_imports_sample, written_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (DEMO_SILENT_UUID, 5, 2, 2, 0, 2, marker_count,
         reason_breakdown_json, worker_imports_sample_json,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    # Validator summary auch (für getValidatorRun-Verlauf-Eintrag)
    folio.execute(
        """INSERT INTO worker_run_summary
               (run_uuid, geprueft, uebernommen, actionable, archive_silent,
                council_objects, marker_count, reason_breakdown,
                worker_imports_sample, written_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (DEMO_VALIDATOR_UUID, 5, 2, 2, 0, 2, 0,
         json.dumps({}), json.dumps([]),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )


def insert_object_overrides(folio: sqlite3.Connection, now_iso: str) -> int:
    """Insert status_override + note for demo-olhao-cluster-a so its cluster
    sibling (-b) shows the Provenance-Pill 'via homegate' (inherited)
    via folio's cluster-substance.ts read-through (D14)."""
    folio.execute(
        """INSERT INTO object_status_override
               (council_object_id, user_id, status_tag, recorded_at, reason, source)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("demo-olhao-cluster-a", 1, "kaufen", now_iso,
         "Maya hat zugesagt — Lage perfekt für Yoga-Studio im Erdgeschoss.",
         "user_action"),
    )
    folio.execute(
        """INSERT INTO object_notes
               (council_object_id, user_id, note_text, recorded_at, source)
           VALUES (?, ?, ?, ?, ?)""",
        ("demo-olhao-cluster-a", 1,
         "Verhandelt von 280k auf 270k. Renovação 2025 verifiziert (Energieklasse B).",
         now_iso, "user_action"),
    )
    return 2


def insert_hauskauf(folio: sqlite3.Connection) -> int:
    n = 0
    for row in HAUSKAUF_ROWS:
        folio.execute(
            """INSERT INTO hauskauf_workflow
                   (council_object_id, status, termin, verhandlungspreis,
                    notes, verdict, created_by_user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (row["council_object_id"], row["status"], row["termin"],
             row["verhandlungspreis"], row["notes"], row["verdict"], 1),
        )
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--feedback-db",
                    default=os.environ.get("FEEDBACK_DB_PATH", str(_DEFAULT_FEEDBACK_DB)))
    ap.add_argument("--folio-db",
                    default=os.environ.get("FOLIO_DB_PATH", str(_DEFAULT_FOLIO_DB)))
    ap.add_argument("--fixture",
                    default=str(_DEFAULT_FIXTURE))
    ap.add_argument("--force", action="store_true",
                    help="Re-seed even if demo rows already exist (DELETEs first)")
    args = ap.parse_args()

    fixture_path = Path(args.fixture)
    if not fixture_path.exists():
        print(f"Fixture not found at {fixture_path}", file=sys.stderr)
        return 1
    with fixture_path.open() as f:
        fixture = json.load(f)

    feedback_path = Path(args.feedback_db)
    folio_path = Path(args.folio_db)
    if not folio_path.exists():
        print(f"folio.db not found at {folio_path} — start folio dev server once to init",
              file=sys.stderr)
        return 1
    feedback_path.parent.mkdir(parents=True, exist_ok=True)

    feedback = sqlite3.connect(str(feedback_path))
    folio = sqlite3.connect(str(folio_path))
    folio.execute("PRAGMA foreign_keys = ON")
    feedback.executescript("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL, imap_uid INTEGER NOT NULL,
            sender TEXT NOT NULL, subject TEXT NOT NULL,
            body_hash TEXT NOT NULL, plugin_value TEXT,
            plugin_confidence REAL, plugin_evidence TEXT,
            heuristic_suggested_action TEXT, heuristic_reason TEXT,
            heuristic_confidence TEXT, heuristic_markers TEXT,
            user_classification TEXT, user_final_action TEXT,
            suggested_action_confirmed INTEGER, response_time_ms INTEGER,
            timeout_occurred INTEGER, created_at TEXT NOT NULL,
            UNIQUE(imap_uid)
        );
    """)
    feedback.commit()

    if (already_seeded_folio(folio) or already_seeded_feedback(feedback)):
        if args.force:
            force_delete_demo_rows(folio, feedback)
        else:
            print("Demo rows already exist — skipping (use --force to re-seed)")
            return 0

    # Timestamps für die 2 Runs (vor 1 Tag, je ~14 min lang)
    now = datetime.now()
    silent_start = (now - timedelta(days=1, hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    silent_end = (now - timedelta(days=1, hours=1, minutes=46)).strftime("%Y-%m-%d %H:%M:%S")
    validator_start = (now - timedelta(days=1, hours=1, minutes=45)).strftime("%Y-%m-%d %H:%M:%S")
    validator_end = (now - timedelta(days=1, hours=1, minutes=31)).strftime("%Y-%m-%d %H:%M:%S")
    now_iso = now.strftime("%Y-%m-%d %H:%M:%S")

    n_feedback = insert_feedback(feedback, fixture)
    feedback.commit()

    feedback_ids = get_feedback_ids(feedback, RECENT_TRANCHE_UIDS)

    insert_worker_runs(folio, silent_start, silent_end, validator_start, validator_end)
    n_silent_logs, n_validator_logs = insert_worker_logs(folio, feedback_ids,
                                                        silent_start, validator_start)
    n_opinions = insert_validator_opinions(folio, feedback_ids, now_iso)
    insert_worker_summary(folio, fixture, feedback_ids)
    n_overrides = insert_object_overrides(folio, now_iso)
    n_hauskauf = insert_hauskauf(folio)
    folio.commit()

    feedback.close()
    folio.close()

    print("Seeded folio.db + feedback.db:")
    print(f"  feedback.feedback:        {n_feedback} rows")
    print(f"  folio.worker_runs:        2 (1 silent + 1 validator, parent-linked)")
    print(f"  folio.worker_run_logs:    {n_silent_logs + n_validator_logs} "
          f"(silent={n_silent_logs}, validator={n_validator_logs})")
    print(f"  folio.worker_run_summary: 2")
    print(f"  folio.validator_opinions: {n_opinions}")
    print(f"  folio.object_status_override + object_notes: {n_overrides}")
    print(f"    → triggers cluster-inherit Provenance-Pill on demo-olhao-cluster-b")
    print(f"  folio.hauskauf_workflow:  {n_hauskauf}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
