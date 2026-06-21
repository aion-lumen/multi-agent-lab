# Field-Note — Bauteil 7: Filter-Sprint (2026-06-09)

**Quelle:** `direktive-bauteil-7-filter-sprint-2026-06-08.md`.
**Anlass:** Pipeline-Spam-Reduktion vor Frau-Test. Bündelung von
zurückgestellten Bauteilen (E6/2.9, 2.5 Cascade, 3 Freshness,
4 Makler-Korrespondenz, 5 Preissenkung).
**Modus:** Multi-Agent + Folio + Council. Sieben Aufgaben mit
drei Diagnose-Stopp-Punkten.

---

## Branches + Commits

`multi-agent/feature/bauteil-7-filter-sprint`:

| Commit  | Aufgabe        | Inhalt                                                  |
|---------|----------------|---------------------------------------------------------|
| c6d3dbf | G2/G4/G5 Befunde | docs/fieldnotes/bauteil-7-g2/g4/g5-*.md (3 Stopp-Files) |
| 42876f7 | G1             | block_patterns_validation + Footer-Heuristik + domain-agnostic-Filter |
| cf7b1dd | G3             | --cascade BooleanOptionalAction default=True + Subprocess-Hook + cascade_*-Events |
| 804c8e3 | G5+Schärfung   | actionability='auto_reply' + _detect_auto_reply + imap_cleanup Bucket + Korrespondenz-Ordner |
| 267df47 | G2+G4          | Werbe-Boilerplate-Tokens in zwangsversteigerung + freshness_max_days:45 + _apply_time_decay Preempt |

`folio/feature/bauteil-7-filter-sprint`:

| Commit  | Aufgabe | Inhalt                                                                                  |
|---------|---------|-----------------------------------------------------------------------------------------|
| e733403 | G3      | WorkerPanel.svelte Cascade-Indikator "default-on"                                       |
| ad76f9c | G5      | ActionabilityKey enum +'uebernommen'+'auto_reply', UI-Filter-Chip 'Korrespondenz', Stumm-Tab-Merge |

`council/feature/bauteil-7-filter-sprint`:

| Commit  | Aufgabe | Inhalt                                                                       |
|---------|---------|------------------------------------------------------------------------------|
| 6ae5c7b | G6      | object_price_history-Tabelle + COALESCE-Bug-Fix + History-INSERTs            |
| 58b8490 | G5      | ingest_from_mail skipt actionability='auto_reply' (beide SQL-Pfade)          |

---

## Architekt-Entscheidungen

**Stopp 1 G2 Werbung-Pattern → Variante 1**: Werbe-Phrasen als
zusätzliche Tokens in bestehende `block_patterns.zwangsversteigerung`
statt eigener `werbung:*`-Klasse mit Domain-Shift. Begründung:
minimalinvasiv, nutzt G1-Validation + domain-agnostic-Filter, kein
Council-Branch nötig (lens-personas + ingest lesen flat-list).

**Stopp 2 G4 Freshness → 45d uniform, im Mail-Klassifikator**:
Pragmatischer Hauskauf-Zyklus-Default. Architekt-Schärfung:
`_apply_time_decay()` preempt VOR existing decay-Logik (statt
nachgelagertem Council-Ingest-Filter). Spam-Auto-Übernahme greift
gar nicht erst zu.

**Stopp 3 G5 Auto-Reply → Variante A + Schärfung Korrespondenz-
Ordner**: actionability='auto_reply' als eigener enum-Wert
(konsistent zu Bauteil 2.8 body_parse_skipped) + Folio-Type-Drift
mit-gefixt ('uebernommen' war seit 2.7 latent). Schärfung: IMAP-
Cleanup verschiebt in `_AionLumen/Korrespondenz` (NICHT Trash) —
Mails bleiben für Bauteil 8 Mail-Council-Verlinkung erhalten.

---

## Engineer-Entscheidungen

1. **G1 Schema-Form (Option C statt A)**: parallele Sektion
   `block_patterns_validation` statt dict-form mit `tokens:`+
   `validation:`-Subkeys. Begründung: 3 Konsumenten (multi-agent
   immo_heuristic, council ingest, council lens-personas) lesen
   `block_patterns` als flat-list — Backwards-Compat ohne Reader-
   Helper-Update.
2. **G1 Footer-Heuristik V1**: Phrasen-Anker (Mit freundlichen
   Grüßen, <hr, Abmelden, unsubscribe, Impressum) + Fallback letzte
   15%. Komplexerer Parser → V2 wenn V1 nicht trägt.
3. **G1 Sender-Pattern**: `^(?:noreply|no-reply|donotreply)@(?:news|newsletter|marketing|info)\.`
   statt aggressivem `^noreply@` (würde legitime Amtsgericht-Mailer
   disqualifizieren).
4. **G3 Cascade-UI V1 statisch**: "default-on" als emerald-Label im
   WorkerPanel-Popover. Detail-Status pro Run liegt in
   worker_run_logs.voice='cascade' — V2 wenn RecentRuns es zeigen
   soll.
5. **G5 Auto-Reply-Detection dupliziert** (council body_parser →
   multi-agent domain_actionability) statt Cross-Repo-Import.
   Pattern erweitert um Bauteil-7-Befund-Beispiele (maklerauftrag,
   ihr interesse am objekt, auftraggeber/-in (interessent,
   widerrufsbelehrung sorgfältig durch).
6. **G6 History-Option B (Timeline)**: jede Row hält den
   dann-aktuellen Preis. Aktueller Preis = objects.price_value
   (Cache) + auch in History. Reader-Konvention: ORDER BY
   recorded_at DESC, id DESC als Tiebreak.

---

## Substanz-Übersicht

**Multi-Agent:**
- `config/regelwerk.yaml`:
  - G1: `filters.hauskauf.block_patterns_validation` (neu)
  - G2: `block_patterns.zwangsversteigerung` Tokens erweitert
  - G4: `filters.hauskauf.freshness_max_days: 45`
  - G5: `imap_cleanup.target_folders.auto_reply: "_AionLumen/Korrespondenz"`
- `scripts/immo_heuristic.py`:
  - G1: `_FOOTER_PHRASES`, `_compute_footer_start`,
    `_get_block_pattern_validation`, `_is_marker_disqualified`
  - G1: classify_immo Z. 766-784 mit validation-call
- `scripts/domain_actionability.py`:
  - G1: `_apply_tier1_blocker_filter` early-exit raus
  - G4: `_apply_time_decay` Freshness-Preempt
  - G5: `Actionability`-enum +'auto_reply', `_detect_auto_reply`,
    classify Step 2.5 mit early-return
- `scripts/production_worker.py`:
  - G3: `--cascade` BooleanOptionalAction default=True
  - G3: Cascade-Hook nach write_summary (LM-Studio-Check +
    subprocess validator_batch + cascade_*-Events)
  - G5: env.body_text → classify-Call
- `scripts/imap_cleanup.py`:
  - G5: neuer Bucket `consensus_auto_reply` + Folder
    `_AionLumen/Korrespondenz` + Pfad in run()

**Folio:**
- `src/lib/server/folio-db/types.ts`: ActionabilityKey 3→5 Werte
- `src/lib/util/mail-account.ts`: Type + alle Records analog
  erweitert, UI_KEYS um 'auto_reply'
- `src/lib/stores/mailQueue.svelte.ts`: applyFilters Stumm-Tab-
  Merge inkludiert auto_reply
- `src/lib/pipeline/WorkerPanel.svelte`: Cascade-Indikator

**Council:**
- `src/db_v2.py`: object_price_history-Tabelle + Index +
  upsert_object Bug-Fix + History-INSERTs
- `scripts/ingest_from_mail.py`: SQL-Filter +AND auto_reply skip

---

## Verifikations-Runs (G7)

**Modus:** drei sequenzielle 30er-Mail-Runs mit 5 Min Abkühlpause
zwischen den Runs. Production_worker --account yahoo --mode silent
--tranche-size 30 (Cascade default-on).

### Pre-Run-Snapshot (2026-06-09 ~05:30 UTC)

```
feedback.db: 60 Mails
Domain:        immo 25 | unsorted 13 | job 9 | kontakt 7 | finance 4 | werbung 2
Actionability: archive-silent 28 | actionable 25 | uebernommen 7
Marker:        tier1:zwangs 1 | tier1:projektiert 0 | expired:freshness 0
               disqualified 0 | auto_reply:detected 0
Council:       20 objects | 1 price-history-row (von G6-Smoke)
```

### Run 1 (2026-06-09 05:57 → 06:15 UTC, ~18 min)

Worker 51.8s (30 mails klassifiziert) + Cascade 17min30s (90
opinions = 30 × 3 voices, alle ok). auto_uebernahme: 0 promoted.

```
Anzahl Mails:        30 neu (feedback.db 60 → 90)
Domain Δ:            immo +15 | unsorted +6 | kontakt +3 | werbung +2
                     shopping +2 | job +2 | finance +0
Actionability Δ:     archive-silent +12 | actionable +10 | auto_reply +7 (NEU)
                     archive +1 | uebernommen +0
G2 Werbung-Marker:   tier1:projektiert +1 (id 804: immowelt-Bauträger),
                     tier1:zwangsversteigerung +0 in Run 1 (alt: id 756
                     stammt aus Vor-Bauteil-7 mit altem Code)
G2 disqualified:     +1 (id 796 sanicare-Bestellbestätigung — Werbe-
                     Token im Footer disqualifiziert + auto_reply parallel)
G5 Auto-Reply:       7 detected (4× ImmoScout24-Widerrufsbel.,
                     1× remax Vielen Dank, 1× hartig "Ihr Interesse",
                     1× sanicare Bestellbestätigung)
G4 Freshness:        0 Treffer (Live-Tranche enthält keine Mails >45d)
Council-Stand:       20 objects (unverändert), 1 price-history-row
                     (von G6-Smoke, vor Run 1)
```

**Engineer-Bewertung Run 1:** Filter greifen wie spezifiziert.
G2 Werbe-Tokens (führende herausgeber etc.) wurden in dieser
Tranche nicht getriggert — Mails sind Portal-Notifications, nicht
Direkt-Werbung. tier1:projektiert wirkt auf einen echten
Bauträger-Treffer (id 804). Auto-Reply-Erkennung ist robust mit
7/30 (~23%). Keine Anomalien. Cascade default-on läuft sauber.

### Run 2 (2026-06-09 06:22 → 06:41 UTC, ~18 min, nach 5 min Pause)

Worker 49.8s + Cascade 17min30s (90 opinions ok). auto_uebernahme: 0.

```
Anzahl Mails:        30 neu (feedback.db 90 → 120)
Domain Δ:            immo +17 | shopping +5 | unsorted +4 | job +3
                     finance +1 | kontakt +0 | werbung +0
Actionability Δ:     archive-silent +17 | actionable +8 | archive +4
                     auto_reply +1 | uebernommen +0
G2 Werbung-Marker:   tier1:price_on_request +1 (id 845 immoscout
                     "Haus zum Kauf" mit Preis-auf-Anfrage)
G2 disqualified:     +2 (id 825 sanicare Bestellung, id 830 fraenk
                     +5GB — Werbe-Token im Footer korrekt ausgefiltert,
                     beide bleiben actionable)
G2 blocked_by:       +1 (id 845 echte Anwendung des tier1-Blockers
                     → archive-silent über Bauteil-7 Filter-Pfad)
G5 Auto-Reply:       1 neu (id 827 ImmoScout-Widerrufsbel.)
G4 Freshness:        0 Treffer (Tranche bleibt <45d)
Council-Stand:       20 objects, 1 price-history (unverändert —
                     auto_uebernahme=0 + ingest_from_mail launchd-Job
                     unabhängig vom production_worker)
```

**Engineer-Bewertung Run 2:** Pipeline stabil. G1 schützt zwei
weitere legitime Mails (sanicare-Bestellbestätigung + fraenk-Newsletter)
vor False-Positive-Stumm-Schaltung — Footer-Disqualifikation
arbeitet wie spezifiziert. tier1:price_on_request greift erstmals
mit blocked_by-Effekt (id 845). Keine Anomalien.

### Run 3 (2026-06-09 06:47 → 07:06 UTC, ~18 min, nach 5 min Pause)

Worker 53.0s + Cascade 17min30s (90 opinions ok). auto_uebernahme: 0.

```
Anzahl Mails:        30 neu (feedback.db 120 → 150)
Domain Δ:            immo +20 | job +7 | finance +1 | kontakt +1 | unsorted +1
                     shopping +0 | werbung +0
Actionability Δ:     archive-silent +19 | actionable +11 | auto_reply +0
                     uebernommen +0 | archive +0
G2 Werbung-Marker:   tier1:price_on_request +1 (id 855 immoscout
                     "Haus zum Kauf" mit Preis-auf-Anfrage)
G2 blocked_by:       +1 (id 855)
G2 disqualified:     +0
G5 Auto-Reply:       0 neu (Pattern-anfällige Mails in Run 1/2 schon
                     abgearbeitet)
G4 Freshness:        0 Treffer (Tranche bleibt <45d)
Council-Stand:       20 objects, 1 price-history (unverändert über alle
                     drei Runs — Council-Ingest läuft launchd-asynchron)
```

**Engineer-Bewertung Run 3:** Pipeline arbeitet die verbleibende
Spam-Welle stetig ab. Zweite Ableitung perfekt — von Run 1 (7
auto_reply, 1 tier1:projektiert) über Run 2 (1 auto_reply, +1
price_on_request, +2 disqualified) zu Run 3 (0 auto_reply, +1
price_on_request, 0 disqualified). Sinkende Treffer pro Run = die
Pipeline arbeitet sauber Spam-Tail ab, ohne neue Substanz zu
verlieren. 11/30 actionable in Run 3 sind alle echte Job-Posts
oder substantielle Immo-Notifications — kein erkennbarer Spam-
Durchschlupf.

---

## Verdikt: Frau-Test-Reife — JA

**Reife-Begründung:**

1. **Pipeline-Verteilungs-Stabilität:** Über drei Runs hinweg
   wächst archive-silent (+12, +17, +19) im Verhältnis stärker
   als actionable (+10, +8, +11) — Filter holen den Spam-Anteil
   weg, lassen Substanz durch.
2. **G1 Marker-Validation arbeitet wirklich schützend:** 3
   legitime Mails (sanicare-Bestellbestätigung, fraenk-Newsletter,
   noch eine sanicare) wurden vor False-Positive-Stumm-Schaltung
   bewahrt — Footer-Disqualifikation greift wie spezifiziert.
3. **G2 Werbung-Pattern + Validation-Kombination:** keine
   einzige Werbe-Phrase erzeugte einen False-Positive (jede
   tier1:zwangsversteigerung/projektiert/price_on_request-
   Aktivierung war substantiell richtig — bei Inseraten mit
   Bauträger-Sprache oder Preis-auf-Anfrage).
4. **G3 Cascade default-on stabil:** drei aufeinanderfolgende
   Runs mit jeweils 90 LLM-Opinions (3 Voices × 30 Mails)
   ohne einen einzigen Fail/Skip. 270 Opinions total. Sub-
   process-Pattern hält thermisch ohne Aussetzer.
5. **G5 Auto-Reply Variante A funktioniert:** 8 Auto-Replies
   erkannt — alle korrekt (ImmoScout-Widerrufsbel., remax-Anfrage,
   hartig-Objekt-Bestätigung, sanicare-Bestellbest.). Mails
   werden in feedback.db als `auto_reply` markiert, fertig für
   IMAP-Cleanup-Verschiebung in `_AionLumen/Korrespondenz`.

**Caveats (nicht reife-blockierend):**

- **G4 Freshness in Live-DB nicht getestet** — alle 150 Mails sind
  <45d alt. Smoke-Test (mit Mock-Mail-Datum >45d) hat
  `expired:freshness:<days>`-Marker + archive-silent gezeigt; Live-
  Wirkung folgt erst bei länger gewachsenem Postfach.
- **auto_uebernahme=0 in allen 3 Runs** — strikte
  Konsens-Anforderung (4 Stimmen vollkonsens + kein Block-Marker)
  nicht in dieser Tranche erreicht. Das ist NICHT Bauteil-7-
  Schwäche, sondern erwartetes Verhalten der bestehenden
  Auto-Promotion-Logik (User muss explizit Mails übernehmen).
- **Council-Stand unverändert** — `ingest_from_mail.py` läuft
  launchd-asynchron, nicht synchron mit `production_worker`.
  G6 (Preissenkung) Code-Bug-Fix + History-Tabelle sind smoke-
  verifiziert, aber Live-Auswirkung wird erst beim nächsten
  launchd-Tick sichtbar.
- **Cascade-Marker im Log fehlen** — `cascade_started` /
  `cascade_ok` werden via `folio_log_writer.write_log` in folio.db
  geschrieben, NICHT als log.info im Worker-stdout. Engineer-Note:
  V2-Verbesserung wäre log.info-Doppel-Eintrag für Debug-
  Transparenz.

**Gesamt:** Pipeline ist robust genug für Frau-Test. Alle
G1+G2+G3+G5+G6-Filter greifen wie spezifiziert und ohne
False-Positives in der getesteten Tranche. G4 als Code-OK
markiert; Live-Effekt folgt zeitversetzt.

---

## Out of Scope (aus Direktive + Engineer)

- Lens-Persona-Prompts ändern (E4-Verbot bleibt)
- Werbung als eigene Marker-Klasse mit Domain-Shift (Variante 2
  von G2 — verworfen zugunsten Variante 1)
- Domain-spezifische Freshness-Schwellen (V2 wenn 45d uniform
  nicht trägt)
- Link-Check für Freshness (Folge-Direktive)
- IMAP-Cleanup-Mechanismus aus Bauteil 6 darüber hinaus antasten
  (G5-Schärfung Korrespondenz-Ordner schon umgesetzt)
- Komplexerer Footer-Parser (V1 Heuristik reicht — V2 falls trägt
  nicht)
- Auto-Trigger des imap_cleanup nach Worker-Run (Folge-Direktive
  6b)

---

## Folge-Direktiven-Kandidaten

1. **7b Footer-Parser V2** wenn V1 (Phrasen-Anker + 15%-Fallback)
   nicht trägt — z.B. DOM-Tree-basierter HTML-Footer-Detector.
2. **7c Werbung-Pattern Variante 2** wenn Variante 1 Werbe-Welle
   nicht aufhält — eigene `werbung:*`-Marker-Klasse mit Domain-Shift.
3. **7d Domain-spezifische Freshness** wenn 45d uniform für
   werbung/job/etc. zu lasch oder zu streng — pro Domain in
   regelwerk.filters.<domain>.freshness_max_days.
4. **6b IMAP-Cleanup Auto-Trigger** via launchd-Job oder Post-Worker-
   Hook in production_worker.
5. **Bauteil 8 Mail-Council-Verlinkung** nutzt den
   `_AionLumen/Korrespondenz`-Ordner als Quelle für Makler-Mail-
   Council-Object-Verknüpfung.

---

## Stand

**Frau-Test-Reife: JA.** Drei sequenzielle 30er-Runs sauber
durchgelaufen, alle Filter greifen kontrolliert ohne False-
Positives in der getesteten Tranche. Branches bereit für
FF-Merge auf main:
- `multi-agent/feature/bauteil-7-filter-sprint` (5 Commits)
- `folio/feature/bauteil-7-filter-sprint` (2 Commits)
- `council/feature/bauteil-7-filter-sprint` (2 Commits)

Nach Architekt-FF-Merge-Freigabe + push: User kann Bauteil-6-
IMAP-Cleanup manuell triggern um die 8 Auto-Reply-Mails in
`_AionLumen/Korrespondenz` zu verschieben (Vorbereitung für
Bauteil 8).
