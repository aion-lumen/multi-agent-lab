#!/usr/bin/env python3
"""migrate_subject_wordboundary_reclass_2026-05-27.py — Phase A.2 Bestandskorrektur.

Updated feedback.domain + feedback.actionability für Mails, die mit der neuen
Word-Boundary-Logik anders klassifiziert würden als bisher in der DB.

Aus dem A.1-Audit-Report (Architekt-Go Option B = alle 3 Kipper):
  - id=15  job → shopping  (Amazon-Paket-zustellen)
  - id=81  unsorted → werbung (Steam-Angebot, Pre-F.8.5 Re-Classify-Win)
  - id=375 job → shopping  (Amazon-Paket-zustellen)

Idempotent: Re-run schreibt nur, wenn aktuelle DB-Werte noch von neuer Logik
abweichen. Dry-run-default; `--apply` für Commit.

Run:
    .venv/bin/python3 scripts/migrate_subject_wordboundary_reclass_2026-05-27.py        # dry-run
    .venv/bin/python3 scripts/migrate_subject_wordboundary_reclass_2026-05-27.py --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from domain_actionability import classify_domain_actionability, load_user_context

DB_PATH = Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "state" / "feedback.db"

# Explicit allowlist per Architekt-Go Option B (A.1-Report). Hardcoded statt
# „all flips" damit kein versehentliches Schreiben von anderen Drift-Quellen.
ALLOWED_IDS = {15, 81, 375}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Commit changes (default: dry-run)")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found")
        return 1

    ctx = load_user_context()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, sender, subject, plugin_value, mail_date, "
        "domain, actionability, effective_actionability "
        "FROM feedback WHERE id IN ({})".format(",".join("?" * len(ALLOWED_IDS))),
        sorted(ALLOWED_IDS),
    ).fetchall()

    if len(rows) != len(ALLOWED_IDS):
        found = {r["id"] for r in rows}
        missing = ALLOWED_IDS - found
        print(f"WARNING: expected ids {ALLOWED_IDS}, missing {missing}")

    updates: list[tuple[int, str, str, str, str]] = []
    no_change: list[int] = []

    for r in rows:
        result = classify_domain_actionability(
            sender=r["sender"],
            subject=r["subject"],
            mail_date=r["mail_date"],
            plugin_class=r["plugin_value"],
            user_context=ctx,
        )
        new_dom = result.domain
        new_act = result.actionability
        old_dom = r["domain"]
        old_act = r["actionability"]
        if new_dom == old_dom and new_act == old_act:
            no_change.append(r["id"])
            continue
        updates.append((r["id"], old_dom, new_dom, old_act, new_act))

    print("=" * 80)
    print("PHASE A.2 — Word-Boundary-Bestandskorrektur (Option B)")
    print(f"DB: {DB_PATH}")
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")
    print("=" * 80)
    print()
    print("## Planned UPDATEs")
    print()
    print("| id | old_domain | new_domain | old_action | new_action |")
    print("|---|---|---|---|---|")
    for row_id, od, nd, oa, na in updates:
        print(f"| {row_id} | {od} | {nd} | {oa} | {na} |")
    print()

    if no_change:
        print(f"Already up-to-date (idempotent skip): {no_change}")
        print()

    if not updates:
        print("Nothing to apply.")
        conn.close()
        return 0

    if not args.apply:
        print(f"Dry-run only. Re-run with --apply to commit {len(updates)} updates.")
        conn.close()
        return 0

    cur = conn.cursor()
    for row_id, _od, nd, _oa, na in updates:
        cur.execute(
            "UPDATE feedback SET domain = ?, actionability = ?, "
            "effective_actionability = ? WHERE id = ?",
            (nd, na, na, row_id),
        )
    conn.commit()
    conn.close()
    print(f"APPLIED {len(updates)} updates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
