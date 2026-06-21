CREATE TABLE feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    account_id TEXT NOT NULL DEFAULT 'yahoo',
    imap_uid INTEGER NOT NULL,
    sender TEXT NOT NULL,
    subject TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    plugin_value TEXT,
    plugin_confidence REAL,
    plugin_evidence TEXT,
    heuristic_suggested_action TEXT,
    heuristic_reason TEXT,
    heuristic_confidence TEXT,
    heuristic_markers TEXT,
    user_classification TEXT,
    user_final_action TEXT,
    suggested_action_confirmed INTEGER,
    response_time_ms INTEGER,
    timeout_occurred INTEGER,
    created_at TEXT NOT NULL,
    mail_date TEXT,
    domain TEXT,
    actionability TEXT,
    effective_actionability TEXT, body_excerpt TEXT,
    UNIQUE(account_id, imap_uid)
);
CREATE INDEX idx_feedback_account ON feedback(account_id);
CREATE INDEX idx_feedback_sender ON feedback(sender);
CREATE INDEX idx_feedback_user_final_action ON feedback(user_final_action);
CREATE INDEX idx_feedback_domain ON feedback(domain);
CREATE INDEX idx_feedback_actionability ON feedback(actionability);
