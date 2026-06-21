#!/usr/bin/env bash
# init_demo_dbs.sh — Initializes the three isolated *-demo.db files (folio.db,
# council.db, feedback.db) so `make demo` can populate them. Idempotent:
# existing demo DBs are NOT overwritten.
#
# Two source paths for the schema, in priority order:
#   1. If the corresponding real DB exists (~/.folio/folio.db etc.), clone its
#      live schema via `sqlite3 .schema` — preserves owner-side schema-drift
#      detection on a developer machine.
#   2. Otherwise fall back to the static SQL dumps in data/schemas/*.sql —
#      lets a cold-start machine bootstrap the demo without ever having run
#      the real worker, the folio dev server, or the council scripts.
#
# Real DBs are never read for data — only `.schema`. Static SQL dumps contain
# CREATE TABLE/INDEX statements plus one default-user INSERT for the folio
# hauskauf_workflow FK. Regenerate via `make refresh-demo-schemas` after a
# schema migration on the owner's machine.

set -euo pipefail

FOLIO_REAL="${HOME}/.folio/folio.db"
FOLIO_DEMO="${HOME}/.folio/folio-demo.db"

COUNCIL_REAL="${HOME}/.council/council.db"
COUNCIL_DEMO="${HOME}/.council/council-demo.db"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FEEDBACK_REAL="${REPO_ROOT}/state/feedback.db"
FEEDBACK_DEMO="${REPO_ROOT}/state/feedback-demo.db"
SCHEMA_DIR="${REPO_ROOT}/data/schemas"

init_demo_db() {
    local real="$1"
    local demo="$2"
    local schema_sql="$3"
    local label="$4"

    if [ -f "$demo" ]; then
        echo "  $label demo DB already exists at $demo — skipping (delete to re-init)"
        return 0
    fi

    mkdir -p "$(dirname "$demo")"

    if [ -f "$real" ]; then
        # Path 1: clone live schema from owner's real DB.
        # Migration-backup tables (*__pre_*) are left in for fidelity to the
        # owner's live schema; they're empty and harmless. Path 2 strips them
        # since the static dump is a stranger-facing artefact.
        sqlite3 "$real" .schema \
            | grep -v "^CREATE TABLE sqlite_sequence" \
            | sqlite3 "$demo"

        # folio.db's init.ts inserts a default users row (id=1) as part of
        # init — `.schema` dumps CREATE only, not seed-data. The
        # hauskauf_workflow FOREIGN KEY needs this user to exist.
        if [[ "$demo" == *"folio-demo.db" ]]; then
            sqlite3 "$demo" \
                "INSERT OR IGNORE INTO users (id, tailscale_login, display_name, role) VALUES (1, NULL, 'demo-user', 'owner');"
        fi

        local source="cloned from $real"
    elif [ -f "$schema_sql" ]; then
        # Path 2: load static schema dump (cold-start path).
        sqlite3 "$demo" < "$schema_sql"
        local source="bootstrapped from $schema_sql"
    else
        echo "  ERROR: $label — neither real DB ($real) nor static schema ($schema_sql) available" >&2
        return 1
    fi

    local table_count
    table_count=$(sqlite3 "$demo" 'SELECT COUNT(*) FROM sqlite_master WHERE type="table"')
    echo "  $label demo DB initialized at $demo ($table_count tables, $source)"
}

echo "Initializing isolated *-demo.db files..."
echo ""
init_demo_db "$FOLIO_REAL"    "$FOLIO_DEMO"    "$SCHEMA_DIR/folio.schema.sql"    "folio.db   "
init_demo_db "$COUNCIL_REAL"  "$COUNCIL_DEMO"  "$SCHEMA_DIR/council.schema.sql"  "council.db "
init_demo_db "$FEEDBACK_REAL" "$FEEDBACK_DEMO" "$SCHEMA_DIR/feedback.schema.sql" "feedback.db"
echo ""
echo "Done. Use \`make demo\` to populate these isolated DBs."
echo "Real DBs (~/.folio/folio.db etc.) are untouched."
