#!/usr/bin/env python3
"""audit_senderprefix_reclass.py — Phase B.4 Re-Klassifikations-Report.

Loopt alle feedback-rows, klassifiziert mit der NEUEN (post-fix) Sender-Prefix-
Logik in `classify_domain_actionability()`, und vergleicht das Ergebnis zur in
der DB gespeicherten domain. Wo's kippt → Tabelle.

Read-only: KEINE DB-Schreiben. Vorlage für Architekt-Go vor Bestandskorrektur
(B.5 macht das Schreiben).

Run:
    .venv/bin/python3 scripts/audit_senderprefix_reclass.py
"""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from domain_actionability import classify_domain_actionability, load_user_context

DB_PATH = Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "state" / "feedback.db"


def main() -> int:
    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found")
        return 1

    ctx = load_user_context()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, sender, subject, plugin_value, mail_date, domain, actionability "
        "FROM feedback ORDER BY id"
    ).fetchall()
    conn.close()

    flips: list[dict] = []
    by_from_to: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for r in rows:
        try:
            result = classify_domain_actionability(
                sender=r["sender"],
                subject=r["subject"],
                mail_date=r["mail_date"],
                plugin_class=r["plugin_value"],
                user_context=ctx,
            )
        except Exception as e:  # noqa: BLE001
            print(f"  ! id={r['id']} classify-failed: {e}")
            continue

        if result.domain != r["domain"]:
            entry = {
                "id": r["id"],
                "sender": r["sender"],
                "subject": (r["subject"] or "")[:60],
                "old_domain": r["domain"],
                "new_domain": result.domain,
                "new_markers": result.matched_markers,
            }
            flips.append(entry)
            by_from_to[(r["domain"], result.domain)].append(entry)

    print("=" * 80)
    print("PHASE B.4 — Re-Klassifikations-Report Sender-Prefix-Fix")
    print(f"DB: {DB_PATH}")
    print(f"Total rows: {len(rows)}")
    print(f"Flips (old → new): {len(flips)}")
    print("=" * 80)
    print()

    if not flips:
        print("KEINE Kipper — alle Mails klassifizieren mit neuer Logik identisch.")
        print("→ Bedeutet: aktueller DB-Korpus hat keinen Sender, der von der")
        print("  Substring-Trap betroffen war (linkedin-info-style etc.).")
        return 0

    print("## Kipper-Aggregation (old_domain → new_domain)")
    print()
    print("| Von | Auf | Count |")
    print("|---|---|---|")
    for (old, new), entries in sorted(by_from_to.items(), key=lambda x: -len(x[1])):
        print(f"| {old} | {new} | {len(entries)} |")
    print()

    print("## Details pro Kipper")
    print()
    for (old, new), entries in sorted(by_from_to.items()):
        print(f"### {old} → {new}  ({len(entries)} mails)")
        print()
        print("| id | sender | subject | new_markers |")
        print("|---|---|---|---|")
        for e in entries:
            sender_short = e["sender"][:40]
            subj_short = (e["subject"] or "").replace("|", "/")[:50]
            markers_str = ", ".join(e["new_markers"][:3])
            print(f"| {e['id']} | {sender_short} | {subj_short} | {markers_str} |")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
