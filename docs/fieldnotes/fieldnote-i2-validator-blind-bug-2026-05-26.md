# Field-Note — I2: Validator blind klassifiziert auf body_hash (2026-05-26)

## Symptom

Der Validator-LLM (`gemma-4-26b-it-mlx`) bekam für jede Mail im Prompt-Block
`Body (erste 1000 Zeichen):` einen **SHA-256-Hexstring** anstelle des
Mailtextes. Folge: alle bisherigen 94 Einträge in `folio.db.validator_opinions`
sind auf Basis von sender+subject+plugin-hint entstanden, ohne dass das LLM
den Body je gelesen hätte — das `reasoning`-Feld ist halluziniert.

## Ursache

`scripts/validator_batch.py:213` (vor Fix):
```python
body_excerpt = (row.get("body_hash") or "")[:1000]
```

`body_hash` ist die SHA-256-Spalte aus `feedback.db.feedback` —
`production_worker.py:660`:
```python
body_hash = hashlib.sha256((env.body_text or "")[:5000].encode("utf-8")).hexdigest()
```

`env.body_text` existiert zur Worker-Laufzeit, wird in den Hash gefüttert,
an Hermes-Plugin und `classify_immo()` weitergereicht — aber **nirgends in
der DB persistiert**. Schema `body_hash TEXT NOT NULL` ohne Body-Spalte.

Der Validator hatte damit keine Body-Quelle in der DB. Der Code-Fehler — die
Variable `body_excerpt` aus `body_hash` zu beziehen — wurde vom Wortlaut des
Prompt-Platzhalters `{body}` semantisch maskiert: der Code sah aus, als
würde er den Body lesen.

## Recovery-Pfad-Analyse (vor Fix)

| Quelle | Verfügbar? | Genutzt? |
|---|---|---|
| `feedback.body_text` | – | existiert nicht |
| `feedback.body_excerpt` | – | existiert nicht |
| Hermes-Kanban-Task-Body | nein — Tasks werden nach Verarbeitung gc'd (Test: `hermes kanban show t_898e8582` → `no such task`) | – |
| IMAP-Re-fetch | nur solange Mail noch in INBOX | – |

## Fix — Forward-only Schema-Migration

1. `scripts/migrate_feedback_add_body_excerpt.py` — fügt `body_excerpt TEXT`
   zur `feedback`-Tabelle hinzu (idempotent, NULL-tolerant für alte Rows).
2. `production_worker.py` — schreibt `(env.body_text or "")[:1000]` in die
   neue Spalte zusätzlich zum unveränderten `body_hash`. INSERT-Statement
   um 24. Spalte erweitert.
3. `validator_batch.py:213` — liest jetzt `row.body_excerpt`. Legacy-Rows
   (pre-migration, `body_excerpt IS NULL`) fallen auf den expliziten Marker
   `[body unavailable for legacy row — pre-i2-migration entry]` zurück,
   damit sofort im Prompt-Log sichtbar ist, wenn der Validator auf eine
   nicht-rekonstruierbare alte Row trifft.

Kein rückwirkender IMAP-Re-fetch der ~50 korrigierten Mails (User-Entscheidung):
einfacher Forward-Fix, alte Opinions bleiben für Vorher-Nachher-Vergleich.

## Snapshot der 94 alten Opinions

`folio.db.validator_opinions__pre_i2_fix_20260526Z` ist eine read-only
Kopie des Standes pre-Fix. Vergleich zukünftig via JOIN auf `feedback_id`.

## Aufruf-Hinweis für künftige Validator-Runs (wichtig)

`--scope disagreements` (Default) trifft auch die 49 alten korrigierten Mails,
weil ihre `user_final_action ≠ heuristic_suggested_action`. Ein Re-Run würde
deren Opinions in der Haupt-Tabelle via UPSERT überschreiben — das ist nicht
gewünscht. Daher:

- ✅ `python3 validator_batch.py --scope last-tranche` — nur neueste Worker-Tranche
- ✅ `python3 validator_batch.py --scope unreviewed --limit N` — Mails ohne `review_state`
- ⚠️ `python3 validator_batch.py --scope disagreements` — würde alte Opinions ersetzen, bei Bedarf bewusst zusätzlich `--limit` setzen oder das Verhalten in Kauf nehmen

Snapshot bleibt unabhängig erhalten — selbst wenn die Haupt-Tabelle
überschrieben wird, ist die Pre-i2-fix-Version in
`validator_opinions__pre_i2_fix_20260526Z` weiter abrufbar.

## Quellen

- Direktive: `~/Projects/direktive-bugfix-i2-cleanup.md`
- Vorab-Befund: `regelwerk-ist-stand-2026-05-25.md` Inkonsistenz I2
- Backup pre-Migration: `~/Projects/backups/pre-i2-migration-2026-05-26/`
- Branch: `fix/i2-validator-body-2026-05-26`
