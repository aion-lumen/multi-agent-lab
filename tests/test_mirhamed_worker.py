"""Verify mirhamed account worker path writes feedback.db rows."""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "imap" / "smoke_single.json"
WORKER = REPO_ROOT / "scripts" / "production_worker.py"


def test_mirhamed_worker_writes_feedback(tmp_path: Path, monkeypatch):
    """Worker run with --account mirhamed persists rows with account_id='mirhamed'."""
    feedback_db = tmp_path / "feedback.db"
    monkeypatch.setenv("FEEDBACK_DB_PATH", str(feedback_db))
    monkeypatch.setenv("MULTI_AGENT_STATE_DIR", str(tmp_path))

    venv_py = REPO_ROOT / ".venv" / "bin" / "python"
    python = str(venv_py) if venv_py.exists() else sys.executable
    proc = subprocess.run(
        [
            python,
            str(WORKER),
            "--account", "mirhamed",
            "--mode", "silent",
            "--no-telegram",
            "--no-kanban",
            "--tranche-size", "1",
            "--imap-fixture", str(FIXTURE),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=90,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert feedback_db.exists(), "feedback.db was not created"

    conn = sqlite3.connect(feedback_db)
    try:
        rows = conn.execute(
            "SELECT account_id, domain, actionability FROM feedback WHERE account_id = ?",
            ("mirhamed",),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) >= 1, "expected at least one mirhamed feedback row"
    assert all(r[0] == "mirhamed" for r in rows)
