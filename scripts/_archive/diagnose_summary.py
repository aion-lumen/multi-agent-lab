#!/usr/bin/env python3
"""
diagnose_summary.py - Aggregate user-edited diagnose categories from
state/diagnose-mismatches-2026-05-10.csv and emit a strategy recommendation
for Phase 3.5c.

Supports legacy categories plus new 'user_inkonsistent' from Phase 3.5b-Update.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

CSV = (Path.home() / "Projects" / "aion-lumen" / "multi-agent"
       / "state" / "diagnose-mismatches-2026-05-10.csv")


def main() -> None:
    if not CSV.exists():
        sys.exit(f"CSV not found: {CSV}")

    diag = Counter()
    by_pfad: Counter = Counter()
    notes: list[dict] = []
    total = 0
    matches = 0

    with open(CSV) as f:
        for row in csv.DictReader(f):
            total += 1
            if (row.get("match") or "").strip().lower() == "true":
                matches += 1
            kat = (row.get("diagnose_kategorie") or "").strip()
            if kat:
                diag[kat] += 1
                by_pfad[(row.get("pfad") or "unknown", kat)] += 1
            notiz = (row.get("diagnose_notiz") or "").strip()
            if notiz or kat:
                notes.append({
                    "uid": row.get("uid"),
                    "pfad": row.get("pfad"),
                    "kategorie": kat,
                    "notiz": notiz,
                    "subject": (row.get("subject") or "")[:60],
                })

    print("=" * 70)
    print("Diagnose-Auswertung")
    print("=" * 70)
    print(f"Total: {total} rows, matches={matches}, mismatches={total - matches}\n")

    print("Kategorien-Verteilung:")
    for kat, count in diag.most_common():
        print(f"  {kat:<22} {count}")
    print()

    print("Pro Pfad x Kategorie:")
    for (pfad, kat), count in sorted(by_pfad.items()):
        print(f"  {pfad:<16} {kat:<22} {count}")
    print()

    if notes:
        print("Notizen:")
        for n in notes:
            print(f"  [{n['kategorie']:<22}] uid={n['uid']:<8} {n['subject']}")
            if n["notiz"]:
                print(f"     -> {n['notiz'][:140]}")
        print()

    print("=" * 70)
    print("Strategie-Empfehlung fuer Phase 3.5c")
    print("=" * 70)

    heuristik = diag.get("heuristik_falsch", 0) + diag.get("heuristik_zu_breit", 0)
    executor = diag.get("executor_falsch", 0)
    validator = diag.get("validator_falsch", 0)
    beide_llm = diag.get("beide_llm_falsch", 0)
    schema = diag.get("schema_konflikt", 0)
    inkonsistent = diag.get("user_inkonsistent", 0)

    # Dominanz-Heuristik
    ranks = [
        ("schema_konflikt", schema),
        ("heuristik", heuristik),
        ("executor", executor),
        ("validator", validator),
        ("beide_llm", beide_llm),
        ("user_inkonsistent", inkonsistent),
    ]
    ranks.sort(key=lambda kv: -kv[1])
    top = ranks[0]

    print()
    if top[1] == 0:
        print("-> Keine Diagnose-Kategorien ausgefuellt - User-Aktion ausstehend.")
    elif top[0] == "schema_konflikt":
        print(f"-> Hauptproblem: SCHEMA-DEFINITION ({schema} Faelle)")
        print("   Schema v3 oder klare v2-Doku vor Phase 4.")
        if inkonsistent:
            print(f"   Plus: {inkonsistent} user_inkonsistent - Workflow-Problem unter Live-Druck")
            print("   -> Phase 5 Inline-Buttons priorisieren (reduziert Tippfehler).")
    elif top[0] == "heuristik":
        print(f"-> Hauptproblem: HEURISTIK ({heuristik} Faelle)")
        print("   Phase 3.5c sollte sender_learner + marketing_learner priorisieren.")
        print("   Modell-Rollen koennen unveraendert bleiben.")
    elif top[0] == "executor":
        print(f"-> Hauptproblem: EXECUTOR ({executor} Faelle)")
        print("   Phase 3.5c sollte Modell-Rollen-Tausch evaluieren:")
        print("   Gemma als Executor, gpt-oss als Validator.")
    elif top[0] == "validator":
        print(f"-> Hauptproblem: VALIDATOR ({validator} Faelle)")
        print("   Validator-Modell pruefen, ggf. austauschen.")
    elif top[0] == "beide_llm":
        print(f"-> Hauptproblem: BEIDE LLMs ({beide_llm} Faelle)")
        print("   Phase 3.5c sollte einen dritten Modell-Pass (Architect) erwaegen")
        print("   oder Schema-Definition schaerfen.")
    elif top[0] == "user_inkonsistent":
        print(f"-> Hauptproblem: USER-INKONSISTENZ ({inkonsistent} Faelle)")
        print("   Workflow unter Live-Druck. Phase 5 Inline-Buttons priorisieren.")

    if schema and heuristik:
        print()
        print(f"   Zweitwichtig: HEURISTIK ({heuristik} Faelle) -> sender_learner / marketing_learner")


if __name__ == "__main__":
    main()
