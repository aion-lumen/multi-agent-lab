# Cleanup-Befund — A6: Council + Folio cross-DB TRUNCATE (Bauteil 8)

**Datum:** 2026-06-09 15:15 UTC. **Variante A — Architekt-Tendenz.**

## Pre-Snapshot

```
council.db (Pre):
  objects:                       20
  rankings:                      62
  lens_comparisons:              12
  consolidated_top10:            30
  object_lifecycle_events:       20
  mail_ingest_acks:              10
  mail_inserat_markers:          12
  council_runs:                  14
  council_run_logs:               5
  council_run_summary:            3
  object_price_history:           1
  (ingest_acks + user_actions waren schon 0)

folio.db cross-DB (Pre):
  object_status_override:        31
  object_views:                  16
  object_triggers:                0
  object_notes:                   6
  user_rankings:                  4
  hauskauf_workflow:              0
  pending_ingest:                 0
```

**Dump-Backups:** `/tmp/bauteil8-cleanup-backup/{council,folio}-pre-bauteil8.sql`
(53 KB + 192 KB).

## TRUNCATE-Operation

```sql
-- council.db
PRAGMA foreign_keys = OFF;
DELETE FROM object_price_history;
DELETE FROM object_lifecycle_events;
DELETE FROM mail_inserat_markers;
DELETE FROM mail_ingest_acks;          -- Bauteil-7-Mails werden neu verarbeitet
DELETE FROM ingest_acks;
DELETE FROM consolidated_top10;
DELETE FROM lens_comparisons;
DELETE FROM user_actions;
DELETE FROM rankings;
DELETE FROM objects;
DELETE FROM council_run_summary;
DELETE FROM council_run_logs;
DELETE FROM council_runs;
PRAGMA foreign_keys = ON;
VACUUM;

-- folio.db cross-DB
DELETE FROM object_status_override;
DELETE FROM object_views;
DELETE FROM object_triggers;
DELETE FROM object_notes;
DELETE FROM user_rankings;
DELETE FROM hauskauf_workflow;
DELETE FROM pending_ingest;
VACUUM;
```

## Post-Cleanup

Alle 13 council.db-Tabellen + 7 folio.db cross-DB-Tabellen auf **0**.
`corrections` (User-Lern-Anker) **unverändert: 46** ✓.

## Engineer-Bewertung

Variante A übernommen — Architekt-Tendenz wörtlich. User-Bewertungen
(31 `object_status_override`-Einträge) waren überwiegend auf Pseudo-
Objekten (Ratgeber-Karten, Comparis-Werbung), die durch das fehlende
Stammdaten-Kriterium in Council gelandet waren. Der Lern-Anker
„verworfen weil Werbung" ist die Eigenschaft eines Klassifikations-
Bugs, kein echtes User-Inserat-Verwerfen.

`mail_ingest_acks` wurde explizit mit-truncated, damit die Bauteil-7-
Mails (id 795–884) beim nächsten Council-Ingest-Tick neu durch
A1+A2+A3-Filter laufen können.

Wichtig: `feedback.db` (multi-agent) bleibt komplett erhalten — keine
Mail-Substanz verloren. Beim nächsten production_worker-Run werden
nur NEUE IMAP-Mails klassifiziert; die existierenden 150 Mails bleiben
mit altem Klassifikations-Stand sichtbar bis sie via Re-Run oder
manueller Override aktualisiert werden.
