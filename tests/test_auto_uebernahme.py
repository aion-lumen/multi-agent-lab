"""auto_uebernahme consensus promotion (tmp SQLite)."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))


@pytest.fixture()
def dbs(tmp_path: Path, monkeypatch):
    feedback = tmp_path / "feedback.db"
    folio = tmp_path / "folio.db"
    monkeypatch.setenv("FEEDBACK_DB_PATH", str(feedback))
    monkeypatch.setenv("FOLIO_DB_PATH", str(folio))

    with sqlite3.connect(feedback) as conn:
        conn.execute(
            """CREATE TABLE feedback (
                id INTEGER PRIMARY KEY, domain TEXT, actionability TEXT,
                heuristic_markers TEXT, account_id TEXT, imap_uid INTEGER
            )"""
        )
        conn.execute(
            "INSERT INTO feedback VALUES (1, 'immo', 'actionable', '[]', 'yahoo', 100)"
        )
    with sqlite3.connect(folio) as conn:
        conn.execute(
            """CREATE TABLE validator_opinions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feedback_id INTEGER, validator_model TEXT,
                validator_domain TEXT, validator_actionability TEXT
            )"""
        )
        for model in ("gemma", "qwen", "qwen-thinking"):
            conn.execute(
                "INSERT INTO validator_opinions (feedback_id, validator_model, "
                "validator_domain, validator_actionability) VALUES (1, ?, 'immo', 'actionable')",
                (model,),
            )
    yield feedback, folio


def test_promote_eligible_promotes_on_full_consensus(dbs):
    import importlib

    import paths
    import auto_uebernahme

    importlib.reload(paths)
    importlib.reload(auto_uebernahme)
    promote_eligible = auto_uebernahme.promote_eligible
    feedback, _ = dbs
    stats = promote_eligible(feedback_ids=[1])
    assert stats["eligible"] >= 1
    with sqlite3.connect(feedback) as conn:
        act = conn.execute("SELECT actionability FROM feedback WHERE id=1").fetchone()[0]
    assert act == "uebernommen"
