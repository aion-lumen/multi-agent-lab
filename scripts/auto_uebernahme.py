#!/usr/bin/env python3
"""auto_uebernahme.py — Vier-Stimmen-Vollkonsens → feedback.actionability='uebernommen'.

Bauteil 2 (2026-06-06): Auto-Promotion einer Mail in den Council-Pool wenn:
  - Heuristik-Stimme (feedback.domain, feedback.actionability) == (immo, actionable)
  - Alle 3 Validator-Stimmen (validator_opinions) == (immo, actionable)
  - Kein Block-Marker in feedback.heuristic_markers
    (TIER1_BLOCKER_MARKERS plus 'out_of_corridor:*').

Wird nach validator_batch (oder als standalone) ausgefuehrt. Liest validator_opinions
aus folio.db read-only, schreibt UPDATE auf feedback.actionability='uebernommen'
in feedback.db (multi-agent ist owner).

Re-Promotion ist no-op (UPDATE nur wenn aktueller Wert noch 'actionable').
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from domain_actionability import TIER1_BLOCKER_MARKERS  # noqa: E402

log = logging.getLogger("auto_uebernahme")

from paths import FEEDBACK_DB, FOLIO_DB  # noqa: E402

# Wieviele Validator-Stimmen erwartet werden. Liest Erwartung aus regelwerk.
EXPECTED_VALIDATOR_COUNT = 3


def _has_block_marker(markers: list[str]) -> bool:
    """True wenn irgendein Block-Marker in markers steht.

    Block-Marker: TIER1_BLOCKER_MARKERS (projektiert / zwangsversteigerung /
    price_on_request) plus any 'out_of_corridor:*'.
    """
    for m in markers:
        if m in TIER1_BLOCKER_MARKERS:
            return True
        if m.startswith("out_of_corridor:"):
            return True
    return False


def _parse_markers(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _validator_opinions_for(folio_conn: sqlite3.Connection, feedback_id: int) -> list[tuple[str, str]]:
    """Returnt Liste von (validator_domain, validator_actionability) fuer eine feedback_id."""
    rows = folio_conn.execute(
        """SELECT validator_domain, validator_actionability
           FROM validator_opinions
           WHERE feedback_id = ?""",
        (feedback_id,),
    ).fetchall()
    return [(d, a) for (d, a) in rows if d is not None and a is not None]


def _is_eligible_for_uebernommen(
    heur_domain: str, heur_action: str,
    opinions: list[tuple[str, str]],
    markers: list[str],
) -> bool:
    """Vier-Stimmen-Vollkonsens + immo + actionable + kein Block."""
    if heur_domain != "immo" or heur_action != "actionable":
        return False
    if len(opinions) < EXPECTED_VALIDATOR_COUNT:
        return False
    for (d, a) in opinions:
        if d != "immo" or a != "actionable":
            return False
    if _has_block_marker(markers):
        return False
    return True


def promote_eligible(
    feedback_ids: list[int] | None = None,
    *,
    dry_run: bool = False,
    run_uuid: str | None = None,
) -> dict:
    """Promotion-Hauptfunktion.

    feedback_ids: wenn None → alle feedback-rows mit actionability='actionable'
    und domain='immo' werden gescannt. Sonst nur diese IDs.
    run_uuid: 2026-06-07 Pre-Bauteil — wenn gesetzt, schreibt pro promote/
    no_consensus eine Log-Zeile in folio.db.worker_run_logs.

    Returnt dict {checked, eligible, promoted}.
    """
    if not FEEDBACK_DB.exists():
        log.error("feedback.db nicht gefunden: %s", FEEDBACK_DB)
        return {"checked": 0, "eligible": 0, "promoted": 0}
    if not FOLIO_DB.exists():
        log.error("folio.db nicht gefunden: %s", FOLIO_DB)
        return {"checked": 0, "eligible": 0, "promoted": 0}

    fb_conn = sqlite3.connect(str(FEEDBACK_DB))
    fb_conn.row_factory = sqlite3.Row
    folio_conn = sqlite3.connect(f"file:{FOLIO_DB}?mode=ro", uri=True)

    if feedback_ids is None:
        query = (
            "SELECT id, domain, actionability, heuristic_markers FROM feedback "
            "WHERE domain='immo' AND actionability='actionable'"
        )
        rows = fb_conn.execute(query).fetchall()
    else:
        placeholders = ",".join("?" for _ in feedback_ids)
        query = (
            f"SELECT id, domain, actionability, heuristic_markers FROM feedback "
            f"WHERE id IN ({placeholders})"
        )
        rows = fb_conn.execute(query, feedback_ids).fetchall()

    checked = 0
    eligible = 0
    promoted = 0
    # 2026-06-07 Pre-Bauteil: Log-Helper lazy importieren.
    _write_log = None
    if run_uuid:
        try:
            from folio_log_writer import write_log as _write_log  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            _write_log = None

    for r in rows:
        checked += 1
        markers = _parse_markers(r["heuristic_markers"])
        opinions = _validator_opinions_for(folio_conn, r["id"])
        if not _is_eligible_for_uebernommen(
            r["domain"], r["actionability"], opinions, markers
        ):
            # 2026-06-07 Pre-Bauteil: no_consensus-Log (nur fuer immo+actionable,
            # andere actionabilities sind 'no fit' und nicht log-würdig).
            if (run_uuid and _write_log is not None
                    and r["domain"] == "immo" and r["actionability"] == "actionable"):
                try:
                    _write_log(run_uuid, "auto", "no_consensus",
                               f"#{r['id']} · nicht eligible (stimmen/blocker)",
                               mail_id=r["id"])
                except Exception:  # noqa: BLE001
                    pass
            continue
        eligible += 1
        if dry_run:
            log.info("[dry-run] would promote feedback_id=%d", r["id"])
            continue
        # Re-Promotion no-op: WHERE actionability='actionable' garantiert
        # idempotent (zweiter Lauf macht nichts wenn schon 'uebernommen').
        upd = fb_conn.execute(
            "UPDATE feedback SET actionability='uebernommen' "
            "WHERE id=? AND actionability='actionable'",
            (r["id"],),
        )
        if upd.rowcount > 0:
            promoted += 1
            log.info("promoted feedback_id=%d → uebernommen", r["id"])
            if run_uuid and _write_log is not None:
                try:
                    _write_log(run_uuid, "auto", "promoted",
                               f"4/4 einig · #{r['id']} → uebernommen",
                               mail_id=r["id"])
                except Exception:  # noqa: BLE001
                    pass

    if not dry_run:
        fb_conn.commit()
    fb_conn.close()
    folio_conn.close()

    return {"checked": checked, "eligible": eligible, "promoted": promoted}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="auto_uebernahme")
    ap.add_argument("--ids", type=int, nargs="*", default=None,
                    help="optional list of feedback_ids to scan; default scans all immo+actionable")
    ap.add_argument("--dry-run", action="store_true", default=False,
                    help="check eligibility without writing")
    ap.add_argument("--run-uuid", default=None,
                    help="folio worker_runs.run_uuid (für Cross-DB-Logs)")
    args = ap.parse_args()

    from folio_log_writer import get_run_uuid_from_env_or_args
    ru = get_run_uuid_from_env_or_args(args.run_uuid)
    stats = promote_eligible(feedback_ids=args.ids, dry_run=args.dry_run, run_uuid=ru)
    log.info("auto_uebernahme: checked=%d eligible=%d promoted=%d (dry_run=%s)",
             stats["checked"], stats["eligible"], stats["promoted"], args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
