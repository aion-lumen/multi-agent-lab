#!/usr/bin/env python3
"""
apply_diagnoses.py - Trag die 6 in Phase 3.5b-Update vereinbarten Diagnosen
in state/diagnose-mismatches-2026-05-10.csv ein.

Idempotent: prueft pro Zeile ob diagnose_kategorie schon gesetzt ist;
ueberschreibt nicht.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

CSV = (Path.home() / "Projects" / "aion-lumen" / "multi-agent"
       / "state" / "diagnose-mismatches-2026-05-10.csv")

DIAGNOSES = {
    "418997": {
        "kategorie": "user_inkonsistent",
        "notiz": "Validator klassifizierte korrekt als geschaeftspost (Yahoo App-Passwort-Notice). "
                 "User-Override unter Live-Druck setzte faelschlich werbung. "
                 "Initial-GT-Label war richtig.",
    },
    "418881": {
        "kategorie": "schema_konflikt",
        "notiz": "NGO-Spenden-Mail (RASA). GT-Wert newsletter_business existiert in Schema v2 nicht. "
                 "Unter Schema v2 waere werbung korrekt (Mass-Mailing von Organisation). "
                 "Validator hatte recht.",
    },
    "418537": {
        "kategorie": "heuristik_falsch",
        "notiz": "Andreas Heinze (Einzelunternehmer-Fotograf) ist natuerliche Person. "
                 "life-agents Pre-Klassifizierung markierte ihn faelschlich als service_senders "
                 "wegen Firmen-Domain. sender_learner-Kandidat.",
    },
    "418800": {
        "kategorie": "schema_konflikt",
        "notiz": "Selbe NGO wie 418881. Beide LLMs einig auf werbung - unter Schema v2 korrekt. "
                 "Mismatch ist Schema-Versions-Artefakt, kein echter Fehler.",
    },
    "419075": {
        "kategorie": "schema_konflikt",
        "notiz": "Makler-Antwort. User-Label 'privat' folgt persoenlicher Relevanz, "
                 "Schema v2 zaehlt Sender-Charakter (Firma=geschaeftspost). "
                 "LLMs schema-treu. Konflikt: Sender-Typ vs persoenliche Relevanz.",
    },
    "418578": {
        "kategorie": "heuristik_zu_breit",
        "notiz": "Polymarket-Sender ist mehrdeutig - Service + Marketing. "
                 "Heuristik klassifizierte alles als geschaeftspost. "
                 "Subject-Pattern ('smart money', Emoji, 'click') waeren Marketing-Indikatoren. "
                 "marketing_learner-Kandidat.",
    },
}


def main() -> None:
    if not CSV.exists():
        sys.exit(f"CSV nicht gefunden: {CSV}")

    with open(CSV) as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    applied = 0
    skipped = 0
    for row in rows:
        uid = (row.get("uid") or "").strip()
        if uid in DIAGNOSES:
            existing = (row.get("diagnose_kategorie") or "").strip()
            if existing:
                print(f"  uid={uid}: bereits gesetzt ({existing}) - skip")
                skipped += 1
                continue
            row["diagnose_kategorie"] = DIAGNOSES[uid]["kategorie"]
            row["diagnose_notiz"] = DIAGNOSES[uid]["notiz"]
            applied += 1
            print(f"  uid={uid}: {DIAGNOSES[uid]['kategorie']}")

    with open(CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print()
    print(f"Angewendet: {applied}")
    print(f"Bereits gesetzt: {skipped}")
    print(f"Datei: {CSV}")


if __name__ == "__main__":
    main()
