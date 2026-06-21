# Field-Note — Bauteil 15: Ingest-Konvergenz (2026-06-10)

**Quelle:** Architektur-Schnitt Direktive 15.

---

## Was

1. **`council/src/stammdaten_extract.py`**
   - Registry `PORTAL_BODY_PARSERS`: Comparis + ImmoScout-Varianten.
   - `FETCH_ONLY_PORTAL_IDS` (Homegate, Immowelt) — kein Body-Parse-Fall-through.
   - `BodyParseResult` mit `auto_reply: bool` statt Magic-String.
   - `normalize_inserat()` — einheitliches Inserat-Dict.

2. **`ingest_from_mail.py::_ingest_inserat`**
   - Gemeinsame Post-Parse-Pipeline: validate → upsert → cluster → image-cache.
   - Body-Parser- und Fetch-Pfad rufen dieselbe Funktion.

---

## Golden-Run

Erwartung: identische Marker, ACKs und Object-Rows vorher/nachher für
Comparis-Body, ImmoScout-Body und Fetch-Pfad (reine Konvergenz, kein
Verhaltens-Change beabsichtigt). Diff: keine semantischen Änderungen —
Duplikat-Blöcke kollabiert.
