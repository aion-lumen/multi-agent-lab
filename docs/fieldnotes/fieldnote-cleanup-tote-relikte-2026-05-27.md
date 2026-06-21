# Field-Note — Cleanup tote Relikte (Validator-Disagreements + Board-Auto) — 2026-05-27

**Direktive:** `01-direktive-cleanup-tote-relikte.md`
**Branch:** `feature/cleanup-tote-relikte-2026-05-27` (folio + multi-agent gleichzeitig)
**Commits:** A (Validator-Scope `disagreements` raus), B (Board-Auto raus)
**Rationale:** beide Funktionen obsolet durch Drei-Lens-Architektur + Pipeline-Redesign-Vorbereitung.

## Was raus

### Commit A — Validator-Scope `disagreements`

**folio:**
- `WorkerRunPanel.svelte`: „Validator über Disagreements"-Button-Section
- `workerRun.svelte.ts`: `triggerValidator(scope)` Store-Method komplett (kein Caller mehr)
- `/api/validator/run/+server.ts`: `'disagreements'` aus type + VALID_SCOPES, default-scope wechselt von `'disagreements'` zu `'last-tranche'`
- `manager.ts`: `ValidatorScope` type ohne `'disagreements'`

**multi-agent:**
- `validator_batch.py`: SQL-branch für `scope == "disagreements"` in `fetch_target_uids` raus
- `--scope` CLI-arg: choices ohne `disagreements`, default `last-tranche`
- Docstring entsprechend

**Was BLEIBT:** `validatorOnly`-Listen-Filter im `mailQueue` (orthogonaler Listen-Filter, nicht-betroffen). Auto-Trigger nach silent-worker-end mit Scope `last-tranche` bleibt (`manager.ts:248`).

### Commit B — Board-Auto

**folio:**
- `manager.ts`: `slugifyBoard` + `ensureBoardExists` (Hermes-CLI-spawnSync) raus
- `manager.ts`: `startRun` baut Subprocess ohne `--board`-arg
- `manager.ts`: neuer `defaultBoardSlug(account)` für internen DB-Logging-Identifier
- `types.ts` `StartRunInput`: `board`-Feld weg
- `WorkerRunPanel.svelte`: Form-Input + `slugPreview` + `defaultBoardName` + `todayIso` + `slugifyPreview` alles raus. Grid auf 1 Spalte (nur Tranche).
- `workerRun.svelte.ts`: `board` state weg, `submit()` ohne board-payload
- `/api/worker/run/+server.ts`: `body.board` validation weg

**multi-agent:**
- `production_worker.py`: `--board` `required=True` → `default=None`
- `_preflight_board_exists`: conditional skip wenn args.board None

**Schema:** `worker_runs.board` bleibt `NOT NULL` — kein Migration nötig. Manager schreibt weiterhin slug-string als internen Identifier. `ActiveRunInfo.board` + `RecentRuns`-Display unverändert.

## Backup

Pre-Cleanup folio.db: `~/.folio/backups/folio-2026-05-27-pre-cleanup.db` (sqlite3 .backup, 128 worker_runs verifiziert).

## Verifikation (vor User-Smoke)

| Check | Result |
|---|---|
| folio `npm run check` (type-check) | 0 errors, 24 pre-existing warnings unchanged |
| multi-agent `python3 -c "import validator_batch, production_worker"` | imports ok |
| `python3 production_worker.py --help` zeigt `--board` als `[--board BOARD]` (optional) | ✓ |
| `test_subject_keyword_boundary.py` regression | 31/31 ✓ |
| `test_sender_prefix_match.py` regression | 28/28 ✓ |
| Bestehende worker_runs-Rows lesbar (kein Schema-Bruch) | ✓ (kein ALTER TABLE) |

**Noch nicht durch:** Lens-Pipeline-Smoke (10er-Tranche real). Wird per User-Action nach FF-Merge gestartet (siehe Direktive §Test).

## Reihenfolge / nächste Schritte

1. Architekt-Go für FF-Merge in beide main (folio + multi-agent)
2. Push pro Repo auf separates Signal
3. User-Smoke 10er-Tranche via WorkerRunPanel (jetzt ohne Board-Feld) → bestätigt „Worker-Run startet ohne `--board`-Arg sauber durch"

## Out of scope (nicht Teil dieses Cleanups)

- `validatorOnly` Listen-Filter — bleibt orthogonal
- worker_runs.board Schema-Drop — bewusst kein Migration (Strategy B, lesbarkeitserhaltend)
- Hermes-Chat-API-Use von `loadHermesEnvVars` — bleibt (nicht board-spezifisch)
- Phase-3.5c-Kompatibilität: `production_worker.py --board <slug>` funktioniert weiter wenn manuell explizit gesetzt (für Legacy-Manual-Runs mit Hermes-Board-Tasks)
