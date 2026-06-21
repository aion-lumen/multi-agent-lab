CREATE TABLE ingest_acks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pending_ingest_id INTEGER NOT NULL UNIQUE,
    council_object_id TEXT NULL,
    status TEXT NOT NULL CHECK(status IN ('processed', 'failed', 'duplicate')),
    error_reason TEXT NULL,
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE objects (
            id TEXT PRIMARY KEY,
            source_url TEXT UNIQUE NOT NULL,
            portal TEXT NOT NULL,
            address TEXT,
            qm INTEGER,
            bj INTEGER,
            price_value INTEGER,
            price_currency TEXT,
            photo_url TEXT,
            object_class TEXT NOT NULL DEFAULT 'annonce'
                CHECK(object_class IN ('annonce', 'update', 'transaktion')),
            status_tag TEXT NOT NULL DEFAULT 'neu'
                CHECK(status_tag IN ('neu', 'kaufen', 'beobachten', 'verworfen', 'archiv', 'abgelaufen')),
            times_seen INTEGER NOT NULL DEFAULT 1,
            last_seen TEXT NOT NULL DEFAULT (datetime('now')),
            title TEXT,
            description TEXT,
            raw_og TEXT,
            from_feedback_ids TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_updated TEXT NOT NULL DEFAULT (datetime('now'))
        , image_urls TEXT, image_local_paths TEXT);
CREATE TABLE rankings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant_id TEXT NOT NULL,
                object_id TEXT NOT NULL,
                rank INTEGER NOT NULL CHECK(rank BETWEEN 1 AND 10),
                recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (object_id) REFERENCES objects(id)
            );
CREATE TABLE lens_comparisons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lens_id TEXT NOT NULL,
                obj_a_id TEXT NOT NULL,
                obj_b_id TEXT NOT NULL,
                winner_id TEXT NOT NULL,
                reason TEXT,
                confidence TEXT CHECK(confidence IN ('low', 'medium', 'high')),
                recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (obj_a_id) REFERENCES objects(id),
                FOREIGN KEY (obj_b_id) REFERENCES objects(id),
                FOREIGN KEY (winner_id) REFERENCES objects(id)
            );
CREATE TABLE consolidated_top10 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_id TEXT NOT NULL,
                borda_score REAL NOT NULL,
                rank INTEGER NOT NULL,
                computed_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (object_id) REFERENCES objects(id)
            );
CREATE TABLE user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                object_id TEXT NOT NULL,
                action TEXT NOT NULL
                    CHECK(action IN ('kaufen', 'beobachten', 'verwerfen', 'in-top10', 'out-top10')),
                recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (object_id) REFERENCES objects(id)
            );
CREATE TABLE object_lifecycle_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN
        ('first_seen', 'expired', 'reactivated', 'archived')),
    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata TEXT NULL,
    FOREIGN KEY (object_id) REFERENCES objects(id)
);
CREATE TABLE mail_inserat_markers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    feedback_id  INTEGER NOT NULL,
    marker       TEXT NOT NULL,
    recorded_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE mail_ingest_acks (
            feedback_id INTEGER PRIMARY KEY,
            status TEXT NOT NULL CHECK(status IN
                ('processed', 'no_url_found', 'all_failed',
                 'filtered_projected', 'filtered_foreclosure',
                 'filtered_out_of_corridor')),
            n_objects INTEGER NOT NULL DEFAULT 0,
            error_reason TEXT NULL,
            processed_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
CREATE TABLE council_runs (
    run_uuid      TEXT    PRIMARY KEY,
    run_type      TEXT    NOT NULL,
                -- 'council-ingest' | 'council-lens'
    started_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    ended_at      TEXT,
    status        TEXT    NOT NULL DEFAULT 'running',
                -- 'running' | 'completed' | 'failed'
    n_processed   INTEGER DEFAULT 0,
    exit_code     INTEGER,
    error_summary TEXT
);
CREATE TABLE council_run_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_uuid     TEXT    NOT NULL,
    seq          INTEGER NOT NULL,
    recorded_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    voice        TEXT    NOT NULL,
                -- 'council-ingest' | 'council-lens' | 'borda'
    mail_id      INTEGER,            -- feedback.id (cross-DB ref)
    object_id    TEXT,               -- council.objects.id
    event_type   TEXT    NOT NULL,
                -- 'ingested' | 'no_url_found' | 'all_failed'
                -- | 'filtered_out_of_corridor' | 'body_parse_skipped'
                -- | 'lens-ranked' | 'lens-skipped' | 'info'
    message      TEXT,
    level        TEXT    DEFAULT 'info'
);
CREATE TABLE council_run_summary (
    run_uuid          TEXT    PRIMARY KEY,
    geprueft          INTEGER NOT NULL DEFAULT 0,
    objects_created   INTEGER NOT NULL DEFAULT 0,
    marker_count      INTEGER NOT NULL DEFAULT 0,
    reason_breakdown  TEXT,
    written_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE object_price_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    object_id       TEXT    NOT NULL,
    price_value     INTEGER,
    price_currency  TEXT,
    recorded_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    source_mail_id  INTEGER,
    FOREIGN KEY (object_id) REFERENCES objects(id)
);
CREATE INDEX idx_ingest_acks_processed_at ON ingest_acks(processed_at DESC);
CREATE INDEX idx_objects_status ON objects(status_tag);
CREATE INDEX idx_objects_class ON objects(object_class);
CREATE INDEX idx_objects_portal ON objects(portal);
CREATE INDEX idx_objects_last_seen ON objects(last_seen DESC);
CREATE INDEX idx_rankings_participant ON rankings(participant_id);
CREATE INDEX idx_rankings_object ON rankings(object_id);
CREATE INDEX idx_rankings_recorded ON rankings(recorded_at DESC);
CREATE INDEX idx_comparisons_lens ON lens_comparisons(lens_id);
CREATE INDEX idx_comparisons_recorded ON lens_comparisons(recorded_at DESC);
CREATE INDEX idx_top10_computed ON consolidated_top10(computed_at DESC);
CREATE INDEX idx_top10_rank ON consolidated_top10(rank);
CREATE INDEX idx_user_actions_user ON user_actions(user_id);
CREATE INDEX idx_user_actions_object ON user_actions(object_id);
CREATE INDEX idx_ole_object_event
    ON object_lifecycle_events(object_id, event_type);
CREATE INDEX idx_ole_recorded_at
    ON object_lifecycle_events(recorded_at);
CREATE INDEX idx_mim_feedback ON mail_inserat_markers(feedback_id);
CREATE INDEX idx_mail_ingest_acks_processed_at
            ON mail_ingest_acks(processed_at DESC);
CREATE INDEX idx_council_runs_started ON council_runs(started_at DESC);
CREATE INDEX idx_crl_run_uuid_seq ON council_run_logs(run_uuid, seq);
CREATE INDEX idx_price_history_object_date
    ON object_price_history(object_id, recorded_at DESC);
CREATE TABLE object_clusters (
    cluster_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    plz            INTEGER,
    qm             INTEGER,
    price          INTEGER,
    price_currency TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE cluster_members (
    cluster_id  INTEGER NOT NULL,
    object_id   TEXT    NOT NULL,
    joined_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (cluster_id, object_id),
    FOREIGN KEY (cluster_id) REFERENCES object_clusters(cluster_id),
    FOREIGN KEY (object_id) REFERENCES objects(id)
);
CREATE INDEX idx_cluster_members_object
    ON cluster_members(object_id);
CREATE INDEX idx_object_clusters_plz_qm
    ON object_clusters(plz, qm);
