# Field-Note — Bauteil 16: Entsorgung + Ehrlichkeit (2026-06-10)

**Quelle:** Architektur-Schnitt Direktive 16.

---

## Was

1. **`voice_consensus.py` gelöscht** — null Aufrufer; E4 in `regelwerk.yaml`
   beschreibt jetzt faktischen Schutz (`mode: manual` + `auto_uebernahme`
   4-voice-Konsens). `protection_clause`-Versprechen entfernt.

2. **H4-Docstrings:** `immo_heuristic.py` + `domain_actionability.py` —
   „komplementär, nicht ablösend".

3. **Archiv:** `pilot_worker.py` → `scripts/_archive/`.
   (`production_worker_v013.py` war nicht im Tree.)

4. **`consolidate_clusters_bauteil9.py`:** „ad-hoc, manual trigger only".

5. **`mail-mock.ts`:** Kommentar auf Dev-Fixture-Status aktualisiert.

6. **SUPERSEDED-Header** auf B9/B11 Field-Notes (Push-Inheritance).

---

## Grep-Proof

- `import voice_consensus` / `from voice_consensus`: 0 Treffer.
- `regelwerk.yaml` lädt via `regelwerk_reader` / Folio `loadRegelwerk`.
