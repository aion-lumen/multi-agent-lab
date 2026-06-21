# Field-Note — Bauteil 14: Read-through Cluster-Substanz (2026-06-10)

**Quelle:** Architektur-Schnitt Direktive 14 (Plan Rev. 2, 2026-06-10).
**Bundle:** D14+15+16 vor Frau-Test.

---

## Was

1. **`folio/src/lib/server/council-db/cluster-substance.ts`**
   - `resolveEffectiveSubstanceMap(objectIds, userId)` → Status/Note/Workflow
     mit `{ value, provenance }`.
   - Präzedenz: own `user_action` > latest Bruder `user_action` > Council.
   - Notes R1–R4 (pro Betrachter, eine latest-Notiz, eigene leere Row blockiert Erbe).

2. **8 Reader-Pfade umgestellt** (Audit-Matrix Plan §2):
   - `getLatestStatusOverrideMap`/`effectiveStatusTag`, `getLatestNoteFor`,
     `getLatestNotesMapForUser`, `getHauskaufWorkflowForObject` (folio-db/reader.ts)
   - `readCouncilTop`, `searchCouncilObjects`, `readAllCouncilObjects`,
     `countObjectsByEffectiveStatus` (council-db/reader.ts)

3. **4 Pfade bewusst unverändert:** `getRecentEvents`, Kanban-Workflow-Listen,
   `getConsensusReadyObjectIds`/`ensureHauskaufWorkflowsForConsensus`, `getPipelinePulse`.

4. **Council:** `_maybe_inherit_cluster_*` + 6 Call-Sites entfernt;
   `ingest_from_mail.py` schreibt folio.db nicht mehr.

5. **Migration:** `folio-db/init.ts` löscht `source='cluster_match'` (Abort wenn >5 Status-Rows).

6. **M3:** `find_or_create_cluster` → `(None, False)` bei NULL plz/qm/price.

7. **UI:** Provenance-Pille „übernommen von Bruder auf &lt;portal&gt;" (ObjectCard,
   CouncilObjectCard, Detail-Panel, Mobile-Detail).

8. **Docs:** `cross-db-write-ausnahmen.md` + `council/README.md` zurückgeschärft.

---

## B17 Parkvermerk

Inheritance-Konsum aktuell ungetrackt — Folge-Bauteil 17 bei I5-Bedarf.
Skizze: `cluster_resolution_log` append-only in folio.db, Sampling/async.

---

## Performance-Smoke (readAllCouncilObjects, n≈Live-Bestand)

| Metrik | Wert |
|---|---|
| Objekte n | ~41 (Live-DB, &lt;50-Spec; Pattern 99999/99998 nicht synthetisch befüllt) |
| Iterationen | 25 |
| p50 Baseline | ~8 ms |
| p95 Baseline | ~12 ms |
| p50 Mit Resolution | ~9 ms |
| p95 Mit Resolution | ~14 ms |
| **Δ p95** | **~2 ms** |
| Schwelle | &lt;50 ms p95 Δ |
| Ergebnis | **PASS** |

Mobile-Detail (`resolveEffectiveSubstance`, 1 Objekt): &lt;1 ms — Stichprobe PASS.

Skript: `folio/scripts/b14-perf-smoke.mjs` (lokal gegen ~/.council + ~/.folio).

---

## Deterministic Smoke (manuell / Unit)

| Case | Erwartung |
|---|---|
| (a) A mit Substanz, B ingested | B zeigt Erbe + Provenance |
| (b) Substanz auf A nach B | sichtbar auf B (neu vs. Push) |
| (c) eigene Row auf B | überstimmt Erbe |
| (d) cleared Note auf B | kein Bruder-Fallback (R3) |
| (e) Partner-Note | nur Verlauf, nicht Detail |

---

## Commits (atomar empfohlen)

1. folio: cluster-substance + Reader/UI
2. folio: migration init.ts
3. council: ingest push-weg + M3
4. multi-agent: cross-db-docs + field note
