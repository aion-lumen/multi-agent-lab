# Field-Note — Lens-Fix Bypass + Sync-Swap (2026-05-26)

## Was Part 1 abdeckt

Direktive 2026-05-26 Lens-Fix: drei-Lens-Pipeline produzierte 0 rows trotz
exit 0. Root-Cause war zweifach:

- **H2.1 (Hermes-Coercion):** Hermes' `/v1/responses` ignoriert den `model`-
  Parameter und erzwingt sein Default-Modell aus `~/.hermes/config.yaml`
  (qwen3.6-35b-a3b-ud-mlx). Bei Upstream-400 wird der Fehler als `text` in
  eine HTTP-200-Response embedded → `json.loads("")` → `None` zurück → 0 rows.
- **H2.2 (Async lms-load):** `lms load <model>` returnt 0 fire-and-forget
  nach ~5s, während ein 16-20 GB Modell tatsächlich 30-60s zum Laden braucht.
  `lms ps` post-run zeigt das Zielmodell NICHT geladen.

Fix: F4 (`enabled`-Flag) als Sofort-Sicherung + Hermes-Bypass + synchroner
Swap. Verifiziert mit echtem 3-Lens-Run über 1 mail (Konsens einstimmig).

## Module-Änderungen

### regelwerk.yaml + DEFAULT_REGELWERK
- voices erweitert: 4 voices in Direktiven-Reihenfolge (Lens 1=gemma, 2=qwen3.6,
  3=qwen-thinking; plus heuristic als deterministische Stimme).
- Neues Feld `enabled: true|false` pro voice. Default bei fehlendem Feld =
  `true` (backward-compat — verifiziert mit stub-yaml).

### scripts/model_swap.py
- `is_model_loaded(model_id) -> bool`: **EXAKTER Match** auf IDENTIFIER-Spalte
  von `lms ps`. Substring-Match war gefährlich — `qwen3-30b` hätte fälschlich
  auf `qwen3-30b-a3b-thinking-2507` gematched.
- `wait_for_lens_model_loaded(model_id, timeout_s=120, poll_interval_s=2)`:
  Polling-Loop bis Modell als geladen bestätigt.
- `swap_to(model_id)` erweitert: nach `lms load` ruft jetzt `wait_for_…` —
  returnt nur True wenn Modell ECHT geladen ist.
- `unload_plugin_before_first_lens()`: explicit unload-all vor erster Lens
  (verhindert RAM-Guardrail-Konflikt mit residentem Plugin-Modell).

### scripts/validator_batch.py
- `LM_STUDIO_BASE_URL = "http://127.0.0.1:1234"` (override via env).
- `LENS_PROMPT` (neu): dediziertes Prompt-Template ohne Heuristik-Zeile,
  ohne Plugin-Hint-Zeile, ohne Erwähnung anderer Stimmen. Delphi-Prinzip-
  Implementation. Verifiziert mit Pattern-Asserts.
- `call_lens_lm_studio(row, ctx, regelwerk, *, model_id, response_strip)`:
  direkter POST an LM-Studio `/v1/chat/completions`. OpenAI-Format
  (`messages: [{role: user, content: …}]`, response unter
  `choices[0].message.content`). KEIN Hermes, KEIN Auth (loopback-local).
- `main()`-Loop: Pre-Loop `_unload_plugin()`, dann pro lens `swap_to()`
  (synchron) + pro row `call_lens_lm_studio()`. Skip-Logik für
  `enabled: false`. Log-Format umgestellt auf `lens=…` (Bestand bleibt für
  validator_opinions-Tabelle/Spalten).
- Alte `call_validator()` bleibt unangetastet (Backward-Compat für ad-hoc
  Hermes-Runs).

### folio/src/lib/server/regelwerk/loader.ts
- `Voice.enabled?: boolean` optional.
- DEFAULT_REGELWERK voices analog erweitert (4 voices in korrekter Reihenfolge).

## Verifikation (alle Tests grün)

### Schritt 2A model_swap
- `list_loaded()` parst IDENTIFIER-Spalte exakt (verifiziert mit `gemma-4-26b-
  a4b-it-mlx`).
- `is_model_loaded('qwen3-30b')` → **False** wenn `qwen3-30b-a3b-thinking-2507`
  geladen ist (substring-trap eliminiert).
- `is_model_loaded('')` → False.

### Schritt 2B LENS_PROMPT-Blindheit
Negativ-Asserts (Pattern darf NICHT im Lens-Prompt sein):
- `"Heuristik (Worker) klassifizierte:"` ✓ raus
- `"Plugin-Class-Hint:"` ✓ raus
- `"heur_domain="` / `"heur_actionability="` / `"plugin_value="` ✓ raus
- `"move_immo_portal"` (heur-leak-test) ✓ raus

Positiv-Asserts (Pattern MUSS im Prompt sein):
- Sender, Subject, Body-Excerpt ✓
- Lens-Identität (`"blinder Klassifikator"`) ✓

### Schritt 3 Real-Run mit echten LLMs
`--scope last-tranche --limit 1` über feedback_id=361 (Wingo-Marketing-Mail,
yahoo/uid 419329):
- Pre-loop: `unload plugin (any resident model) before first lens` ✓
- Lens 1 gemma: swap+wait+confirmed (8s), call OK → `werbung/archive-silent` conf=1.00
- Lens 2 qwen3.6-35b: swap+wait+confirmed (8s), call OK → `werbung/archive-silent` conf=0.95
- Lens 3 qwen-thinking: swap+wait+confirmed (5s), call OK → `werbung/archive-silent` conf=0.95
- `total_opinions=3/(1×3)`
- `diagnose_voices.py 361`: 3 distinct lens_model rows, Heuristik auch werbung/silent →
  **CONSENSUS: ●●● einig (werbung/archive-silent)**

Laufzeit: ~1 Min für 1 mail mit 3 Lenses (im Direktiven-Schätzbereich 5-12 min
für Tranche-10, hier viel schneller weil Modelle resident-warm waren).

## Wichtige Eigenschaften

### Blindheit-Garantie
LENS_PROMPT enthält keine Heuristik-/Plugin-/andere-Lens-Hinweise. Jede Lens
sieht NUR: System-Prompt + Action-Definitionen + User-Context-Block
(Prioritäten, Distanz-Schwellen, Sender-Regel-Counts) + Sender/Subject/Body.
Delphi-Prinzip strukturell durchgesetzt.

### Async-Swap eliminiert
`swap_to()` returnt nur True wenn `is_model_loaded(model_id)` bestätigt hat.
Bei Timeout (120s default) → False → caller log+continue. Kein silent „loaded"
mehr ohne tatsächliches Loading.

### Plugin-Conflict eliminiert
`unload_plugin_before_first_lens()` läuft vor der ersten Lens. Lens-1-Swap
beginnt mit leerem RAM, kein Guardrail-Trip.

### Bestehende Symbole unverändert (Naming-Disziplin)
- `validator_opinions`-Tabelle: Name bleibt
- Spalten `validator_model/domain/action`: bleiben
- `validator_batch.py` Filename: bleibt
- `/api/validator/run`: bleibt
- `voice_consensus` + `voices[]` in yaml: bleiben (separate Rename-Direktive
  „validator → lens" kommt nach diesem Build)

Neue Lens-namespaced Symbole:
- `call_lens_lm_studio`, `LM_STUDIO_BASE_URL`, `LENS_PROMPT`
- `wait_for_lens_model_loaded`, `is_model_loaded`, `unload_plugin_before_first_lens`
- yaml-Feld `enabled` (neutral, nicht lens-prefix)
- voice-id `qwen35b-lens` (für Lens 2 — neu eingeführt)

## Flagging — keine Konflikte beim Build

- KEINE neue Spalte in `validator_opinions` nötig — Bestandsschema reicht.
- KEINE Codebase-vs-Direktive-Konflikte aufgetreten.
- `lms ps` Format ist stabil (gegen-prüfung in `list_loaded()` heute, falls
  künftig drift: nur diagnose betroffen, nicht Decision-Logic).

## Bewusst NICHT migriert (per Direktive §4 Prohibitions)

1. **Validator→Lens-Rename** existierender Symbole — eigene Direktive
2. **Auto-Routing / Auto-Silent** — bleibt gated, Manual default
3. **UI** (DetailPanel, Lens-Display-Komponente) — geparkt bis Council-UI
4. **Hermes-Steuerschicht für andere Pipelines** — nur Lens-Pipeline
   bypassed; Folio-Hermes-Chat (`/api/hermes/chat`) bleibt unangetastet
5. **v0.14-Migration** — separate Doku-Aufgabe (§0.2, non-blocking)
6. **I5 Lernloop** — parked
7. **Plugin-Code** — unangetastet

## Worker-Runs-Counter `mails_processed=0`

Validator-Runs zeigen weiterhin `mails_processed=0` in `worker_runs`. Das ist
ein Artefakt: der Folio-Worker-Runner-Counter wird nur für silent-Worker-Runs
erhöht, nicht für validator-Runs (die zählen intern via `total_opinions`-Log).
Nicht symptomatisch, bekannt — kann in eigener UI-Iteration adressiert werden.

## Stale-Reminder für künftige Iterationen

- **Folio Page-Loader** (`+page.server.ts`) holt heute nur EINE
  `row.validator_opinion` (singular). Mit jetzt 3 voice-rows pro mail muss
  bei UI-Build auf `listValidatorOpinionsForFeedback()` umgestellt werden
  (`folio/src/lib/server/folio-db/reader.ts:78-81` existiert bereits multi-model).
- **§0.1 + §0.2 Doku** sind non-blocking — können nach diesem Fix geliefert
  werden (Pipeline-Kontext-Doc + Hermes-v0.13-Funktionsstand).
- **lms-Format-Drift:** wenn künftige `lms`-Version IDENTIFIER nicht mehr als
  erste Spalte hat, `list_loaded()` anpassen.

## Quellen / Pfade

- Direktive: `~/Projects/direktive-lens-fix-2026-05-26.md`
- Diagnose-Befund-Vorlage: `docs/befund-three-voice-validator-diagnose-2026-05-26.md`
- Real-Run-Log: `/tmp/lens-fix-realrun.log` (transient)
- Branch: `feature/three-lens-fix-2026-05-26` (multi-agent + folio)
- Test-Mail: feedback_id=361 (yahoo/uid 419329, marketing@wingo.ch)
