# Field-Note — Heuristik Subject-Keyword Word-Boundary-Fix (2026-05-27)

**Direktive:** `direktive-heuristik-job-substring-fix-2026-05-26.md` (Option B + Verschärfung-1 + Verschärfung-2)
**Branch:** `feature/heuristik-subject-wordboundary-2026-05-27`
**Scope:** `scripts/domain_actionability.py` — 4 Subject-Keyword-Listen + Helper + 6 Aufrufstellen

## Symptom

- id=375 `order-update@amazon.de` „Wir haben versucht, Ihr Amazon-Paket zuzustellen." → klassifiziert als `domain=job`
- Erwartet: `domain=shopping` (paketzustellung)

## Diagnose-Recap

Pre-Fix-Code in `_detect_domain`:
```python
if any(k in subj_lower for k in JOB_SUBJECT_KEYWORDS):
    markers.append("job:subject")
    return ("job", markers)
```

`JOB_SUBJECT_KEYWORDS = ("bewerbung", "karriere", "vacancy", "job alert", "stelle")` — substring-match `"stelle" in "...zuzustellen..."` = `True` → mail wird vor `PAKETZUSTELLUNG_KEYWORDS`-Check als job klassifiziert.

Klasse des Bugs: **Substring ohne Wortgrenze**. Identisches Anti-Pattern wie der Lens-Swap-Bug vom 2026-05-26 (`"qwen3-30b" in "qwen3-30b-thinking"` → falscher Model-Match).

## Fix-Mechanik

### Helper `_subject_matches_any()`
```python
def _subject_matches_any(subject_lower: str, keywords: tuple[str, ...]) -> str | None:
    for kw in keywords:
        if re.search(rf"\b{re.escape(kw)}\b", subject_lower):
            return kw
    return None
```

Strict word-boundary statt `in`-Substring. Multi-word-Keywords (`"job alert"`) funktionieren weil das innere Space selbst Wortgrenze ist.

### Erweiterte Listen (Verschärfung-1)

Plural + wichtige Komposita explicit gelistet — sonst würde echter Treffer wie „Stellenangebot" durch strict-match verloren gehen:

- `JOB_SUBJECT_KEYWORDS`: + bewerbungen, karrieren, vacancies, job alerts, stellen, stellenangebot, stellenangebote, stellenanzeige, stellenanzeigen, stellenausschreibung
- `FINANCE_SUBJECT_KEYWORDS`: + rechnungen, quittungen, steuern, steuererklärung, steuerberatung, versicherungen, abonnements, invoices
- `WERBUNG_SUBJECT_KEYWORDS`: + newsletters, sales, rabatte, aktionen, angebote, promos
- `PAKETZUSTELLUNG_KEYWORDS`: + pakete, zustellungen, lieferungen, lieferbestätigung, versandbestätigung, versandbenachrichtigung, sendungen, sendungsverfolgung
- `IMMO_SUBJECT_KEYWORDS`: + immobilien (Plural; Pendant zu „immobilie")

### Verschärfung-2 — bewusst KEIN `\w*`-Suffix

Architekt-Entscheidung: kein `\bsteuer\w*\b`, weil das `Steuerberatung` (gewollt) UND `Steuerung` (falsch) beide fängt. Stattdessen jedes spezifische Keyword einzeln gelistet. Test deckt das ab:

| Subject | Keyword-Liste | Match? |
|---|---|---|
| `Steuerung der neuen Geräte` | FINANCE (`steuer`, `steuern`) | ✗ (korrekt nicht) |
| `Steuerberatung — Beratungstermin` | FINANCE (`steuerberatung`) | ✓ |
| `Berechnung der Beiträge` | FINANCE (`rechnung`) | ✗ |
| `Wichtige Interaktion` | WERBUNG (`aktion`) | ✗ |
| `Salesforce-Update` | WERBUNG (`sale`) | ✗ |

## Test

`scripts/test_subject_keyword_boundary.py` — 31 Asserts grün:
- 10 „DARF NICHT matchen" (Substring-Trap + Suffix-Falle aus 3 Domains)
- 20 „MUSS matchen" (echte Treffer + Plural + Komposita)
- 1 End-to-End: id=375 klassifiziert jetzt als `domain=shopping`

## Bestandskorrektur (Verschärfung-1)

`scripts/audit_subject_wordboundary_reclass.py` (read-only) → 3 Kipper:

| id | Subject | Von | Auf | Klasse |
|---|---|---|---|---|
| 15 | Amazon-Paket zuzustellen | job | shopping | Bug-Fix-Win |
| 375 | Amazon-Paket zuzustellen | job | shopping | Bug-Fix-Win |
| 81 | Steam-Angebot | unsorted | werbung | Pre-F.8.5 Re-Classify-Win |

`scripts/migrate_subject_wordboundary_reclass_2026-05-27.py --apply` (Architekt-Go Option B = alle 3) executed. Idempotent verifiziert. Backup: `state/feedback.db.pre-wordboundary-2026-05-27.bak`.

## Wiederkehrendes Muster — Substring ohne Wortgrenze

Drei Auftreten in Folge:

1. **2026-05-25 Lens-Swap** — `lm_studio_swap("qwen3-30b", "qwen3-30b-thinking")`: substring-match in model-listing matched „qwen3-30b-thinking" als „qwen3-30b". → Fix: exakt-Match auf model_id.
2. **2026-05-27 Subject-Keywords** (dieser Fix) — `"stelle" in "zuzustellen"`. → Fix: word-boundary `\b…\b`.
3. **2026-05-27 Sender-Prefix** (anticipated, Folge-Direktive) — `p in prefix` matched `"info"` in `"linkedin-info"`. → Wird in eigenem Branch `feature/heuristik-senderprefix-2026-05-27` adressiert.

**Lesson für Future-Self:** vor jedem `x in y`-Substring-Check bewusst fragen, ob das tokens-mit-Trennzeichen-Domäne ist. Wenn ja: word-boundary, exact-match oder segment-split, NICHT `in`.

## Out of Scope

- **JOB_DOMAINS-Substring-Check** (`d in domain` Z. 256 für system-domains) — weiterer potentieller Substring-Trap-Kandidat. Heutiger Test-Korpus zeigt keinen Kipper-Effekt, aber bei künftiger Erweiterung der SYSTEM_DOMAINS-Liste prüfen.
- **Sender-Prefix-Trap** — separate Folge-Direktive, Branch `feature/heuristik-senderprefix-2026-05-27`.
