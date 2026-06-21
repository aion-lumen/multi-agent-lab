# Field-Note — tier1-Blocker projektiert + zwangsversteigerung (2026-06-05)

**Repo:** `~/Projects/aion-lumen/multi-agent/`
**Branch:** `feature/heuristik-marker-erweiterung-2026-06-05` · 1 Commit.
**Direktive:** `~/Projects/direktive-filter-und-ui-iteration-2026-06-04.md` §1.2 + §1.3.

## Anlass

Mail-Welle hat 30 Mails geliefert. Drei davon sind „Bau mit Haus
Beispiel"-Anbieter-Spam (Neubau-Projekte ohne fertiges Inserat). Plus
ein Zwangsversteigerung-Title (Bonus-Befund aus dem Title-Parser-Bauteil).
Beide Kategorien verstopfen den Council, ohne dass User je darauf
reagiert hätte.

Filter analog zum 2026-05-28 PLZ-Country-Filter: Marker in
`immo_heuristic.py`, Override in `domain_actionability.py` Step 8.

## Was gebaut

### `scripts/immo_heuristic.py`

Zwei neue Compile-Konstanten (nach `PRICE_THRESHOLD`):

```python
_PROJEKTIERT_RE = re.compile(
    r"\b(?:neubau[- ]?projekt|projektiert|bau\s+mit\s+haus|ausbaustufe|"
    r"town\s*&\s*country|massa[- ]?haus|wird\s+errichtet|in\s+planung)\b",
    re.IGNORECASE,
)

_ZWANGSVERSTEIGERUNG_RE = re.compile(
    r"\b(?:zwangsversteigerung(?:stermin|en)?|amtsgericht|"
    r"versteigerung(?:stermin)?|gerichtsvollzieher|notverkauf)\b",
    re.IGNORECASE,
)
```

Match-Logik in `classify_immo` nach `haystack`-Zeile (~Zeile 720):

```python
if _PROJEKTIERT_RE.search(haystack):
    markers.append("tier1:projektiert:true")
if _ZWANGSVERSTEIGERUNG_RE.search(haystack):
    markers.append("tier1:zwangsversteigerung:true")
```

Reine Marker-Schreibung — Override-Entscheidung in domain_actionability.

### `scripts/domain_actionability.py`

Neue Funktion `_apply_tier1_blocker_filter` (nach `_apply_plz_country_filter`):
- Prüft `heuristic_markers` auf `tier1:projektiert:true` /
  `tier1:zwangsversteigerung:true`.
- Bei Treffer: actionability → `'archive-silent'`, schreibt
  `blocked_by:projektiert:true` / `blocked_by:zwangsversteigerung:true`
  in `matched_markers` für Audit-Trail.

In `classify_domain_actionability` nach Step 7 (PLZ-Country) eingehängt
als Step 8.

`TIER1_BLOCKER_MARKERS = ("tier1:projektiert:true", "tier1:zwangsversteigerung:true")`
ist tuple-konstant — Erweiterung um neue tier1-Blocker später trivial.

## Verifikation

### Standalone-Smoke

`python scripts/domain_actionability.py` →
```
=== tier1-Blocker-Filter (2026-06-05 projektiert/zwangsversteigerung) ===
  ✓ Projektiert            → archive-silent     expected=archive-silent
  ✓ Zwangsversteigerung    → archive-silent     expected=archive-silent
  ✓ Regulär CH (kein Block) → actionable         expected=actionable
```

Existing tests (PLZ-Country + 7 Default-Cases) bleiben grün.

### Re-Klassifikation 30 Live-Mails

```
Mit tier1:projektiert-Marker:      3
Mit tier1:zwangsversteigerung:    0
Newly blocked durch Step 8:        3
```

Drei Treffer alle aus `angebot@suchen.immowelt.de` „alternative
Angebote"-Mails. Body enthält wiederholt `"Ausbaustufe Bau mit Haus
Beispiel = Für Kunden die..."` — Anbieter-Sprache klar als Pattern
erkannt.

False-Positive-Risiko bei aktuellem Sample: 0/30 (manuelle Stichprobe
der 3 Treffer bestätigt: alle 3 sind Bau-mit-Haus-Anbieter, keine
echten Hauskauf-Inserate).

## Out of Scope

- **`Amtsgericht` Regex-Edge-Case:** könnte theoretisch in Straßennamen
  („Am Amtsgericht 5") false-positive triggern. Aktuell 0 Mails
  betroffen. Wenn nach 1 Woche false-positives auftauchen → Regex
  schärfen mit Lookahead `(?!\s+\d+)` für „Amtsgericht".
- **Wort-Variation `Town & Country`:** der `&` wird mit `&amp;`
  HTML-encoded in Mail-Body. Aktueller Regex matched literal `&`.
  Falls Treffer fehlen → Body-Decode oder regex anpassen.
- **`Bestand`-Keyword (Direktive-Vorschlag):** absichtlich nicht
  aufgenommen — false-positive-Risiko hoch („Bestandsobjekt" ist
  legitimer Kaufgegenstand).
- **Ingest-Filter im council** (Direktive empfohlene Sicherheitsnetz):
  separater Branch B2 in council-Repo.

## Folge-Schritte

1. FF-Merge auf Anweisung, Push.
2. Nach nächster Mail-Welle: Trefferquote beobachten via SQL gegen
   `feedback.heuristic_markers LIKE '%tier1:projektiert%'`.
3. Bei false-positives Regex-Update als Mini-Commit.
