# Field-Note — Bauteil 13: Hygiene-Bundle (Review-Fixes) (2026-06-10)

**Quelle:** `direktive-bauteil-13-hygiene-bundle-2026-06-10.md`.
**Anlass:** External Review (`folio-council-multiagent-review-2026-06-10.md`)
identifizierte 14 Befunde. Architekt-Freigabe für Bundle aus 4 kleinen
Fixes: C2 (Cluster-Currency-Bug), H1 (Status-Inheritance-Drift), C1
(Cross-DB-Doku-Verstoß), M1 (Council-Repo-Doku-Schuld).
**Modus:** Council + Multi-Agent (Docs). Eine Direktive, kein Diagnose-
Stopp.

---

## Branches + Commits

`council/feature/bauteil-13-hygiene`:

| Commit  | Befund | Inhalt |
|---------|--------|--------|
| da31c11 | C2 | currency-aware cluster match (NULL-tolerant) + price_currency in 2 Aufrufern durchreichen |
| 5879aad | H1 | status-inheritance source-Filter (analog Notes/Workflow seit B11) |
| a88fef9 | M1 | pyproject.toml name+description, README komplett neu (Council V2), ingest_from_mail.py Modul-Docstring entstauben |

`multi-agent/feature/bauteil-13-hygiene`:

| Commit  | Befund | Inhalt |
|---------|--------|--------|
| 4ae9f43 | C1 | Cluster-Inheritance als Council-side Cross-DB-Ausnahme dokumentieren |

4 atomare Commits, einer pro Befund.

---

## Engineer-Entscheidungen

1. **C2 Currency-NULL-tolerant.** SELECT-Bedingung
   `(price_currency = ? OR (price_currency IS NULL AND ? IS NULL))`.
   Erlaubt NULL-Cluster (Comparis-Parser-Lücke) als eigene Klasse,
   ohne sie gegen typed-Cluster zu mergen.
2. **C2 Kein Backfill.** Pre-Run-DB-Snapshot: 23 Cluster, alle EUR
   (auch Comparis-CHF-Inserate aus B10 sind falsch als EUR
   gespeichert). Engineer-Wahl: nicht backfillen, weil alle existing
   Cluster heute single-member oder gleichwährung sind — der Bug
   wirkt erst ab Cluster mit ≥2 Mitgliedern. Field-Note dokumentiert
   das.
3. **B1 Default-Fallback `or "EUR"`.** `ins.get("price_currency") or
   "EUR"` statt `ins.get("price_currency", "EUR")` — letzteres
   würde bei key=None nicht greifen. ImmoScout-Parser ohne currency-
   Output bekommt damit EUR (Deutschland), Comparis CHF/EUR explizit.
4. **H1 1-Zeilen-Fix exakt analog Notes/Workflow.** Copy-Paste-Pattern
   beibehalten — Helper-Abstraktion ist Bauteil 14 (synergisch mit
   UI-Provenance-Pille).
5. **C1 Verboten-Liste differenziert.** „council → folio.db ist
   verboten ausser Cluster-Inheritance" statt komplett-erlaubt —
   neue Anwendungsfälle brauchen weiterhin Architekt-Entscheid +
   Eintrag.
6. **M1 README-Bauteile 7–13, nicht 0–6.** Bauteile 0–6 leben in
   multi-agent/-fieldnotes; ein Council-README sollte die Council-
   spezifischen Bauteile zeigen (mit Verweis auf Vorarbeit).

---

## Verifikation

### B1 Currency-Match (deterministischer Test)

```
Test 1: EUR-Cluster=48, CHF-Cluster=49 (separat) ✓
Test 2: EUR-Re-Match auf cluster=48 (kein new) ✓
Test 3: NULL-currency-Cluster matched konsistent (cluster=50) ✓
Test 4: NULL-Cluster (50) und EUR-Cluster (51) separat ✓
```

Test-PLZ 99999/99998 + Cleanup nach Test → keine Daten-Pollution.

### B2 SELECT-Syntax + Sanity

```
OK B2 SELECT-Syntax akzeptiert (dummy-Query: 0 rows)

Folio object_status_override source-Verteilung:
  cluster_match: 1
  user_action: 21
```

Sanity-Befund: das eine cluster_match-Erbe-Row wäre vom alten
SELECT als Inheritance-Quelle gezogen worden, vom neuen
source-Filter wird es korrekt geskippt. Fix wirkt produktiv.

### Smoke ohne 30er-Run

Alle 30 letzten feedback-Mails sind bereits ack'ed (Multi-Agent-
Pipeline hat sie schon gelaufen). Stattdessen direkter Code-Level-
Smoke der modifizierten Funktionen — deterministisch, ohne
MacBook-Last, ohne IMAP/Web-Latenz. Engineer-Constraint-konform
(1×30er-Default, kein 3×30er).

### B3 + B4 Doku-Verifikation

- `cross-db-write-ausnahmen.md`: rendert sauber, neue
  Cluster-Inheritance-Sektion zwischen Council-side-lokal und
  „Wann erweitern", Tabelle korrekt.
- `council/README.md`: rendert sauber, Bauteile-Tabelle 7–13
  vollständig, V1-Hinweis am Ende als historische Notiz.
- `ingest_from_mail.py`: Modul-Docstring importierbar (Smoke-
  Tests laufen ohne Syntax-Fehler durch den Modul-Import).

---

## Verdikt: trägt — ja

Alle vier Befunde nachvollziehbar gefixt, Smoke ohne Schwächen.
Code-Pfade verifiziert ohne 30er-Run-Belastung (deterministischer
Unit-Test plus folio-DB-Read-only-Sanity).

Wurzel-Pattern aus dem Review-Wurzel-Pattern-4 („Architektur-
Versprechen unaufgelöst"): C1 ist ein klassisches Beispiel — der
Cross-DB-Write wurde in B9/B11 gebaut, aber die zentrale Doku
nicht mit-gepflegt. Lehre dokumentiert in Memory (`feedback_ack
_statt_cross_db_write.md`): bei jeder neuen Cross-DB-Anwendung
IMMER `cross-db-write-ausnahmen.md` im selben Commit mit-fixen.

---

## Stand

7 Commits insgesamt (3 council + 1 multi-agent + dieser Field-
Note-Commit + 2 zukünftige Doku-Tippfehler-Fixes wenn nötig).
Bereit für FF-Merge + Push nach User-Approval.

---

## Out of Scope (aus Direktive + Engineer)

- M2 Inheritance-Helper-Abstraktion → Direktive 14
- H2 + M4 UI-Provenance-Pille → Direktive 14
- H3 Body-Parser-Interface, H4 Dual-Pipeline-Trennung, H5
  voice_consensus → Direktive 15 (strategische Diskussion)
- M3 Degenerated-Cluster NULL-Skip → Direktive 15
- Backfill existing Comparis-CHF-Cluster auf currency='CHF' →
  bewusst skip
- 30er-Run-Verifikation → User-Constraint + deterministischer
  Code-Test ist substantiell pass
