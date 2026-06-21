# Befund — D3: user_rankings Reader-Diagnose (Bauteil 11)

**Datum:** 2026-06-10. **Stopp-Punkt vor Variante-Wahl.**

## 7 Reader-Stellen für user_rankings (folio.db)

| # | Funktion / Stelle                                  | Datei                                                                  |
|---|----------------------------------------------------|------------------------------------------------------------------------|
| 1 | `getUserTopRanksFor(userId)`                       | `folio/src/lib/server/folio-db/reader.ts:433-449`                      |
| 2 | `getConsensusReadyObjectIds()`                     | `folio/src/lib/server/council-db/reader.ts:851-869`                    |
| 3 | `readAllCouncilObjects(sort='mine')`               | `folio/src/lib/server/council-db/reader.ts:1268-1320`                  |
| 4 | Mobile Meine-10-Page Loader                        | `folio/src/routes/(council)/council/mobile/meine-10/+page.server.ts`   |
| 5 | Desktop Council-List Loader                        | `folio/src/routes/(council)/council/+page.server.ts`                   |
| 6 | Mobile Detail-View Loader                          | `folio/src/routes/(council)/council/mobile/[id]/+page.server.ts`       |
| 7 | Kampagne/Hauskauf-Auto-Trigger Loader              | `folio/src/lib/server/kampagne/loader.ts`                              |

Plus Writer-Stellen (nicht refactor-betroffen): POST
`/api/council/me/rankings` + `insertUserRankingBatch()`.

**Cross-DB-Reader im Council-Repo:** keine. Council nutzt nur
ACK-Pattern, kein user_rankings-SQL.

## Variante-Risiko-Schätzung

### Variante A — `user_rankings.cluster_id` Schema-Migration

- Jede der 7 Reader-Stellen muss cluster-aware werden.
- Komplikation: rank ist limitiert auf Top-10. Bei Cluster-Wachstum
  müsste canonical-Member-Selektion (z.B. ältester Member) erfolgen,
  sonst zeigt Top-10 mehrere Cluster-Brüder doppelt.
- Plus: Konsens-Check (`getConsensusReadyObjectIds`) muss cluster-
  basiert sein — beide User müssen rank≤3 auf demselben Cluster
  haben, nicht auf einem Cluster-Mitglied.
- **Risiko HOCH** — strukturell sauber, aber 7 Stellen × Refactor
  = viel Oberfläche, potenzielle Regressions in Bauteil-7+
  Hauskauf-Workflow.

### Variante B — UI-Indikator (Reader-Erweiterung)

- Keine Schema-Migration, keine Writer-Änderung.
- Nur 2 Reader-Stellen erweitern: `readAllCouncilObjects` +
  `searchCouncilObjects` (beide computen `clusterMembers` schon
  via `getClusterMembersForAllObjects`).
- Neue Property `cluster_rank_neighbor: {object_id, rank, user_id}
  | null` pro Item — gefüllt wenn ein Cluster-Bruder einen rank≠null
  hat und das Item selbst keinen rank hat.
- Mobile-ObjectCard: kleine Pille „Bruder auf Top-10 #X" analog
  Bauteil-10-C4-Cluster-Pille.
- **Selbstheilend:** wenn das neue Cluster-Mitglied bessere
  Substanz hat (mehr Bilder, bessere Adresse), User kann manuell
  via Mobile-Meine-10 das alte Object aus Top-10 entfernen + neues
  Mitglied einsetzen.
- **Risiko NIEDRIG** — pragmatisch, kein Schema-Refactor.

## Engineer-Empfehlung: Variante B

Begründung:
1. 7 Reader-Stellen = hoch-riskanter Refactor für Variante A.
2. Variante B löst die UI-Sichtbarkeit komplett — User sieht den
   Bruder-Rang, kann handeln.
3. Konsens-Check (`getConsensusReadyObjectIds`) bleibt funktional
   — User entscheidet aktiv pro Object.
4. Selbstheilend statt strukturell starr — passt zum bisherigen
   Hauskauf-Workflow-Stil (User-Entscheidung pro Object, kein
   Auto-Übertrag).

## Architekt-Stopp

Variante B (Engineer-Empfehlung) oder Variante A (Architekt-
Tendenz aus Direktive)?

Bei Variante A: Engineer dokumentiert pro Reader-Stelle den
Refactor (Verbot: keine Reader-Stelle blind anpassen).
Bei Variante B: Engineer baut nur Reader-Erweiterung (~2 Files +
1 UI-Komponente).
