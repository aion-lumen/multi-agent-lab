# Field-Note — Heuristik Sender-Prefix Strict-Match (2026-05-27)

**Direktive:** `direktive-sender-prefix-fix.md`
**Branch:** `feature/heuristik-senderprefix-2026-05-27`
**Scope:** `scripts/domain_actionability.py` — Helper `_prefix_matches_any` + 2 Aufrufstellen (WERBUNG + BULK)

## Symptom-Klasse (latent)

Pre-Fix-Code an zwei Stellen in `_detect_domain`:
```python
if any(prefix.startswith(p) or p in prefix for p in WERBUNG_SENDER_PREFIXES):
    ...
is_bulk = any(prefix.startswith(p) or p in prefix for p in BULK_SENDER_PREFIXES)
```

`p in prefix` matched jeden Substring überall im prefix. Beispiel-Trap-Sender:

| Sender | OLD-Klassifikation | Begründung |
|---|---|---|
| `linkedin-info@…` | bulk → unsorted (falsch) | `"info" in "linkedin-info"` |
| `teamleader@…` | bulk → unsorted (falsch) | `"team" in "teamleader"` |
| `businessnews@…` | bulk → unsorted (falsch) | `"news" in "businessnews"` |
| `subservice@…` | bulk → unsorted (falsch) | `"service" in "subservice"` |
| `newsfeed@…` | bulk → unsorted (falsch) | `prefix.startswith("news")` |

**Latent-Status:** Im aktuellen 179-row-Korpus (`state/feedback.db` per 2026-05-27) ist **kein** Sender der Trap-Klasse vorhanden — der Bug war Code-only, keine Daten betroffen. Re-Klassifikations-Audit (`scripts/audit_senderprefix_reclass.py`): **0 Kipper**, Bestandskorrektur übersprungen.

## Fix-Mechanik

### Helper `_prefix_matches_any()` (3-stufig)

```python
def _prefix_matches_any(prefix: str, tokens: tuple[str, ...]) -> str | None:
    if not prefix:
        return None
    for tok in tokens:
        if prefix == tok:
            return tok
        if (prefix.startswith(tok + "-")
            or prefix.startswith(tok + ".")
            or prefix.startswith(tok + "_")):
            return tok
        if len(tok) >= 5:
            segments = re.split(r"[-._]", prefix)
            if tok in segments:
                return tok
    return None
```

1. **Exact-Match**: `prefix == token` (z.B. `noreply@` matched `noreply`)
2. **Strict-Startswith mit Trennzeichen**: `prefix.startswith(token + "-" | "." | "_")` (z.B. `noreply-system@`)
3. **Segment-Match nur für Tokens ≥ 5 Zeichen**: `re.split([-._])`, `token in segments` (z.B. `acme-newsletter@`)

### Begründung der 5-Zeichen-Schwelle

Kurze Tokens (`info`, `team`, `news` = 4 chars) als Segments wären zu breit:
- `linkedin-info@` würde fälschlich gegen Token `"info"` matchen (`segments = ["linkedin", "info"]`, `"info" in segments` → True)
- Aber `linkedin-info@` ist semantisch ein Brand-Sender, kein Bulk-Mailer.

Lange Tokens (`newsletter` 10, `marketing` 9, `notifications` 13) sind spezifisch genug für Segment-Match — `acme-newsletter@` ist legitim als Werbung-Sender erkennbar.

**5-Zeichen-Schwelle ist tunable** — bei künftigen Token-Erweiterungen prüfen ob die Länge passt. Token von 5+ chars sollte als Wort im Sender selbsterklärend sein; alles kürzere kann mehrdeutig als Substring auftauchen.

## Test

`scripts/test_sender_prefix_match.py` — 28 Asserts grün:
- 6 „DARF NICHT matchen" (linkedin-info, teamleader, businessnews, subservice, myteam, newsfeed)
- 19 „MUSS matchen" (exact + startswith+delimiter + segment≥5 für BULK + WERBUNG)
- 3 End-to-End via `classify_domain_actionability()`:
  - `linkedin-info@example.com` → `domain=kontakt` (war: bulk → unsorted, vor Fix)
  - `noreply@somecorp.example` → `domain=unsorted` (Sanity, unverändert)
  - `acme-newsletter@brand.example` → `domain=werbung` (Sanity, segment-match ≥ 5)

Plus Regression: `scripts/test_subject_keyword_boundary.py` 31/31 weiterhin grün.

## Wiederkehrendes Muster — Substring ohne Wortgrenze

**3. Auftreten in 3 Tagen:**

1. **2026-05-25 Lens-Swap** — `"qwen3-30b" in "qwen3-30b-thinking"` matched falsches Model. → Fix: exakt-Match auf model_id.
2. **2026-05-27 Subject-Keywords** — `"stelle" in "zuzustellen"`. → Fix: word-boundary `\b…\b` (Field-Note `fieldnote-heuristik-subject-wordboundary-2026-05-27.md`).
3. **2026-05-27 Sender-Prefix** (dieser Fix) — `"info" in "linkedin-info"`. → Fix: 3-stufige strict-Match-Mechanik.

**Lesson für Future-Self (verschärft):** Jeder `x in y`-Substring-Check in Token-Domänen ist verdächtig. Vor neuem Code immer fragen:
- Sind die Token Wort-Tokens mit Trennzeichen-Grenzen? → word-boundary / split-and-equals
- Sind die Token Sender-Prefixe mit `-._` als Delimiter? → strict-startswith-mit-Delimiter / segment-match (Längen-Schwelle)
- Sind die Token Model-IDs? → exact-equals

Niemals nackt `in` für tokenisierte Daten.

## Out of Scope

- **JOB_DOMAINS-Substring-Check** (`d in domain` Z. 256 für system-domains) — vierter potentieller Substring-Trap-Kandidat. Bei künftiger SYSTEM_DOMAINS-Erweiterung prüfen.
- **Bestandskorrektur** — 0 Kipper, kein Migration-Script.
