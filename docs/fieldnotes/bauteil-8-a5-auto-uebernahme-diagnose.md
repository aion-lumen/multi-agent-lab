# Befund — A5: Auto-Übernahme-Diagnose (Bauteil 8)

**Datum:** 2026-06-09. **Stopp-Punkt vor Variante-Wahl.**

## Wurzel-Befund

Über die 90 Mails der Bauteil-7-Runs (feedback.id 795–884):
- **0 Validator-Opinions** in `folio.db.validator_opinions` für diese
  Mail-IDs.
- Heuristik klassifizierte 10 von 90 als `immo + actionable`.
- 4/4-Vollkonsens **technisch unerreichbar**, weil 3/4 der Stimmen
  (qwen35b-lens, gemma-control, qwen-validator) leer sind.

```
Histogramm 4-Tupel (heuristik + 3 validator immo+actionable):
  1/4: 10 Mails  (nur Heuristik)
  0/4: 80 Mails

Histogramm 3-Validator-Opinions:
  0/3: 90 Mails  ← ALLE leer

Mails mit Block-Marker:           3
4/4 immo+actionable ohne Block:   0
```

## Root-Cause

Die Worker-Logs zeigen Cascade-Erfolg pro Run:
```
2026-06-09 06:15:23 validator_batch done: total_opinions=90/(30×3)
per-voice={'gemma-control': {ok: 30}, 'qwen35b-lens': {ok: 30},
'qwen-validator': {ok: 30}}
```

ABER auch (Run 1, Z. ~05:58):
```
last-tranche window: started_at=2026-06-09T02:13:39.467Z
                     ended_at=2026-06-09T02:14:40.665Z
target rows: 30
```

Das **last-tranche-Window** zeigt auf **2026-06-09 02:13** — die Bauteil-6
Live-Test-Tranche vom Vortag, NICHT auf den frisch gelaufenen Bauteil-7-
Worker (05:57 startete).

**Mechanik:**
1. Worker (production_worker) klassifiziert Mails 795–824 → INSERT in
   `worker_runs` mit `status='running'`.
2. write_summary läuft.
3. Cascade-Subprocess startet, ruft `fetch_last_tranche_window()` auf.
4. Diese Funktion sucht `WHERE mode='silent' AND status='completed' AND
   ended_at IS NOT NULL` → bekommt den **vor**letzten Run (02:13), weil
   der aktuelle noch `running` ist.
5. Cascade läuft gegen 30 Mails aus 02:13-Tranche → schreibt brav 90
   validator_opinions für FALSCHE Mail-IDs.
6. `auto_uebernahme` läuft anschließend, findet für die 02:13-Mails
   schon längst übernommen / archive-silent → `eligible=0, promoted=0`.

**Telltale-Bestätigung:**
```
Letzte validator_opinions evaluated_at: 2026-06-09T05:06 (= Bauteil-6
Live-Test). Bauteil-7-Zeitfenster (05:57–07:06): 0 Einträge.
```

## Folgerung

`auto_uebernahme.eligible=0` in allen 3 Bauteil-7-Runs ist **kein**
Konsens-Schwelle-Problem (4/4 zu streng) und **kein** Block-Marker-
Problem. Es ist ein **technischer Bug in der G3-Cascade-Hook-Reihenfolge:
worker_run wird erst nach Cascade-Subprocess auf `completed` gesetzt,
dadurch sieht der Subprocess seine eigene Tranche nicht.**

## Architekt-Entscheidung (STOPP)

- **Variante A (Engineer-Empfehlung):** technischen Bug fixen. Zwei
  Optionen:
  - **A.1:** Cascade-Subprocess bekommt `--mail-ids` direkt von
    production_worker (Liste der gerade klassifizierten Mail-IDs).
    Sauberster Fix, kein State-Race.
  - **A.2:** Worker setzt `status='completed' + ended_at=now` VOR
    Subprocess-Start. Riskanter (failed cascade hinterlässt
    inkorrektes status), aber minimalinvasiv.

  → Engineer-Empfehlung A.1 — Subprocess kriegt die Mail-IDs als
  expliziten Parameter, fetch_last_tranche_window wird zum Fallback
  für CLI-Direkt-Aufrufe.

- **Variante B (Schwelle lockern):** wäre verfrüht. 4/4 hat noch nie
  echt laufen können — vor Schwellen-Änderung muss A.1 erst zeigen,
  ob 4/4 erreichbar ist.

- **Variante C (Pattern ändern):** analog B — erst Bug fixen, dann
  empirisch entscheiden.

## Sample 3/4-Konsens-Beispiele

Keine vorhanden — Cascade hat für Bauteil-7-Mails 0 Opinions geschrieben.
(Die 180 existierenden validator_opinions in folio.db gehören zu
früheren Tranchen, max feedback_id=794, alle vor 05:06 evaluiert.)
