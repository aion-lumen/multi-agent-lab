# Field-Note — Regelwerk-Zentralisierung (2026-05-26)

## Was zentralisiert wurde

Neue Datei `~/Projects/aion-lumen/multi-agent/config/regelwerk.yaml` als
zentrale, menschenlesbare Quelle für die F.8-Klassifikations-Achse
(domain × actionability). Bildet die Direktiven-Entscheidungen E1–E4 ab:
- **E1** action_definitions (actionable/archive/archive-silent) mit deutschen
  Labels und Beschreibungen
- **E2** priority_relevance mit max_distance_km pro Priorität (hauskauf: **40 km**
  nach Wert-Korrektur, jobsuche: 100 km), fallback_unknown_plz pro Priorität
- **E3** mode (manual/auto) als konfigurierbarer Schalter
- **E4** voice_consensus mit strictness + protection_clause

## Wer liest jetzt aus der zentralen Quelle

| Konsument | Pfad | Status |
|---|---|---|
| Heuristik (Worker) | `domain_actionability.py:load_regelwerk()` + `validate_regelwerk_against_context()` (nach `:452`) | Loader vorhanden; F.8-Pipeline-Steps konsumieren noch nicht (siehe „Bewusst nicht migriert") |
| Validator-Prompt | `validator_batch.py:format_actionability_block()` + `format_user_context_block()` mit regelwerk-Erweiterung; main() lädt + validiert Cross-Ref + propagiert in `call_validator()` | ✓ live — Prompt rendert action_definitions + max_distance_km aus YAML |
| Folio Layout-Server | `folio/src/lib/server/regelwerk/loader.ts:loadRegelwerk()` + `loadRegelwerkValidated()`; `+layout.server.ts` lädt regelwerk + reicht es als page-data | ✓ live — page.data.regelwerk in jedem Folio-Route verfügbar |
| Folio UI-Labels | `mail-account.ts:ACTIONABILITY_LABELS` auf deutsche Werte umgestellt mit Sync-Kommentar zur YAML | partiell (siehe „Bewusst nicht migriert" Punkt 1) |

## Schutzklausel-Trennung (F5-Schärfung)

`scripts/voice_consensus.py` implementiert die Stimmen-Aggregation strukturell
so, dass die Schutzklausel (`route_to_actionable_always` bei Uneinigkeit)
UNABHÄNGIG vom `strictness`-Wert ist:

- `apply_protection_clause(votes)` — eigenständige Funktion, kennt strictness
  nicht. Feuert sobald irgendzwei Stimmen in Domain ODER Action abweichen.
- `is_consensus(votes, strictness)` — eigenständige Funktion, kennt
  Schutzklausel nicht. Reiner strict/majority-Check.
- `decide_routing()` ruft IMMER ZUERST `apply_protection_clause()`, dann
  ggf. den strictness-Check.

Self-Test im Modul verifiziert: bei `strictness="majority"` würde
`is_consensus` für eine 2-aus-3-Mehrheit `True` zurückgeben, aber
`apply_protection_clause` feuert trotzdem, weil eine Stimme abweicht. Damit
kann ein Wechsel von strict → majority die Schutzklausel strukturell nicht
mit aufweichen.

## Verbleibende Aufgaben (bewusst nicht migriert)

### 1. UI-Komponenten-Migration auf `page.data.regelwerk` (Phase 2)

Aktuell: `mail-account.ts:ACTIONABILITY_LABELS` ist auf deutsche Default-Labels
umgestellt mit Sync-Kommentar zur YAML. Komponenten (`DetailPanel`,
`MailList`, `FilterDisclosure`) importieren weiterhin diese Konstante. Bei
Änderung der Labels in `regelwerk.yaml` MUSS auch `mail-account.ts` synchron
nachgezogen werden.

Saubere Lösung (Phase 2): Komponenten lesen
`page.data.regelwerk.action_definitions[key].label` statt direkter
Konstanten-Import. Hat aber UI-Touch-Risiko (~10 Stellen in 3 Files) und
braucht Browser-Verifikation. Heute aufgeschoben zugunsten von Geschwindigkeit
+ klarer Sync-Doku in der Konstante.

### 2. Heuristik-F.8-Pipeline-Konsum

`load_regelwerk()` existiert, wird aber von `classify_domain_actionability()`
noch nicht aufgerufen. Heuristik-Code (`_initial_actionability`,
`_apply_time_decay`, `_apply_priority_boost`) operiert weiter auf hardcoded
Per-Domain-Defaults. Sinn: heutige E1-E4-Werte ändern die Klassifikations-
Logik nicht (Action-Vokabular ist identisch, Distanz-Schwelle ist ein E2-Soll
das noch nicht im Code lebt — heutiger Code hat keine Distanz-Logik).

Wenn E2 (Distanz-Schwelle als Entscheidungs-Kriterium) im Code aktiv wird,
muss `classify_domain_actionability()` `regelwerk.priority_relevance` lesen.
Das ist ein Build-Schritt der Direktiven-Familie „Distanz als Auto-Heuristik"
(Block 4 alt aus bugs-links-distanz).

### 3. Auto-Routing-Logik

`voice_consensus.py` ist Stub-Modul mit Self-Tests, wird aber von keiner
Pipeline eingebunden. Greift erst, wenn der dritte Validator (Qwen) im
Build steht — Direktive `klaerung-qwen-dritter-validator.md`. Heute existiert
nur Heuristik + gemma-Validator. Drei-Stimmen-Konsens braucht alle drei.

### 4. Validator-Modell-Name-Diskrepanz

`validator_batch.py:50`: `VALIDATOR_MODEL = "gemma-4-26b-it-mlx"` existiert in
LM-Studio nicht; das echte Modell ist `gemma-4-26b-a4b-it-mlx`. Hermes-Router
fuzzy-matched aktuell, aber das ist nicht garantiert. Klärung-b-plus-stimmen-ui
hat das schon dokumentiert; Korrektur kommt mit der Qwen-Build-Direktive.

### 5. Domain-Listen-Zentralisierung (Phase 2)

`domain_actionability.py:69-128` hat IMMO_DOMAINS, JOB_DOMAINS etc. hardcoded.
Direktive sagt explizit „strikt E1–E4, Domain-Listen erst Phase 2". Nicht in
dieser Iteration.

### 6. I8 Zwei-Schema-Koexistenz bleibt unangetastet

`feedback.db.feedback` hat sowohl `heuristic_suggested_action` (Legacy
5-Action) als auch `domain` + `actionability` (F.8 2-Achsen). Beide werden
parallel geschrieben. Die Direktive operiert ausschließlich auf der F.8-
Achse — Legacy 5-Action-Schema (`move_immo_*`, `move_zu_pruefen`,
`move_paketzustellung`, `keep`) bleibt unberührt. Welches Schema in der
Folio-UI „die Wahrheit" ist, ist eine separate Klärung wert (vermutlich
F.8, weil neuer).

## Tests + Verifikation

- `scripts/voice_consensus.py` Self-Test: 7 Assertions zur Schutzklausel +
  strict/majority-Trennung + decide_routing-Mode-Switch — alle grün.
- `domain_actionability.py:load_regelwerk()` Smoke: mode, priorities,
  Distanz-Werte (hauskauf=40, jobsuche=100), strictness, protection,
  action-label korrekt geladen.
- `validate_regelwerk_against_context()` Positiv + Negativ-Test: passt bei
  konsistenter Config, raised bei inkonsistenter.
- `validator_batch.py` Prompt-Render-Smoke mit Stub-Row: voller Prompt
  enthält "Erfordert Handlung/Entscheidung" + "max_distance_km=40" +
  "max_distance_km=100" — Werte aus YAML, nicht hardcoded.
- `folio` svelte-check: 0 errors nach loader.ts + layout.server.ts +
  label-Update.

## Quellen / Pfade

- Direktive: `~/Projects/direktive-regelwerk-zentralisierung.md` (+ Freigabe)
- Vorab-Stopp-Doc: `multi-agent/docs/vorschlag-regelwerk-zentralisierung-form-2026-05-26.md`
- Branch: `feature/regelwerk-zentralisierung-2026-05-26` (multi-agent + folio)
- Neue Files:
  - `multi-agent/config/regelwerk.yaml`
  - `multi-agent/scripts/voice_consensus.py`
  - `folio/src/lib/server/regelwerk/loader.ts`
  - `multi-agent/docs/fieldnotes/fieldnote-regelwerk-zentralisierung-2026-05-26.md` (dieses Doc)
- Geänderte Files:
  - `multi-agent/scripts/domain_actionability.py` (load_regelwerk + DEFAULT_REGELWERK + validate)
  - `multi-agent/scripts/validator_batch.py` (Prompt-Template + Render-Funktionen + main-Loader)
  - `folio/src/routes/+layout.server.ts` (regelwerk an page.data)
  - `folio/src/lib/util/mail-account.ts` (deutsche Labels + Sync-Kommentar)
