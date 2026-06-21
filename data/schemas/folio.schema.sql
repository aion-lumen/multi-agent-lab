CREATE TABLE corrections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    feedback_id     INTEGER NOT NULL,
    imap_uid        INTEGER NOT NULL,
    previous_action TEXT,
    corrected_action TEXT NOT NULL,
    note            TEXT,
    source          TEXT NOT NULL,
    corrected_at    TEXT NOT NULL
, corrected_domain TEXT, corrected_actionability TEXT, correction_marker TEXT, heuristic_markers_snapshot TEXT);
CREATE TABLE worker_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_uuid TEXT NOT NULL UNIQUE,
    account TEXT NOT NULL,
    board TEXT NOT NULL,
    mode TEXT NOT NULL,
    tranche_size INTEGER NOT NULL,
    pid INTEGER,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    exit_code INTEGER,
    error_summary TEXT,
    mails_processed INTEGER DEFAULT 0
, parent_run_uuid TEXT NULL);
CREATE TABLE review_state (
    feedback_id INTEGER PRIMARY KEY,
    account_id TEXT NOT NULL,
    imap_uid INTEGER NOT NULL,
    reviewed_at TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE validator_opinions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feedback_id INTEGER NOT NULL,
    account_id TEXT NOT NULL,
    imap_uid INTEGER NOT NULL,
    validator_model TEXT NOT NULL,
    validator_action TEXT NOT NULL,
    validator_confidence REAL,
    validator_reasoning TEXT,
    evaluated_at TEXT NOT NULL, validator_domain TEXT, validator_actionability TEXT,
    UNIQUE(feedback_id, validator_model)
);
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tailscale_login TEXT UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('owner', 'council_member')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE object_views (
    object_id       TEXT    NOT NULL,
    user_id         INTEGER NOT NULL,
    last_viewed_at  TEXT    NOT NULL,
    PRIMARY KEY (object_id, user_id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE object_triggers (
    object_id       TEXT    NOT NULL,
    user_id         INTEGER NOT NULL,
    triggered_at    TEXT    NOT NULL,
    PRIMARY KEY (object_id, user_id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE object_status_override (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    council_object_id   TEXT    NOT NULL,
    user_id             INTEGER NOT NULL,
    status_tag          TEXT    NOT NULL
        CHECK(status_tag IN ('neu','kaufen','beobachten','verworfen','archiv','abgelaufen')),
    recorded_at         TEXT    NOT NULL, reason TEXT NULL, source TEXT DEFAULT 'user_action',
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE user_rankings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    object_id       TEXT    NOT NULL,
    rank            INTEGER NOT NULL,
    recorded_at     TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE pending_ingest (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    url                     TEXT    NOT NULL,
    submitted_by_user_id    INTEGER NOT NULL,
    submitted_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    processed_at            TEXT,
    FOREIGN KEY (submitted_by_user_id) REFERENCES users(id)
);
CREATE TABLE object_notes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,
    council_object_id   TEXT    NOT NULL,
    note_text           TEXT    NOT NULL,
    recorded_at         TEXT    NOT NULL, source TEXT DEFAULT 'user_action', inherited_from_object_id TEXT NULL, inherited_from_cluster_id INTEGER NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE mail_actionability_override (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    feedback_id              INTEGER NOT NULL,
    user_id                  INTEGER NOT NULL,
    overridden_actionability TEXT    NOT NULL
        CHECK(overridden_actionability IN ('actionable', 'uebernommen', 'archive-silent')),
    recorded_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE worker_run_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_uuid     TEXT    NOT NULL,
    seq          INTEGER NOT NULL,
    recorded_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    voice        TEXT    NOT NULL,
                -- 'heuristik' | 'gemma' | 'qwen' | 'qwen-thinking' | 'auto'
    mail_id      INTEGER,                 -- feedback.id (cross-DB ref)
    object_id    TEXT,                    -- council.objects.id (cross-DB ref)
    event_type   TEXT    NOT NULL,
                -- 'classified' | 'validated' | 'promoted' | 'no_consensus' | 'info'
    message      TEXT,
    level        TEXT    DEFAULT 'info'   -- 'info' | 'warn' | 'error'
);
CREATE TABLE worker_run_summary (
    run_uuid              TEXT    PRIMARY KEY,
    geprueft              INTEGER NOT NULL DEFAULT 0,
    uebernommen           INTEGER NOT NULL DEFAULT 0,
    actionable            INTEGER NOT NULL DEFAULT 0,
    archive_silent        INTEGER NOT NULL DEFAULT 0,
    council_objects       INTEGER NOT NULL DEFAULT 0,
    marker_count          INTEGER NOT NULL DEFAULT 0,
    reason_breakdown      TEXT,
    worker_imports_sample TEXT,
    written_at            TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS "hauskauf_workflow" (
				id                    INTEGER PRIMARY KEY AUTOINCREMENT,
				council_object_id     TEXT    NOT NULL,
				status                TEXT    NOT NULL DEFAULT 'offen'
				                        CHECK(status IN ('offen','in_arbeit','blockiert','erledigt')),
				termin                TEXT,
				verhandlungspreis     REAL,
				notes                 TEXT,
				verdict               TEXT    NULL
				                        CHECK(verdict IS NULL OR verdict IN ('favorisiert','verworfen')),
				recorded_at           TEXT    NOT NULL DEFAULT (datetime('now')),
				created_by_user_id    INTEGER NOT NULL, source TEXT DEFAULT 'user_action', inherited_from_object_id TEXT NULL, inherited_from_cluster_id INTEGER NULL,
				FOREIGN KEY (created_by_user_id) REFERENCES users(id),
				CHECK (
					(status = 'offen') OR
					(status = 'in_arbeit' AND termin IS NOT NULL) OR
					(status = 'blockiert') OR
					(status = 'erledigt' AND verhandlungspreis IS NOT NULL)
				)
			);
CREATE INDEX idx_corrections_feedback ON corrections(feedback_id);
CREATE INDEX idx_corrections_corrected_at ON corrections(corrected_at);
CREATE INDEX idx_worker_runs_started ON worker_runs(started_at DESC);
CREATE INDEX idx_review_account ON review_state(account_id);
CREATE INDEX idx_validator_feedback ON validator_opinions(feedback_id);
CREATE INDEX idx_validator_domain ON validator_opinions(validator_domain);
CREATE INDEX idx_validator_actionability ON validator_opinions(validator_actionability);
CREATE INDEX idx_corrections_domain ON corrections(corrected_domain);
CREATE INDEX idx_corrections_actionability ON corrections(corrected_actionability);
CREATE INDEX idx_users_tailscale ON users(tailscale_login);
CREATE INDEX idx_object_views_user ON object_views(user_id);
CREATE INDEX idx_object_triggers_object ON object_triggers(object_id);
CREATE INDEX idx_oso_object ON object_status_override(council_object_id);
CREATE INDEX idx_oso_recorded ON object_status_override(recorded_at DESC);
CREATE INDEX idx_ur_user ON user_rankings(user_id);
CREATE INDEX idx_ur_recorded ON user_rankings(recorded_at DESC);
CREATE INDEX idx_pi_pending ON pending_ingest(processed_at) WHERE processed_at IS NULL;
CREATE INDEX idx_on_user_object ON object_notes(user_id, council_object_id);
CREATE INDEX idx_on_recorded ON object_notes(recorded_at DESC);
CREATE INDEX idx_mao_feedback_recorded
    ON mail_actionability_override(feedback_id, recorded_at DESC);
CREATE INDEX idx_wrl_run_uuid_seq ON worker_run_logs(run_uuid, seq);
CREATE INDEX idx_wrl_recorded ON worker_run_logs(recorded_at DESC);
CREATE INDEX idx_hauskauf_workflow_status
			  ON hauskauf_workflow(status);
CREATE INDEX idx_hauskauf_workflow_object_recorded
			  ON hauskauf_workflow(council_object_id, recorded_at DESC);
CREATE INDEX idx_worker_runs_parent ON worker_runs(parent_run_uuid);

-- Default user (id=1) required for hauskauf_workflow.created_by_user_id FK.
-- Mirrors the hardcoded INSERT in scripts/init_demo_dbs.sh (folded into the schema).
INSERT OR IGNORE INTO users (id, tailscale_login, display_name, role) VALUES (1, NULL, 'demo-user', 'owner');
