#!/usr/bin/env python3
"""audit_subject_wordboundary_reclass.py — Phase A.1 Re-Klassifikations-Report.

Loopt alle feedback-rows, klassifiziert mit der NEUEN (post-fix) Word-Boundary-
Logik in `classify_domain_actionability()`, und vergleicht das Ergebnis zur in
der DB gespeicherten domain. Wo's kippt → Tabelle.

Read-only: KEINE DB-Schreiben. Vorlage für Architekt-Go vor Bestandskorrektur
(A.2 macht das Schreiben).

Run:
    .venv/bin/python3 scripts/audit_subject_wordboundary_reclass.py
"""
from __future__ import annotations

import sqlite3
import sys
from collections import Counter, defaultdict
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
    flip_marker_categories: Counter = Counter()
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
            relevant_markers = [
                m for m in result.matched_markers
                if (":subject" in m or m.startswith("paketzustellung"))
            ]
            entry = {
                "id": r["id"],
                "sender": r["sender"],
                "subject": (r["subject"] or "")[:60],
                "old_domain": r["domain"],
                "new_domain": result.domain,
                "new_markers": result.matched_markers,
                "new_reason": result.reason,
            }
            flips.append(entry)
            by_from_to[(r["domain"], result.domain)].append(entry)
            for m in relevant_markers:
                flip_marker_categories[m.split(":", 1)[0]] += 1

    print("=" * 80)
    print("PHASE A.1 — Re-Klassifikations-Report Word-Boundary-Fix")
    print(f"DB: {DB_PATH}")
    print(f"Total rows: {len(rows)}")
    print(f"Flips (old → new): {len(flips)}")
    print("=" * 80)
    print()

    if not flips:
        print("KEINE Kipper — alle 179 Mails klassifizieren mit neuer Logik identisch.")
        print("→ Bug-Symptom id=375 muss separat verifiziert werden.")
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
        print("| id | sender | subject | new_markers (subject-relevant) |")
        print("|---|---|---|---|")
        for e in entries:
            relevant = [
                m for m in e["new_markers"]
                if ":subject" in m or "paketzustellung" in m
            ]
            sender_short = e["sender"][:35]
            subj_short = (e["subject"] or "").replace("|", "/")[:50]
            markers_str = ", ".join(relevant) or "(keine subject-marker)"
            print(f"| {e['id']} | {sender_short} | {subj_short} | {markers_str} |")
        print()

    print("## Marker-Klassen unter Kippern")
    print()
    for marker_cat, cnt in flip_marker_categories.most_common():
        print(f"- {marker_cat}: {cnt}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
