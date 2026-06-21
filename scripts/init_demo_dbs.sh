#!/usr/bin/env bash
# init_demo_dbs.sh — Klont die SCHEMA der drei Real-DBs (folio.db, council.db,
# feedback.db) in isolierte *-demo.db Pendants. Idempotent: bestehende
# Demo-DBs werden NICHT überschrieben.
#
# Reale Daten werden NIE kopiert — nur das Schema via `sqlite3 .schema`.

set -euo pipefail

FOLIO_REAL="${HOME}/.folio/folio.db"
FOLIO_DEMO="${HOME}/.folio/folio-demo.db"

COUNCIL_REAL="${HOME}/.council/council.db"
COUNCIL_DEMO="${HOME}/.council/council-demo.db"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FEEDBACK_REAL="${REPO_ROOT}/state/feedback.db"
FEEDBACK_DEMO="${REPO_ROOT}/state/feedback-demo.db"

clone_schema() {
    local src="$1"
    local dst="$2"
    local label="$3"

    if [ ! -f "$src" ]; then
        echo "  ERROR: source $label DB not found at $src" >&2
        echo "         start folio dev server / council scripts once to initialize" >&2
        return 1
    fi

    if [ -f "$dst" ]; then
        echo "  $label demo DB already exists at $dst — skipping (delete to re-clone)"
        return 0
    fi

    mkdir -p "$(dirname "$dst")"
    # sqlite_sequence is an internal table — can't be CREATE'd manually.
    # Filter it out from the schema dump.
    sqlite3 "$src" .schema | grep -v "^CREATE TABLE sqlite_sequence" | sqlite3 "$dst"

    # folio.db's init.ts inserts a default users row (id=1) as part of init —
    # `.schema` dumps only CREATE statements, not seed-data INSERTs. The
    # hauskauf_workflow FOREIGN KEY on created_by_user_id needs this. Re-add.
    if [[ "$dst" == *"folio-demo.db" ]]; then
        sqlite3 "$dst" "INSERT OR IGNORE INTO users (id, tailscale_login, display_name, role) VALUES (1, NULL, 'demo-user', 'owner');"
    fi

    echo "  $label demo DB initialized at $dst ($(sqlite3 "$dst" 'SELECT COUNT(*) FROM sqlite_master WHERE type="table"') tables)"
}

echo "Cloning real-DB schemas into isolated *-demo.db files..."
echo ""
clone_schema "$FOLIO_REAL"    "$FOLIO_DEMO"    "folio.db   "
clone_schema "$COUNCIL_REAL"  "$COUNCIL_DEMO"  "council.db "
clone_schema "$FEEDBACK_REAL" "$FEEDBACK_DEMO" "feedback.db"
echo ""
echo "Done. Use \`make demo\` to populate these isolated DBs."
echo "Real DBs (~/.folio/folio.db etc.) are untouched."
