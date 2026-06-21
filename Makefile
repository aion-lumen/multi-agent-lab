# multi-agent Makefile — Demo + Test convenience targets.
#
# Run from multi-agent/ root with a Python venv activated (see docs/quickstart.md).
#
# Demo workflow uses ISOLATED *-demo.db files so real production data is never
# touched. Env-vars below are exported to every demo* target; seed scripts and
# folio dev server (via scripts/demo-server.sh) respect them.

PYTHON ?= python3
COUNCIL_SETUP := ../council/scripts/setup_council_db.py
REAL_FOLIO_DB := $(HOME)/.folio/folio.db
REAL_COUNCIL_DB := $(HOME)/.council/council.db
REAL_FEEDBACK_DB := state/feedback.db

# Isolated demo databases — separate files, never touched by real workflow.
# All gitignored. init_demo_dbs.sh clones the real-DB schemas into them.
export FOLIO_DB_PATH := $(HOME)/.folio/folio-demo.db
export COUNCIL_DB_PATH := $(HOME)/.council/council-demo.db
export FEEDBACK_DB_PATH := $(abspath state/feedback-demo.db)

# Internal aliases used by inventory + demo-clean targets.
COUNCIL_DB := $(COUNCIL_DB_PATH)
FOLIO_DB := $(FOLIO_DB_PATH)
FEEDBACK_DB := $(FEEDBACK_DB_PATH)

.PHONY: help demo demo-force demo-clean test init-demo-dbs inventory

help:
	@echo "Targets (all demo* operate on ISOLATED *-demo.db files, real DBs untouched):"
	@echo "  make demo            Init isolated demo DBs (if needed) + seed Alex+Maya"
	@echo "                       state across all three (council objects ranked, folio"
	@echo "                       runs + hauskauf workflow, feedback rows). Idempotent."
	@echo "  make demo-force      Re-seed even if demo rows exist (DELETEs first)."
	@echo "  make demo-clean      Delete only the demo rows from the demo DBs."
	@echo "  make init-demo-dbs   Clone schemas from real DBs into *-demo.db (idempotent)."
	@echo "  make inventory       Print current demo-state inventory."
	@echo "  make test            Run pytest suite."
	@echo ""
	@echo "Demo DBs (set via env, override-able):"
	@echo "  FOLIO_DB_PATH    = $(FOLIO_DB_PATH)"
	@echo "  COUNCIL_DB_PATH  = $(COUNCIL_DB_PATH)"
	@echo "  FEEDBACK_DB_PATH = $(FEEDBACK_DB_PATH)"

init-demo-dbs:
	@bash scripts/init_demo_dbs.sh

# Check all three demo DBs exist (init_demo_dbs.sh creates them if missing).
check-demo-dbs:
	@if [ ! -f "$(FOLIO_DB_PATH)" ] || [ ! -f "$(COUNCIL_DB_PATH)" ] || [ ! -f "$(FEEDBACK_DB_PATH)" ]; then \
		echo "Demo DBs missing — running init_demo_dbs.sh first..."; \
		bash scripts/init_demo_dbs.sh; \
	fi

demo: check-demo-dbs
	@echo "─── Seeding council-demo.db ─────────────────────────────────"
	$(PYTHON) scripts/seed_council_demo.py --db "$(COUNCIL_DB_PATH)"
	@echo ""
	@echo "─── Seeding feedback-demo.db + folio-demo.db ────────────────"
	$(PYTHON) scripts/seed_pipeline_demo.py \
		--feedback-db "$(FEEDBACK_DB_PATH)" \
		--folio-db "$(FOLIO_DB_PATH)"
	@echo ""
	@$(MAKE) inventory

demo-force: check-demo-dbs
	@echo "─── Re-seeding council-demo.db (force) ──────────────────────"
	$(PYTHON) scripts/seed_council_demo.py --db "$(COUNCIL_DB_PATH)" --force
	@echo ""
	@echo "─── Re-seeding feedback-demo.db + folio-demo.db (force) ─────"
	$(PYTHON) scripts/seed_pipeline_demo.py \
		--feedback-db "$(FEEDBACK_DB_PATH)" \
		--folio-db "$(FOLIO_DB_PATH)" \
		--force
	@echo ""
	@$(MAKE) inventory

demo-clean: check-demo-dbs
	@echo "─── Deleting demo rows only (no other data touched) ─────────"
	$(PYTHON) -c "import sqlite3, os; \
		conn = sqlite3.connect('$(COUNCIL_DB)'); \
		conn.executescript(\"DELETE FROM consolidated_top10 WHERE object_id LIKE 'demo-%'; DELETE FROM lens_comparisons WHERE obj_a_id LIKE 'demo-%' OR obj_b_id LIKE 'demo-%'; DELETE FROM rankings WHERE object_id LIKE 'demo-%'; DELETE FROM object_lifecycle_events WHERE object_id LIKE 'demo-%'; DELETE FROM council_runs WHERE run_uuid LIKE 'demo-%'; DELETE FROM objects WHERE id LIKE 'demo-%';\"); \
		conn.commit()"
	$(PYTHON) -c "import sqlite3; \
		conn = sqlite3.connect('$(FOLIO_DB)'); \
		conn.executescript(\"DELETE FROM worker_run_logs WHERE run_uuid LIKE 'demo-%'; DELETE FROM worker_run_summary WHERE run_uuid LIKE 'demo-%'; DELETE FROM validator_opinions WHERE imap_uid BETWEEN 90001 AND 90040; DELETE FROM worker_runs WHERE run_uuid LIKE 'demo-%'; DELETE FROM hauskauf_workflow WHERE council_object_id LIKE 'demo-%';\"); \
		conn.commit()"
	$(PYTHON) -c "import sqlite3; \
		conn = sqlite3.connect('$(FEEDBACK_DB_PATH)'); \
		conn.execute(\"DELETE FROM feedback WHERE imap_uid BETWEEN 90001 AND 90040\"); \
		conn.commit()"
	@echo "Demo rows deleted."

inventory:
	@echo "═══ Demo State Inventory ═══════════════════════════════════"
	@echo "  Reading from isolated demo DBs (not real production DBs)."
	@echo ""
	@$(PYTHON) -c "import sqlite3; \
		c = sqlite3.connect('$(COUNCIL_DB)'); \
		objects = c.execute(\"SELECT COUNT(*) FROM objects WHERE id LIKE 'demo-%'\").fetchone()[0]; \
		rankings = c.execute(\"SELECT COUNT(*) FROM rankings WHERE object_id LIKE 'demo-%'\").fetchone()[0]; \
		comps = c.execute(\"SELECT COUNT(*) FROM lens_comparisons WHERE obj_a_id LIKE 'demo-%' OR obj_b_id LIKE 'demo-%'\").fetchone()[0]; \
		runs = c.execute(\"SELECT COUNT(*) FROM council_runs WHERE run_uuid LIKE 'demo-%'\").fetchone()[0]; \
		print(f'  council.db ($(COUNCIL_DB_PATH)):'); \
		print(f'    objects:             {objects}'); \
		print(f'    rankings:            {rankings} (3 personas)'); \
		print(f'    lens_comparisons:    {comps}'); \
		print(f'    council_runs:        {runs}')"
	@$(PYTHON) -c "import sqlite3; \
		c = sqlite3.connect('$(FOLIO_DB)'); \
		runs = c.execute(\"SELECT COUNT(*) FROM worker_runs WHERE run_uuid LIKE 'demo-%'\").fetchone()[0]; \
		logs = c.execute(\"SELECT COUNT(*) FROM worker_run_logs WHERE run_uuid LIKE 'demo-%'\").fetchone()[0]; \
		ops = c.execute(\"SELECT COUNT(*) FROM validator_opinions WHERE imap_uid BETWEEN 90001 AND 90040\").fetchone()[0]; \
		hk = c.execute(\"SELECT COUNT(*) FROM hauskauf_workflow WHERE council_object_id LIKE 'demo-%'\").fetchone()[0]; \
		summary = c.execute(\"SELECT COUNT(*) FROM worker_run_summary WHERE run_uuid LIKE 'demo-%'\").fetchone()[0]; \
		print(f'  folio.db ($(FOLIO_DB_PATH)):'); \
		print(f'    worker_runs:         {runs}'); \
		print(f'    worker_run_logs:     {logs}'); \
		print(f'    worker_run_summary:  {summary}'); \
		print(f'    validator_opinions:  {ops}'); \
		print(f'    hauskauf_workflow:   {hk}')"
	@$(PYTHON) -c "import sqlite3; \
		c = sqlite3.connect('$(FEEDBACK_DB_PATH)'); \
		mails = c.execute('SELECT COUNT(*) FROM feedback WHERE imap_uid BETWEEN 90001 AND 90040').fetchone()[0]; \
		print(f'  feedback.db ($(FEEDBACK_DB_PATH)):'); \
		print(f'    feedback:            {mails} (demo mails uid 90001-90040)')"
	@echo ""
	@echo "STOP-POINT — owner reviews this inventory before screenshot capture."
	@echo "═══════════════════════════════════════════════════════════"

test:
	$(PYTHON) -m pytest tests/ -q
