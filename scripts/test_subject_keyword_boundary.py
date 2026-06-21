#!/usr/bin/env python3
"""test_subject_keyword_boundary.py — Fallstrick-Test für Subject-Keyword-Match.

Direktive 2026-05-27 Job-Substring-Fix §4 + Architekt-Verschärfung-2.
Verifiziert dass:
  1. Der bestätigte Bug (id=375 „...Amazon-Paket zuzustellen" → fälschlich job)
     nicht mehr feuert.
  2. Die Substring-Trap-Klasse für alle drei betroffenen Domains (job, finance,
     werbung) eliminiert ist.
  3. Echte Treffer weiterhin matchen (Plural + Komposita aus den erweiterten
     Listen).
  4. End-to-end via `classify_domain_actionability()`: id=375 ist jetzt shopping.

Run:
    ~/Projects/aion-lumen/multi-agent/.venv/bin/python3 \\
        ~/Projects/aion-lumen/multi-agent/scripts/test_subject_keyword_boundary.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from domain_actionability import (
    _subject_matches_any,
    classify_domain_actionability,
    load_user_context,
    JOB_SUBJECT_KEYWORDS,
    FINANCE_SUBJECT_KEYWORDS,
    WERBUNG_SUBJECT_KEYWORDS,
    PAKETZUSTELLUNG_KEYWORDS,
    IMMO_SUBJECT_KEYWORDS,
)


# ---------------------------------------------------------------------------
# (1) DARF NICHT matchen — Substring-Trap + Suffix-Falle
# ---------------------------------------------------------------------------
DARF_NICHT_MATCHEN: list[tuple[str, tuple[str, ...], str]] = [
    # Job substring (id=375 + Klasse)
    ("Wir haben versucht, Ihr Amazon-Paket zuzustellen.", JOB_SUBJECT_KEYWORDS, "stelle in zustellen"),
    ("Ihre Bestellung wurde versandt",                    JOB_SUBJECT_KEYWORDS, "stelle in bestellen"),
    ("Neue Einstellungen verfügbar",                      JOB_SUBJECT_KEYWORDS, "stelle in einstellen"),
    ("Anstelle eines Termins schlage ich vor",            JOB_SUBJECT_KEYWORDS, "stelle in anstelle"),
    ("Bitte um Stellungnahme zur Diskussion",             JOB_SUBJECT_KEYWORDS, "stelle in stellungnahme"),

    # Finance suffix (Architekt-Verschärfung-2)
    ("Steuerung der neuen Geräte freigegeben",            FINANCE_SUBJECT_KEYWORDS, "steuer in steuerung"),
    ("Berechnung der Beiträge für Q3",                    FINANCE_SUBJECT_KEYWORDS, "rechnung in berechnung"),

    # Werbung suffix (Audit-Report)
    ("Salesforce-Update für Ihr Team",                    WERBUNG_SUBJECT_KEYWORDS, "sale in salesforce"),
    ("Wichtige Interaktion mit dem System",               WERBUNG_SUBJECT_KEYWORDS, "aktion in interaktion"),
    ("Reaktion auf Ihre Anfrage",                         WERBUNG_SUBJECT_KEYWORDS, "aktion in reaktion"),
]

# ---------------------------------------------------------------------------
# (2) MUSS matchen — echte Treffer + Plural + wichtige Komposita
# ---------------------------------------------------------------------------
MUSS_MATCHEN: list[tuple[str, tuple[str, ...], str]] = [
    # Job
    ("Neue Stelle als Product Manager",                   JOB_SUBJECT_KEYWORDS, "stelle"),
    ("5 neue Stellen, die zu deinem Profil passen",       JOB_SUBJECT_KEYWORDS, "stellen"),
    ("Stellenangebot in Basel",                           JOB_SUBJECT_KEYWORDS, "stellenangebot"),
    ("Bewerbung erfolgreich eingereicht",                 JOB_SUBJECT_KEYWORDS, "bewerbung"),
    ("Karriere-Coaching im Mai",                          JOB_SUBJECT_KEYWORDS, "karriere"),

    # Finance
    ("Ihre Rechnung Nr 2026-001 steht bereit",            FINANCE_SUBJECT_KEYWORDS, "rechnung"),
    ("Steuererklärung 2025 fällig",                       FINANCE_SUBJECT_KEYWORDS, "steuererklärung"),
    ("Steuern Q3 — Quittung anbei",                       FINANCE_SUBJECT_KEYWORDS, "steuern"),
    ("Steuerberatung — Beratungstermin",                  FINANCE_SUBJECT_KEYWORDS, "steuerberatung"),
    ("Ihre Versicherung Police 12345",                    FINANCE_SUBJECT_KEYWORDS, "versicherung"),

    # Werbung
    ("60% Sale auf alle Schuhe",                          WERBUNG_SUBJECT_KEYWORDS, "sale"),
    ("Sales-Update Mai",                                  WERBUNG_SUBJECT_KEYWORDS, "sales"),
    ("Neue Aktion: 3 für 2",                              WERBUNG_SUBJECT_KEYWORDS, "aktion"),
    ("Newsletter Mai 2025",                               WERBUNG_SUBJECT_KEYWORDS, "newsletter"),
    ("Rabatt-Code für Neukunden",                         WERBUNG_SUBJECT_KEYWORDS, "rabatt"),

    # Paketzustellung (bleibt unverändert, verifizieren)
    ("Ihr Paket wurde geliefert",                         PAKETZUSTELLUNG_KEYWORDS, "paket"),
    ("Versandbestätigung Ihrer Bestellung",               PAKETZUSTELLUNG_KEYWORDS, "versand"),
    ("Tracking-Update: in Zustellung",                    PAKETZUSTELLUNG_KEYWORDS, "tracking"),

    # Immo
    ("Neue Immobilien in Basel",                          IMMO_SUBJECT_KEYWORDS, "immobilien"),
    ("Haus kaufen Region Basel",                          IMMO_SUBJECT_KEYWORDS, "haus kaufen"),
]


def run_tests() -> int:
    fails = 0

    print("=" * 70)
    print("DARF NICHT matchen (Substring-Trap + Suffix-Falle)")
    print("=" * 70)
    for subject, keywords, what in DARF_NICHT_MATCHEN:
        subj_lower = subject.lower()
        hit = _subject_matches_any(subj_lower, keywords)
        if hit is None:
            print(f"  ✓ {what:35s} | {subject[:50]!r}")
        else:
            print(f"  ✗ FAIL {what:35s} | {subject!r} matched {hit!r}")
            fails += 1

    print()
    print("=" * 70)
    print("MUSS matchen (echte Treffer + Plural + Komposita)")
    print("=" * 70)
    for subject, keywords, expected_hit in MUSS_MATCHEN:
        subj_lower = subject.lower()
        hit = _subject_matches_any(subj_lower, keywords)
        if hit == expected_hit:
            print(f"  ✓ {hit:25s} | {subject[:50]!r}")
        elif hit is not None:
            # Andere Keyword aus derselben Liste matched — auch ok
            print(f"  ✓ {hit:25s} (statt {expected_hit!r}) | {subject[:50]!r}")
        else:
            print(f"  ✗ FAIL expected {expected_hit!r:20s} got None | {subject!r}")
            fails += 1

    print()
    print("=" * 70)
    print("END-TO-END: id=375 nach Fix (sollte shopping/paketzustellung sein)")
    print("=" * 70)
    ctx = load_user_context()
    r = classify_domain_actionability(
        sender="order-update@amazon.de",
        subject="Wir haben versucht, Ihr Amazon-Paket zuzustellen.",
        mail_date="2026-05-27T00:53:43+00:00",
        plugin_class="geschaeftspost",
        user_context=ctx,
    )
    print(f"  domain        = {r.domain}")
    print(f"  actionability = {r.actionability}")
    print(f"  reason        = {r.reason}")
    print(f"  markers       = {r.matched_markers}")
    if r.domain == "shopping":
        print(f"  ✓ id=375 ist jetzt korrekt shopping (war: job, vor Fix)")
    else:
        print(f"  ✗ FAIL id=375 erwartet shopping, bekommen {r.domain}")
        fails += 1

    print()
    if fails == 0:
        print(f"ALLE TESTS GRÜN ✓  ({len(DARF_NICHT_MATCHEN) + len(MUSS_MATCHEN) + 1} Asserts)")
        return 0
    else:
        print(f"FAILS: {fails}")
        return 1


if __name__ == "__main__":
    sys.exit(run_tests())
