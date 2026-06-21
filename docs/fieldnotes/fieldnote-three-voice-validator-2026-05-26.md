# Field-Note — Three-Voice-Validator Part 1 (2026-05-26)

## Was Part 1 abdeckt (DB + Pipeline)

Direktive 2026-05-26 Part 1: zweite LLM-Stimme (Qwen, primary) neben gemma
(control), beide pro Tranche sequenziell mit Modell-Swap, persistiert als
separate Rows in `validator_opinions` über den UNIQUE-Key
`(feedback_id, validator_model)`. UI ist explizit **out of scope** (per
User-Korrektur DROP Part 2 — wartet auf Council-UI-Richtung + Claude-Design).

### Voice-Konfiguration in regelwerk.yaml

`voice_consensus.voices[]` jetzt mit `lm_studio_model` und `response_strip`
pro Voice:

| voice id | role | lm_studio_model | response_strip |
|---|---|---|---|
| heuristic | deterministic | `null` (kein LLM) | `none` |
| qwen-validator | primary_llm | `qwen3-30b-a3b-thinking-2507` | `think` |
| gemma-control | control_llm | `gemma-4-26b-a4b-it-mlx` | `code_fence` |

### Modul-Änderungen

| Datei | Änderung |
|---|---|
| `config/regelwerk.yaml` | voices um `lm_studio_model` + `response_strip` erweitert |
| `scripts/domain_actionability.py` | DEFAULT_REGELWERK analog erweitert |
| `scripts/model_swap.py` (NEU) | `swap_to()` / `unload_all_models()` / `load_model()` / `list_loaded()` via `lms` CLI; alle returnen bool, nie raise |
| `scripts/validator_batch.py` | (a) `re` import + `strip_llm_response()`, (b) gemma-Name-Fix (`gemma-4-26b-it-mlx` → `gemma-4-26b-a4b-it-mlx`), (c) `call_validator(*, model_id, response_strip)`, (d) `write_opinion(*, validator_model)`, (e) `main()`-Loop iteriert über voices mit swap |
| `scripts/diagnose_voices.py` (NEU) | CLI: `diagnose_voices.py <fb_id>` druckt Heuristik + alle voice-rows + Consensus-Indikator |
| `folio/src/lib/server/regelwerk/loader.ts` | `Voice`-Interface neu, `DEFAULT_REGELWERK` analog |

## Strukturelle Eigenschaften

### Modell-Swap-Verhalten

`lms` CLI ist verfügbar (`/Users/afschinmirhamed/.local/bin/lms`).
`swap_to(model_id)` macht intern `lms unload --all` → `lms load <model>`.
Bei jedem Fehler (CLI nicht da, OOM, load-timeout, return-code≠0) wird
gewarnt und `False` zurückgegeben — der Orchestrator skippt diese Voice
und macht mit der nächsten weiter. **Eine Tranche failt NIEMALS wegen
Swap-Fehler** (Direktiven-Contract).

### Idempotenz + UNIQUE-Constraint (verifiziert)

Direkt-Schreibtest gegen folio.db.validator_opinions (pre/post-Count
nachgewiesen mit Cleanup):

| Aktion | Δ rows | Begründung |
|---|---|---|
| `write_opinion` mit `validator_model=qwen3-30b-a3b-thinking-2507` | +1 | neue (fb_id, model) Kombination |
| `write_opinion` mit `validator_model=gemma-4-26b-a4b-it-mlx` (gleiche fb_id) | +1 | andere Kombination → zweite Row |
| `write_opinion` mit `qwen…` (gleiche fb_id, gleiches Modell) | ±0 | UPSERT-UPDATE auf bestehender Row |

→ UNIQUE-Constraint hält, Re-Run ist idempotent. Pro Mail gibt es nach
vollem Build genau 2 LLM-voice-Rows (plus die Heuristik-„Voice" in
`feedback.db`, die nicht in `validator_opinions` lebt).

### Think-Strip Pattern (unit-getestet)

`strip_llm_response(text, "think")`:
1. Entfernt `<think>...</think>`-Blöcke (multi-line, ggf. mehrere)
2. Entfernt anschließend ```...``` / ```json-Fences (analog zu Mode "code_fence")
3. Idempotent für Modelle ohne think-Block (kein Match = no-op)

Unit-Tests in `scripts/validator_batch.py` (via inline-Python während Build):
- think + plain json: ✓ parst korrekt
- think + code-fence json: ✓ parst korrekt
- code_fence only (kein think): ✓ parst korrekt
- think-strip auf plain output: ✓ no-op, parst weiterhin

### Manual UI Trigger

Folio's `worker-runner/manager.ts:248` spawnt `validator_batch.py --scope ...`
(ein Spawn). Mit dem Voice-Loop intra-script werden ALLE LLM-voices in
diesem einen Spawn sequenziell verarbeitet — automatisch sowohl bei
manual als auch bei auto-Trigger. **Keine Folio-Code-Änderung nötig.**

## Auto-Routing bleibt gated

Per Direktive: dieser Build macht NUR die zweite Stimme real. Auto-Routing/
auto-silent ist NICHT enabled. `voice_consensus.decide_routing()` ist
weiterhin nur Stub-Modul mit Self-Tests; wird nicht eingebunden.

## Bewusst nicht migriert (in Übereinstimmung mit Direktive)

1. **UI** (DetailPanel.svelte:525-572): unverändert. Wartet auf Council-UI +
   Claude-Design-Konsult. Folio's Page-Loader holt heute nur EINE Voice
   (`row.validator_opinion`, singular) — welche genau, ist eine bestehende
   +page.server.ts-Frage. Mit dem Build hat die DB ab dem ersten echten
   Two-Voice-Run **zwei rows pro feedback_id**; UI sieht aber weiter nur eine.
   Council-Build wird die Multi-Voice-Lese-Funktion in `folio-db/reader.ts:78-81`
   (`listValidatorOpinionsForFeedback`) reuseren.
2. **Plugin-Verdict-Surfacing**: plugin_value (z.B. "werbung", "geschaeftspost")
   bleibt in `feedback.db` versteckt — `diagnose_voices.py` zeigt es immerhin
   pro Mail im Output.
3. **Auto-Routing-Pfad**: kein Auto-Move bei Konsens, kein Council-Hand-off-
   Trigger. Schutzklausel-Logik (`apply_protection_clause`) ist in
   `voice_consensus.py` aber bereits implementiert.
4. **I5 Lernloop**: parked bis 200 Mails / 20 Council-Objekte.
5. **Council-Worker-Recycling**: out of scope.
6. **Domain-Listen / Legacy-Schema (I8)**: koexistiert weiter.

## Verifikations-Pfad

### Non-Live (in dieser Build-Iteration durchgeführt):

- `scripts/voice_consensus.py` Self-Tests: 7 Assertions grün (Schutzklausel
  + strict/majority-Trennung + decide_routing-Mode-Switch)
- `scripts/model_swap.py` Diagnostic-Run: `lms available: True`,
  `currently loaded: []` — CLI reachable
- `strip_llm_response` Unit-Tests: 4 Strip-Szenarien parsen json korrekt
- Direkt-Schreibtest auf folio.db.validator_opinions: UNIQUE hält,
  Idempotenz hält, Cleanup auf 0 Δ
- `diagnose_voices.py <feedback_id>` zeigt alle voice-rows mit
  Consensus-Indikator
- `python3 -c "import ast; ast.parse(...)"`: alle Files syntax-clean
- folio `svelte-check`: 0 errors

### Live-Smoke (User-Verifikation nach Auth-Setup):

```bash
# Echter Run einer Tranche durch beide Voices
cd ~/Projects/aion-lumen/multi-agent
HERMES_API_KEY=<key> python3 scripts/validator_batch.py --scope last-tranche --limit 1

# Diagnose:
python3 scripts/diagnose_voices.py --all                  # zeigt jetzt fb_ids mit 2 voices
python3 scripts/diagnose_voices.py <fb_id>                # zeigt qwen + gemma row
```

Erwarteter Output: per Mail in der Tranche 2 voice-rows in `validator_opinions`,
qwen-row mit `<think>`-strippped reasoning, beide voices auf domain×action-Achse.

### Swap-Failure-Simulation:

```bash
# Während Run: lms unload --all in einem anderen Terminal nach erstem
# voice-Pass → zweiter Pass schlägt swap_to fehl → log + continue.
# Erwarteter Exit-Code: 0, validator_opinions hat nur voice-1-Rows.
```

## Stale Reminders (für künftige Iterationen)

- Folio Page-Loader (`+page.server.ts`) muss bei Multi-Voice-Konsum
  umgestellt werden auf `listValidatorOpinionsForFeedback()` statt singular
  fetch. Wird gemeinsam mit dem UI-Build kommen.
- Voice-Reihenfolge in regelwerk.yaml bestimmt Modell-Lade-Reihenfolge. Wenn
  Plugin-Executor (qwen) bereits resident ist, könnte ein `qwen → gemma →
  qwen-reload-für-nächste-Mail` overhead haben. Aktuell wird einfach
  `unload-all` + `load` gemacht — Plugin-Auswirkung dokumentieren wenn live.
- `lms` Output-Parsing in `list_loaded()` ist Best-Effort (Format-Drift in
  künftigen lms-Versionen möglich) — nur für Diagnose, nicht für Decision-Logic.

## Quellen

- Direktive: `~/Projects/architektur-folio-council.md` (Voice-Konzept-Kontext)
- Direktive: User-Block 2026-05-26 (Three-Voice-Build, Part 2 via Korrektur ausgeklammert)
- Vorgänger-Field-Note: `fieldnote-regelwerk-zentralisierung-2026-05-26.md`
- Vorgänger-Klärung: `klaerung-stimmen-mapping-ui-2026-05-26.md`
- Branch: `feature/three-voice-validator-2026-05-26` (multi-agent)
- Folio bleibt unangefasst außer `regelwerk/loader.ts` (Type-Update)
