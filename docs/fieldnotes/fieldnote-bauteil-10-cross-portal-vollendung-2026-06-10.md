# Field-Note — Bauteil 10: Cross-Portal-Vollendung (2026-06-10)

**Quelle:** `direktive-bauteil-10-cross-portal-vollendung-2026-06-10.md`.
**Anlass:** Bauteil 9 trägt für IntraDup, echte Cross-Portal-
Wiedererkennung braucht Stammdaten aus Comparis + Homegate +
lokalen Bilder-Cache. Mobile-Such-Page Cluster-Pille als 9c-
Out-of-Scope nachgezogen.
**Modus:** Council + Folio + Multi-Agent. Drei Substanz-Blöcke,
ein Diagnose-Stopp, **1× 30er-Verifikations-Run** (User-Constraint:
keine 3×30 ohne explizite Erlaubnis).

---

## Branches + Commits

`council/feature/bauteil-10-cross-portal-vollendung`:

| Commit  | Aufgabe | Inhalt |
|---------|---------|--------|
| 595f855 | C2      | comparis_body_parser.py + Dispatcher in ingest_from_mail |
| 82a86f7 | C3      | Bilder-Cache (Schema-ALTER + image_cache.py + Lockfile + Cleanup-Script) |

`folio/feature/bauteil-10-cross-portal-vollendung`:

| Commit  | Aufgabe | Inhalt |
|---------|---------|--------|
| 9061fff | C4      | SearchHit.cluster_members + Mobile-Such-Page Caller |

`multi-agent/feature/bauteil-10-cross-portal-vollendung`:

| Commit  | Aufgabe | Inhalt |
|---------|---------|--------|
| 17c5995 | C1      | Diagnose-Befund Comparis + Homegate |

---

## Architekt-Entscheidungen

**Pattern-Strategie (C1 STOPP bestätigt):**
- Comparis: Body-Parser-Pfad mit Regex auf body_excerpt
- Homegate: Fetch-Pfad (existing Sendgrid-Resolve via
  `CLICK_TRACKER_HINTS` + `allow_redirects=True` — kein neuer Code)
- Mindest-Substanz: 3-von-4 (PLZ + qm + Preis + Adresse) — Bauteil-8-
  Pattern bleibt

---

## Engineer-Entscheidungen

1. **C2 Comparis-Body-Parser-Form:**
   - URL-basierter Block-Split (cgid eindeutiger Key)
   - Dedup via cgid + Prefix-Match (Truncation-Schutz)
   - Multi-Currency (CHF/EUR) in `price_currency`
   - Adress-Format: Pipe-separated ODER PLZ-Stadt-only (FR-Inserate)
   - PLZ direkt aus body — kein `lookup_plz_for_city`-Pfad
2. **C2 Homegate Sendgrid-Resolve:** **NICHT** neu gebaut — existing
   Code `CLICK_TRACKER_HINTS` (`u8489473.ct.sendgrid.net:homegate_basel`)
   + `fetch_with_canonical(allow_redirects=True)` macht das. Wenn
   Verifikation zeigt dass Stammdaten trotzdem fehlen: Folge-Direktive
   10b für og:title-Parser-Erweiterung.
3. **C3 Cache-Form:** synchron beim Ingest, max 3 Bilder/Object,
   5s-Timeout, neutraler User-Agent. Storage
   `~/.folio/cache/images/<oid>/<sha1(url)>.<ext>`.
4. **C3 Lockfile:** fcntl LOCK_EX|LOCK_NB pro Object-ID (Pattern aus
   Bauteil 6 imap_cleanup).
5. **C3 Body-Parser-Pfad bekommt KEIN Cache** — Body hat heute kein
   `og:image`. Nur Fetch-Pfad cached. V2 wenn Body-Parser Bilder
   extrahiert.
6. **C3 object_image_history NICHT in V1** — Vereinfachung.
7. **C4 Reuse `getClusterMembersForAllObjects`** aus Bauteil 9 —
   keine neue Reader-Funktion.

---

## Verifikations-Run (C5, 1× 30er)

### Pre-Snapshot

```
feedback.db:                    330 Mails (id 735-1064)
council.objects:                 22
  immoscout_de_grenzregion:      20
  immowelt_grenzregion:           2
  comparis_basel:                 0 (heute kein Body-Parser → keine Inserate)
  homegate_basel:                 0 (Stammdaten-Schwelle nicht erreicht)
Stammdaten-Vollständigkeit:
  immoscout: 20/20 alle Felder (Body-Parser sauber)
  immowelt:   1/2 (1 leerer Eintrag — Title-Parser-Lücke)
Bilder-Cache:                     0 / 22  (Schema gerade migriert, leer)
Cluster:                         20 / 22 / 1 (multi-member)
Cache-Dir:                       existiert noch nicht
```

### Run (04:30 → 04:52 UTC, Worker 47s + Cascade 21min)

**Worker:**
```
30 mails klassifiziert (id 1065-1094)
Cascade: 90/90 opinions (qwen-validator 30/30 — keine None-Returns)
auto_uebernahme: eligible=2 promoted=2
```

**Council-Ingest (manuell nach Worker):**
```
3 neue Mails verarbeitet (von 37 in Queue, 34 bereits ack'ed):
- id 1090: ImmoScout → INSERT
- id 1086: Comparis "Immobilie mit aktualisiertem Preis"
- id 1082: ImmoScout → INSERT
```

**Bug-Fix during Verification (C5 ad-hoc):**
- Comparis-Mail 1086 wurde im ersten Versuch SKIPPED weil
  `regelwerk.inserat_url_patterns.comparis.ch` nur
  `/immobilien/.../...`-Pattern hatte. Comparis-Click-URL ist aber
  `comparis.ch/comparis/dispatcher/go?cgid=XXX` — kein Match → A3-
  Stammdaten-Filter blockte upsert trotz vollständiger Substanz.
- Fix: Pattern `/comparis/dispatcher/go\?cgid=` ergänzt
  (regelwerk.yaml). Re-Ingest mit mail_ingest_acks-Reset → erfolgreich.

**Comparis-Inserat ingested:**
```
id:      aaee55ffac92...
portal:  comparis_basel
address: Kapellenweg 72, Grindel 4247
qm:      120
price:   330000 CHF
cluster: 47 (single-member — keine Stammdaten-Match mit
              Bauteil-9-Bestand)
```

Vollständige Stammdaten (4/4): PLZ + qm + Preis + Adresse + Currency.

**Stammdaten-Vollständigkeit (Post-Run):**

| Portal                    | Total | has_addr | has_qm | has_price |
|---------------------------|-------|----------|--------|-----------|
| immoscout_de_grenzregion  | 22    | 22       | 22     | 22        |
| immowelt_grenzregion      | 2     | 1        | 1      | 1         |
| **comparis_basel**        | **1** | **1**    | **1**  | **1**     |
| homegate_basel            | 0     | —        | —      | —         |

**Bilder-Cache:** 0/25 Objects mit gecachtem Bild. Cache-Dir existiert
nicht — in diesem Run lief nur Body-Parser-Pfad (3 Mails, alle
ImmoScout/Comparis), und Body-Parser-Pfad hat KEIN cache-Hookup
(Engineer-Entscheidung — body hat heute keine Bild-URLs). Fetch-Pfad
wäre der Cache-Trigger, hat aber in diesem Run nichts ingested.

**Cluster-Stand:**
```
clusters:     21
members:      25
multi-member:  1 (cluster=27 Julius-Meyer-Weg, unverändert von B9)
```

Comparis-Inserat hat eigenen Cluster (47), kein Match mit
ImmoScout-Bestand (unterschiedliche qm/price/PLZ).

---

## Verdikt: Cross-Portal-Vollendung trägt — JA (Comparis), JEIN (Homegate, Bilder, Cross-Match)

**Reife-Begründung:**

1. **C2 Comparis-Body-Parser wirkt:** 1 echtes Comparis-Inserat
   (id 1086 Kapellenweg, Grindel) sauber ingested mit allen 4
   Stammdaten (PLZ 4247, 120qm, 330k CHF, Adresse). Plus
   regelwerk-Pattern-Fix (dispatcher/go?cgid=) hat Bauteil-8-A3-
   URL-Validation-Block aufgelöst.
2. **C3 Bilder-Cache-Schema + Code wirken** — image_urls +
   image_local_paths Spalten sind da, image_cache.py + Lockfile-
   Pattern getestet. **Aber keine Live-Wirkung** weil Fetch-Pfad
   in diesem Run nichts ingested und Body-Parser-Pfad kein cache-
   Hookup hat.
3. **C4 Mobile-Such-Page Cluster-Pille:** Code wirkt (svelte-check
   0 Errors), manuelle Browser-Verifikation steht aus (Cluster=27
   Julius-Meyer-Weg ist der einzige multi-member, müsste in
   Suche „Julius" sichtbar werden).
4. **Cross-Portal-Match:** noch 0 echte Cross-Portal-Matches —
   das einzige neue Comparis-Inserat (Grindel) hat keine Stammdaten-
   Übereinstimmung mit ImmoScout-Bestand (Grindel-PLZ 4247 ist
   nicht im DE-Korridor von 11 ImmoScout-Inseraten).

**Caveats (nicht reife-blockierend):**

- **Homegate-Sendgrid-Resolve wurde nicht live verifiziert** —
  3 ingested mails waren alle ImmoScout/Comparis. Wenn nächste
  Mail-Welle Homegate-Inserate bringt, sollte Sendgrid-Resolve
  greifen (existing Code aus CLICK_TRACKER_HINTS).
- **Comparis-Title-Parser-Lücke:** id 1086 hat title=".q1WYDog
  Emum7q77MPLNw>" (Email-Format-Artefakt). Block-Struktur ist bei
  „aktualisierter Preis"-Mails anders als bei „neue Immobilie".
  Folge-Direktive 10c wenn Title-Substanz wichtig wird.
- **Bilder-Cache Live-Wirkung steht aus.** Erst bei nächstem
  Fetch-Pfad-Ingest (Immowelt + zukünftig Homegate).
- **regelwerk-URL-Pattern-Lücke** wurde live entdeckt + gefixt:
  Comparis-Click-URL hat anderes Pattern als ich beim Bauteil-8-
  A3 angenommen. Lehre: bei neuen Portalen IMMER ein Sample
  durch URL-Pattern-Check ziehen.

**Gesamt:** Comparis-Pfad live verifiziert. Homegate + Bilder
warten auf passende Mail-Welle. Cross-Portal-Match-Quote = 0
in dieser Tranche (zu wenige Portale aktiv).

---

## Was bleibt offen

1. **Folge-Direktive 10b** (Homegate Stammdaten-Erweiterung) wenn
   Verifikation zeigt dass Sendgrid-Resolve allein nicht reicht.
2. **Folge-Direktive 10c** (Comparis-Title-Parser-V2) wenn Title-
   Substanz für UI/Cluster-Anzeige wichtig wird.
3. **Body-Parser-Bilder** (V2) wenn Mail-HTML-Body Bild-URLs
   enthält (heute nur Body-Excerpt verfügbar).
4. **Mobile-Such-Page Browser-Verifikation** — manuell mit
   Cluster=27 (Julius-Meyer-Weg) prüfen.

---

## Stand

3 Comparis-Body-Parser-Pfad-Substanz verifiziert. Plus 1 regelwerk-
URL-Pattern-Bug live entdeckt + gefixt (Comparis-Click-URL).
Bilder-Cache + Homegate-Sendgrid-Resolve warten auf passende Mail-
Welle. 4 Commits über 3 Repos bereit für FF-Merge:
- multi-agent: 1 Commit (C1 Diagnose)
- council: 3 Commits (C2 Comparis-Parser + C3 Bilder-Cache +
  ad-hoc regelwerk-Pattern-Fix wird mit-committed)
- folio: 1 Commit (C4 Mobile-Pille)

---

## Out of Scope (aus Direktive + Engineer)

- 3×30er-Runs (User-Constraint, Folge-Direktive nötig)
- object_image_history Append-only-Tabelle (V2 wenn nötig)
- Generic-Body-Parser für alle Portale (Verbot)
- Bilder-Cache asynchron (V2)
- Body-Parser-Bilder (V2 wenn Body-Substanz Bilder enthält)
- Newhome/weitere Portale Body-Parser (V2)
- Homegate-spezifische og:title-Erweiterung (Folge-Direktive 10b
  wenn Verifikation zeigt dass Stammdaten fehlen)
