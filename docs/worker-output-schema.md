# Worker-Output-Schema — Aion Lumen Multi-Agent

**Status:** verbindlich für alle Worker-Profile (Architect, Validator, Executor, Librarian)
**Quelle:** Architektur-Synthese 2026-05-08, Sektion 6 — Trajectory-Poisoning-Schutz durch strukturierte Outputs
**Geltungsbereich:** Hermes Kanban Workflows, Pilot life-mail-100, alle Folge-Pipelines

---

## Warum strukturierte Outputs

Cursor-Recherche-Kernbefund: das größte Risiko in Multi-Agent-Setups ist **Tool-Execution-Fabrication** — ein Worker behauptet einen Tool-Call durchgeführt zu haben, ohne ihn wirklich auszuführen, und liefert erfundene Resultate. Beispiel: Worker sagt „ich habe Email X gelesen und sie ist Werbung", aber er hat Email X nie wirklich gelesen — er hat aus dem Subject geraten.

Strukturierte JSON-Outputs mit verpflichtenden Feldern für `evidence` und `tool_trace` machen diese Fabrikation für den Validator detektierbar.

---

## Schema (verbindlich)

Jeder Worker liefert bei Task-Abschluss ein JSON-Objekt nach folgendem Schema:

```json
{
  "task_id": "t_abc123",
  "profile": "executor",
  "outcome": "completed",
  "result": {
    "type": "classification",
    "value": "werbung",
    "confidence": 0.92,
    "alternative_values": ["newsletter"],
    "reasoning_summary": "3 Marketing-Phrasen detected, Sender-Domain in Werbeliste"
  },
  "evidence": [
    {
      "type": "text_snippet",
      "content": "Heute nur 50% Rabatt!",
      "source": "email_body",
      "weight": 0.7
    },
    {
      "type": "rule_match",
      "rule": "marketing_phrases_v1",
      "matches": 3
    }
  ],
  "tool_trace": [
    {
      "tool": "imap_fetch",
      "called_at": "2026-05-08T14:23:01Z",
      "params": {"mailbox": "INBOX", "uid": 12345},
      "success": true,
      "duration_ms": 142
    }
  ],
  "next_action_suggestion": "move_to_folder:promotions"
}
```

---

## Felder im Detail

### Pflichtfelder (Top-Level)

| Feld | Typ | Beschreibung |
|---|---|---|
| `task_id` | string | Hermes-Kanban-Task-ID, Format `t_<hex>` |
| `profile` | enum | Eines von: `architect`, `validator`, `executor`, `librarian` |
| `outcome` | enum | Eines von: `completed`, `needs_validation`, `escalate_to_architect`, `escalate_to_user` |
| `result` | object | Task-spezifisches Ergebnis (siehe unten) |
| `evidence` | array | Mindestens ein Eintrag, sofern `outcome != escalate_to_user` |
| `tool_trace` | array | Mindestens ein Eintrag, sofern Tools verwendet wurden |
| `next_action_suggestion` | string \| null | Hinweis für Routing-Logik des Librarian, oder `null` |

### `result`-Objekt

| Feld | Typ | Pflicht | Beschreibung |
|---|---|---|---|
| `type` | string | ja | Task-spezifisch, z.B. `classification`, `score`, `plan`, `audit` |
| `value` | any | ja | Konkretes Ergebnis (String, Number, Object — task-abhängig) |
| `confidence` | float | ja | 0.0 bis 1.0. Triggert Eskalation < 0.7 |
| `alternative_values` | array | optional | Zweite/dritte beste Hypothesen |
| `reasoning_summary` | string | ja | Maximal 200 Zeichen Begründung |

### `evidence`-Eintrag

Pro Element ein Beleg, warum der Worker zu seinem `result.value` kam.

| Feld | Typ | Pflicht | Beschreibung |
|---|---|---|---|
| `type` | enum | ja | `text_snippet`, `rule_match`, `tool_output_ref`, `prior_output_ref` |
| `content` | string | bei `text_snippet` | Wörtliches Zitat aus Quelle |
| `source` | string | ja | Wo der Beleg herkommt: `email_body`, `email_header`, `tool:<tool_name>`, `kanban_task:<id>` |
| `weight` | float | ja | 0.0 bis 1.0, Beitrag zum Gesamt-Confidence |
| `rule` | string | bei `rule_match` | Regelname, z.B. `marketing_phrases_v1` |
| `matches` | int | bei `rule_match` | Anzahl Matches |

### `tool_trace`-Eintrag

Pro Tool-Call einen Eintrag. **Validator prüft Cross-Reference gegen Hermes-Logs.**

| Feld | Typ | Pflicht | Beschreibung |
|---|---|---|---|
| `tool` | string | ja | Tool-Name aus Hermes-Skill-Registry |
| `called_at` | ISO8601 | ja | UTC-Timestamp |
| `params` | object | ja | Parameter-Echo |
| `success` | bool | ja | War der Call erfolgreich? |
| `duration_ms` | int | ja | Laufzeit in Millisekunden |
| `result_ref` | string | optional | Pointer auf Hermes-Log-Eintrag, z.B. `~/.hermes/logs/2026-05-08-12.log:142` |

### `outcome`-Werte

| Wert | Bedeutung | Routing-Konsequenz (Librarian) |
|---|---|---|
| `completed` | Worker ist sicher, Task ist fertig | Validator-Review (Pflicht) |
| `needs_validation` | Worker explizit unsicher | Validator-Review |
| `escalate_to_architect` | Komplex, braucht Plan | Architect lädt, plant um |
| `escalate_to_user` | Mensch muss entscheiden | Watchdog-Bot pingt User |

---

## Validator-Pflichtcheck

Bei jedem eingehenden Worker-Output prüft der Validator (Profil `validator`, Modell `gemma-4-26b-a4b-it-mlx`) folgende Punkte:

### 1. Schema-Konformität

- Alle Pflichtfelder vorhanden, korrekte Typen
- `confidence` in [0.0, 1.0]
- `outcome` ist gültiger Enum-Wert

### 2. Evidence-Konsistenz

- Mindestens ein `evidence`-Eintrag, sofern `outcome != escalate_to_user`
- Bei `evidence.type == text_snippet`: `content` ist nicht leer und plausibel zur Quelle (`source`)
- Summe der `evidence.weight` bei normalisierter Bewertung passt zu `result.confidence` (±0.2 Toleranz)

### 3. Tool-Trace-Audit (Trajectory-Poisoning-Schutz)

- Jeder behauptete Tool-Call in `tool_trace` muss in den Hermes-Logs (`~/.hermes/logs/`) auffindbar sein, mit übereinstimmendem `tool`, `called_at` (±5 Sekunden), `params` und `success`
- Falls `result_ref` gesetzt: Eintrag dort prüfen
- Bei Mismatch: `outcome` automatisch zu `escalate_to_architect` setzen, Validator-Comment dokumentiert die Diskrepanz

### 4. Plausibilitäts-Heuristik

- `result.confidence` plausibel zur `evidence`-Stärke (nicht 0.95 mit nur einem schwachen Beleg)
- `reasoning_summary` referenziert mindestens ein `evidence`-Element
- `next_action_suggestion` (sofern gesetzt) ist konsistent mit `result.value`

### 5. Echo-Effekt-Schutz

Falls ein Worker auf einen früheren Worker-Output verweist (`prior_output_ref`):
- Prüfen, dass der Verweis nicht zirkulär ist
- Prüfen, dass die ursprüngliche Quelle vom selben Profil-Modell stammt — wenn ja, prüft Validator besonders streng (Echo-Risiko erhöht)

---

## Beispiel-Use-Cases

### life-mail-Klassifikation

Executor klassifiziert eingehende Mail. `result.type = "classification"`, `result.value` ist eine Kategorie (`werbung`, `geschäftspost`, `privat`, `spam`, ...). `tool_trace` enthält den `imap_fetch`-Call.

### Council-Listing-Scoring

Executor bewertet Immobilien-Listing nach Hard-Filter. `result.type = "score"`, `result.value` ist ein Numeric (0–100). `evidence` enthält `rule_match` für jede aktive Hard-Filter-Regel.

### Architect-Plan

Architect plant komplexe Multi-Step-Aufgabe. `result.type = "plan"`, `result.value` ist eine Liste von Sub-Tasks mit Profile-Zuordnung.

---

## Schema-Versionierung

Aktuelle Version: **v1.0** (2026-05-09).

Bei Änderungen: Major-Version-Bump bei Breaking Changes (Pflichtfeld entfernt/Typ geändert), Minor-Version bei additiven Erweiterungen. Worker übermitteln implizit ihre Schema-Version via `profile`-Konfiguration in Hermes — bei Mismatch lehnt Validator ab.

---

## Referenzen

- Architektur-Spezifikation: `~/Projects/aion-lumen/multi-agent/architecture-2026-05-08.md` (Sektion 6, 11, 12)
- Hermes-Profile-Konfiguration: `~/.hermes/config.yaml` (Block `profiles:`)
- Telemetrie-Auswertung: `~/.hermes/kanban/logs/<board>/`
