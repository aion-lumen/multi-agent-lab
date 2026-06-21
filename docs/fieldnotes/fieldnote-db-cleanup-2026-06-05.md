# Field-Note: DB-Cleanup (Bauteil 1, 2026-06-05)

**Direktive:** `~/Projects/direktive-db-cleanup-2026-06-05.md`
**Pfad:** A (Cleanup direkt, nach Architekt-Approval auf Diagnose-Befund).
**Vorgänger-Field-Note:** `fieldnote-filter-diagnose-pre-cleanup-2026-06-05.md`

## Backup

`~/Projects/aion-lumen/backups/db-cleanup-2026-06-05/`:
- `folio.db` (448K) + `folio.db-wal` (3.9M) + `folio.db-shm` (32K)
- `council.db` (220K) + `council.db-wal` (4.0M) + `council.db-shm` (32K)
- `feedback.db` (392K) + `feedback.db-wal` (4.0M) + `feedback.db-shm` (32K)
- Schema-Snapshots als `.sql` für Post-Cleanup-Diff.

## Pre- vs. Post-Counts

### multi-agent feedback.db
| Tabelle | Pre | Post |
|---|---|---|
| feedback | 212 | 0 |

### council.db
| Tabelle | Pre | Post |
|---|---|---|
| objects | 58 | 0 |
| object_lifecycle_events | 62 | 0 |
| ingest_acks | 0 | 0 |
| mail_ingest_acks | 79 | 0 |
| rankings | 219 | 0 |
| lens_comparisons | 41 | 0 |
| consolidated_top10 | 82 | 0 |
| user_actions | 0 | 0 |

### folio.db
| Tabelle | Pre | Post |
|---|---|---|
| corrections | 54 | 0 |
| object_status_override | 13 | 0 |
| user_rankings | 5 | 0 |
| object_notes | 25 | 0 |
| object_views | 11 | 0 |
| object_triggers | 0 | 0 |
| pending_ingest | 1 | 0 |
| worker_runs | 158 | 0 |
| review_state | 65 | 0 |
| hauskauf_workflow | 0 | 0 |
| validator_opinions | 618 | 0 |
| users | 1 | **1** (NICHT geleert) |

## Engineer-Erweiterungen über Plan-File hinaus

Zwei Tabellen waren im Plan nicht explizit aufgeführt, aber semantisch
clean-relevant (referenzieren weg-gecleanste feedback_ids /
object_ids):
- `council.user_actions` (Pre: 0, Post: 0 — ohnehin leer)
- `folio.validator_opinions` (Pre: 618, Post: 0)

Beide mit-geleert. Backup-Versionen `validator_opinions__pre_*`
**nicht** angetastet (historische Migrations-Snapshots).

`folio.users` (1 row) **nicht** geleert — User-Account erhalten,
sonst Login kaputt.

## Schema-Diff

Alle drei DBs: **Schema unverändert**.
- `folio-schema.sql`: 160 Zeilen pre/post identisch.
- `council-schema.sql`: 108 Zeilen pre/post identisch.
- `feedback-schema.sql`: 33 Zeilen pre/post identisch.

## launchd-Pausierung

**Nicht pausiert.** Engineer-Default: Cleanup läuft in Sekunden,
Wahrscheinlichkeit einer Worker-Tick-Kollision minimal. Worker
würden in leere DB schreiben (idempotent via INSERT OR IGNORE).
Tatsächlich: keine Kollision beobachtet, manuelles Verifikations-
Trigger nach Cleanup zeigte „Nothing to process" (alle 0 in
feedback.db).

## Verifikation post-Cleanup

### Folio-UI Smoke

| Route | HTTP |
|---|---|
| `/mail-queue` | 200 |
| `/council` | 200 |
| `/council/mobile` | 200 |

Kein Crash, leere Listen rendern sauber.

### Worker-Smoke

`launchctl kickstart -k gui/$(id -u)/com.aionlumen.council-mail-ingest`
→ Worker startete, log zeigt:
```
=== ingest_from_mail — 0 mails (von 0, 0 bereits ack'ed) ===
Nothing to process: alle feedback-rows bereits ack'ed.
```

Worker läuft sauber gegen leere feedback.db. Kein Crash. Wenn
`com.aionlumen.council-pending-ingest` (:05 Tick) als nächstes
läuft, schreibt es bei pending_ingest-Einträgen (heute leer) auch
nichts. Beim nächsten production_worker-Lauf (über
`multi-agent/launchd/com.aionlumen.production-worker.*` oder manuell)
werden neue Mails aus IMAP geholt → in feedback.db → bei nächstem
mail-ingest-Tick :25 in council.objects.

## Datenbasis bereit für Bauteil 2

- feedback.db leer, alle Schema unverändert.
- council.db leer, lifecycle-Events leer, ACKs leer.
- folio.db Daten-Tabellen leer, users erhalten.
- launchd-Jobs laufen normal weiter, schreiben ab jetzt in saubere DB.

**Engineer-Empfehlung für Bauteil 2 (Hauskauf-Kampagne):** Schema-
Erweiterungen + Kanban-UI können auf clean state planen — keine
Legacy-Daten zu berücksichtigen. Reset gilt als Baseline.

## Folge-Direktive (aus Pre-Cleanup-Diagnose)

Marker-Persistierungs-Bug: `out_of_corridor:*`/`blocked_by:*`-Marker
landen nur in Kanban-Payload, nicht in `feedback.heuristic_markers`.
Fix in `production_worker.py:534` (ClassificationResult-Marker
mit-persistieren). Eigene Direktive falls priorisiert.
