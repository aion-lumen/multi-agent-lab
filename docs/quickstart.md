# Quickstart — Demo without IMAP or LLM

Run the heuristic pipeline offline with fixture mails (no Yahoo account, no LM Studio).

## Prerequisites

```bash
cd multi-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config/user_context.example.yaml config/user_context.yaml   # optional
cp config/immo_whitelist.example.yaml config/immo_whitelist.yaml  # optional
cp config/regelwerk.example.yaml config/regelwerk.yaml          # required for hauskauf-filter
```

The `state/` directory is auto-created on first worker run and holds runtime
DBs, logs, and telemetry. Its contents are gitignored — `state/.gitkeep` is
the only tracked file.

## Heuristic-only dry run

```bash
python scripts/production_worker.py \
  --account yahoo \
  --mode silent \
  --tranche-size 5 \
  --dry-run \
  --no-telegram \
  --imap-fixture tests/fixtures/imap/demo_quickstart.json
```

Expected: exit 0, per-mail log lines, no network I/O.

## Full UI pipeline (folio)

1. Set `AION_LUMEN_PATH` to this repo if not at `~/Projects/aion-lumen/multi-agent`.
2. Start folio: `npm run dev` in the folio repo.
3. Pipeline page → silent worker → auto-validator (Direktive D).

## Demo state — "lived-in" fixtures for screenshots

A single command populates all three databases (council, folio, feedback) with a
fictional but plausible state — six Algarve apartments + cluster + expired listing,
ranked by three lens personas, 40 mails across five domains, plus a populated
Hauskauf kanban. No IMAP or LLM calls. Reproducible end-to-end.

```bash
# Prerequisites: folio dev server must have been started once to init ~/.folio/folio.db.
cd multi-agent
make demo
```

`make demo` is idempotent. To re-seed (after data changes) use `make demo-force`.
To remove only the demo rows (no other data touched) use `make demo-clean`.

Expected inventory after `make demo`:

```
council.db (~/.council/council.db):
  objects:             6   (3 single-portal + 1 cross-portal-cluster + 1 expired)
  rankings:            15  (3 personas × 5 objects, Borda-aggregated)
  lens_comparisons:    9
  council_runs:        2   (1 council-ingest, 1 council-lens)
folio.db (~/.folio/folio.db):
  worker_runs:         2   (1 silent + 1 validator, parent-linked)
  worker_run_logs:     21  (5 heuristik + 15 voice|validated + 1 auto|promoted)
  worker_run_summary:  2   (with diverse Block-Gründe)
  validator_opinions:  15  (5 mails × 3 voices)
  hauskauf_workflow:   3   (offen / in_arbeit / erledigt)
feedback.db (state/feedback.db):
  feedback:            40  (uids 90001–90040 from demo_quickstart.json)
```

All demo rows are prefixed with `demo-` (run_uuids, object_ids) or live in the
UID range 90001–90040 (feedback). Real production data is never touched.

Persona configuration: copy `config/user_context.example.yaml`,
`config/immo_whitelist.example.yaml`, and `config/regelwerk.example.yaml`,
or fill the gitignored real files with the Alex+Maya demo content (see
`docs/fieldnotes/fieldnote-direktive-mock-data-screenshots-2026-06-11.md` for the
canonical values). The example corridor in `regelwerk.example.yaml` uses
Algarve (Portugal) placeholder PLZ — adjust to your real search region.

## Environment overrides

| Variable | Default |
|---|---|
| `AION_LUMEN_PATH` | `~/Projects/aion-lumen/multi-agent` |
| `FEEDBACK_DB_PATH` | `<repo>/state/feedback.db` |
| `FOLIO_DB_PATH` | `~/.folio/folio.db` |
| `MULTI_AGENT_CONFIG_DIR` | `<repo>/config` |
| `LIFE_MAIL_ACCOUNTS_TOML` | `~/Projects/life-mail/accounts.toml` |
