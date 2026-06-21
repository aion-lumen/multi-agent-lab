# Cross-DB-Write-Ausnahmen

**Stand:** 2026-06-10 · Bauteil 14: Council→folio Cluster-Inheritance entfernt (Read-through in Folio).

## Grundregel (Ownership)

Jede DB hat einen Owner-Repo, der schreibt. Andere Repos lesen
read-only via `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`.

| DB | Owner | Schreibt |
|---|---|---|
| `~/Projects/aion-lumen/multi-agent/state/feedback.db` | `multi-agent` | production_worker, post-classification |
| `~/.folio/folio.db` | `folio` (SvelteKit) | manager.ts (UI-Spawn), Endpoints, Reader-Loader |
| `~/.council/council.db` | `council` | ingest_from_mail, council_lens_run, council_borda |

## Etablierte Ausnahmen (mail-side `multi-agent` → `folio.db`)

Die hier gelisteten Schreibpunkte verletzen die Grundregel bewusst.
**Jede neue Ausnahme braucht expliziten Architekt-Entscheid + Eintrag
in dieser Liste** — sonst gilt die Grundregel.

| Ziel-Tabelle (folio.db) | Schreiber (multi-agent) | Etabliert | Begründung |
|---|---|---|---|
| `validator_opinions` | `scripts/validator_batch.py::write_opinion` | 2026-05-26 (Direktive F.8 Block-E) | Validator-Pipeline schreibt direkt — kein UI-Listener-Pattern damals etabliert. UPSERT via `UNIQUE(feedback_id, validator_model)`. |
| `worker_run_logs` | `scripts/folio_log_writer.py::write_log`, aufgerufen aus `production_worker.py` / `validator_batch.py` / `auto_uebernahme.py` | 2026-06-07 (Pre-Bauteil Pipeline-Persistenz) | Per-Mail-Logs für Pipeline-UI. manager.ts könnte alternativ stdout-parsen, aber Hybrid-Pattern erweitert die existing `validator_opinions`-Ausnahme statt einen neuen Mechanismus zu bauen. |
| `worker_run_summary` | `scripts/folio_log_writer.py::write_summary` | 2026-06-07 (Pre-Bauteil) | Run-Ende-Aggregat. INSERT OR REPLACE per `run_uuid` PK. |
| `worker_runs` | `folio/worker-runner/manager.ts` (UI-spawn, primär) | etabliert | **Keine multi-agent-Ausnahme** — schreibt ausschließlich Folio selbst (TypeScript) beim UI-Worker-Spawn. Python-Worker kriegen die `run_uuid` als CLI-arg + env-fallback `FOLIO_RUN_UUID`. |
| `corrections` | `scripts/imap_cleanup.py::_write_correction_snapshot` | 2026-06-09 (Bauteil 6 IMAP-Aufräum) | Snapshot der heuristic_markers + Reason vor IMAP-Trash-Move. Erweitert das `folio_log_writer`-Pattern um cleanup-Use-Case. INSERT-only (Append-only), `feedback_id`-FK ohne SQL-Constraint. `heuristic_markers_snapshot`-Spalte (Bauteil 6) friert Marker-Stand ein, überlebt feedback.db-Cleanup. |

**Run-uuid-Propagation (manager.ts → Python):**
- CLI-arg `--run-uuid <uuid>` (primär).
- env-fallback `FOLIO_RUN_UUID` (für Wrapper-Skripte oder env-only-Umgebungen).
- Resolver in `multi-agent/scripts/folio_log_writer.py::get_run_uuid_from_env_or_args`.
- None bei CLI-Direkt-Aufruf (ohne UI-Spawn) → alle Logger-Calls
  sind defensiv no-op, keine Exception.

## Council-side: lokale Schreibungen

`council/scripts/ingest_from_mail.py` und `council_lens_run.py` laufen
launchd-isoliert. Schreiben **ausschließlich** in council.db:

| Lokale Tabelle (council.db) | Schreiber | Zweck |
|---|---|---|
| `council_runs` | `ingest_from_mail.py::main`, `council_lens_run.py::main` | Run-Metadaten (start/end/status/n_processed) |
| `council_run_logs` | `ingest_from_mail.py` (Body-Parser + Fetch-Pfad) | Per-Mail-Events (ingested / filtered_* / all_failed) |
| `council_run_summary` | `ingest_from_mail.py` | Run-Ende-Aggregat |

Folio liest cross-DB read-only via
`folio/src/lib/server/council-db/reader.ts`. Council liest folio.db
read-only für `mail_actionability_override` (ACK-Filter) — **kein**
Council→folio-Schreiben mehr seit Bauteil 14.

Cluster-Substanz (Status, Notes, Workflow) wird in Folio read-time
aufgelöst (`cluster-substance.ts`), nicht mehr per Ingest-Copy.

## Wann erweitern, wann nicht

**Erlaubt** sind Cross-DB-Writes nur für:
- mail-side `multi-agent` → `folio.db` (etablierter Hybrid-Pfad,
  oben gelistet).

**Verboten** sind:
- `council` → `folio.db` (Council schreibt ausschließlich council.db).
- `folio` → `feedback.db` oder `folio` → `council.db` (Folio liest
  cross-DB, schreibt nie).
- Jede neue Tabelle ohne Architekt-Entscheid + Eintrag oben.

## Begründung des Hybrid-Patterns

User-Bestätigung 2026-06-07 (Pre-Bauteil-Diagnose):
- Mail-side-Worker laufen überwiegend via UI-Spawn — manager.ts ist
  Listener und könnte stdout parsen. Wäre möglicher Sauberer-Pfad,
  aber komplex (Regex-Parser, CLI-Direkt-Aufruf liefert keine Logs).
- Die `validator_opinions`-Ausnahme rechtfertigt direkten Cross-DB-
  Write für die gleiche multi-agent-Familie. Erweitern bekannter
  Ausnahme < neuer Mechanismus.
- Launchd-Worker (council_*) können nicht über manager.ts geloggt
  werden (kein Listener). Lokal in council.db schreiben + Folio
  liest cross-DB ist sauber ownership-konform.

**Ergebnis:** 4 etablierte Cross-DB-Write-Ausnahmen (alle mail-side
multi-agent → folio.db). Liste sichtbar, nicht unbemerkt wachsend.
