"""End-to-end smoke test for production_worker.main() with mocked external
side effects (no real Yahoo, no real plugin CLI, no real Telegram, no real
Hermes kanban). Uses MockIMAPSession via --imap-fixture.

The goal is to verify the pipeline plumbing — argparse, sys.path, sequence
of IMAP fetch → plugin call → heuristic → telegram → kanban → feedback.db.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "imap" / "smoke_single.json"
WORKER = REPO_ROOT / "scripts" / "production_worker.py"


def test_smoke_dry_run_with_fixture(tmp_path: Path):
    """End-to-end --dry-run path. Fixture has 2 envelopes (Homegate + DHL).
    Verifies both Tier-0 (paketzustellung) and Tier-1 (immo) paths in one run.
    """
    venv_py = REPO_ROOT / ".venv" / "bin" / "python"
    python = str(venv_py) if venv_py.exists() else sys.executable
    proc = subprocess.run(
        [
            python,
            str(WORKER),
            "--board", "test-o1-smoke",
            "--tranche-size", "2",
            "--dry-run",
            "--no-telegram",
            "--imap-fixture", str(FIXTURE),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=60,
    )

    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, (
        f"worker exited {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    # Worker pipeline markers — should appear at least once for each of the 2 mails
    assert "production_worker O.1 board=test-o1-smoke" in output
    assert "[dry-run] would create kanban task" in output
    assert "[dry-run] would call plugin CLI" in output
    assert "[no-telegram] would send Stage-1" in output
    assert "[dry-run] would post JSON comment" in output
    # Both Tier paths exercised — heuristic suggestion is in the [no-telegram] log line
    assert "suggested=move_paketzustellung" in output
    assert "suggested=move_immo_portal" in output
    assert "DONE processed=2" in output


def test_smoke_help_text():
    venv_py = REPO_ROOT / ".venv" / "bin" / "python"
    python = str(venv_py) if venv_py.exists() else sys.executable
    proc = subprocess.run(
        [python, str(WORKER), "--help"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=20,
    )
    assert proc.returncode == 0
    for flag in ("--board", "--mode", "--tranche-size",
                 "--dry-run", "--no-telegram", "--imap-fixture", "--assignee"):
        assert flag in proc.stdout, f"missing {flag} in --help"


def test_smoke_mode_audit_raises_not_implemented():
    """--mode audit must raise NotImplementedError per Prompt §2.2."""
    venv_py = REPO_ROOT / ".venv" / "bin" / "python"
    python = str(venv_py) if venv_py.exists() else sys.executable
    proc = subprocess.run(
        [
            python,
            str(WORKER),
            "--board", "test-o1-smoke",
            "--mode", "audit",
            "--dry-run",
            "--no-telegram",
            "--tranche-size", "1",
            "--imap-fixture", str(FIXTURE),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=20,
    )
    assert proc.returncode != 0
    assert "NotImplementedError" in (proc.stdout + proc.stderr)


def test_mock_imap_session_yields_expected_envelope():
    """Direct test of MockIMAPSession contract — no worker involved.
    smoke_single.json now contains 2 envelopes (Homegate + DHL) per CC-Update."""
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    from tests.mocks.imap import MockIMAPSession  # noqa: PLC0415
    with MockIMAPSession(fixture_path=FIXTURE) as session:
        total, uidvalidity = session.select_folder("INBOX")
        assert total == 2
        assert uidvalidity == 1_000_000
        uids = session.search_uids()
        assert set(uids) == {99999, 99998}
        envs = list(session.fetch_envelopes([99999, 99998]))
        assert len(envs) == 2
        addrs = {e.from_addr for e in envs}
        assert addrs == {"noreply@homegate.ch", "noreply@dhl.de"}


def test_build_o1_payload_shape():
    """Verify build_o1_payload returns the §G1-3.4 shape."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from production_worker import build_o1_payload  # noqa: PLC0415
    from immo_heuristic import HeuristicResult  # noqa: PLC0415
    from feedback_telegram import UserDecision  # noqa: PLC0415

    class StubEnv:
        uid = 12345
        from_name = "Homegate AG"
        from_addr = "noreply@homegate.ch"
        subject = "Test"
        date = "Mon, 01 Jan 2026"
        message_id = "<m@id>"

    payload = build_o1_payload(
        task_id="t-1",
        env=StubEnv(),
        uidvalidity=42,
        plugin_output={"value": "geschaeftspost", "confidence": 0.85,
                       "evidence": [{"type": "x"}]},
        heuristic_result=HeuristicResult(
            suggested_action="move_immo_portal",
            reason="test",
            confidence="high",
            matched_markers=["m1"],
        ),
        user_decision=UserDecision(
            classification="geschaeftspost",
            suggested_action_confirmed=True,
            final_action="move_immo_portal",
            response_time_ms=1500,
            timeout_occurred=False,
        ),
        timestamps={
            "imap_fetched_at": "2026-05-16T01:00:00.000Z",
            "imap_fetched_at_ms": 1_000_000,
            "plugin_completed_at": "2026-05-16T01:00:21.000Z",
            "telegram_sent_at": "2026-05-16T01:00:21.500Z",
            "user_responded_at": "2026-05-16T01:00:24.000Z",
            "kanban_completed_at": "2026-05-16T01:00:24.100Z",
            "kanban_completed_at_ms": 1_001_500,
        },
    )

    assert payload["schema_version"] == "o1.0"
    assert payload["imap_uid"] == 12345
    assert payload["uidvalidity"] == 42
    assert payload["outcome"] == "completed_with_telegram_feedback"
    # Envelope sub-record (per Gate-1-signoff §3.4)
    assert payload["envelope"]["from_addr"] == "noreply@homegate.ch"
    assert payload["envelope"]["subject"] == "Test"
    # Plugin output preserved as-is
    assert payload["plugin_output"]["value"] == "geschaeftspost"
    # Heuristic result is asdict'd
    assert payload["heuristic_result"]["suggested_action"] == "move_immo_portal"
    # User decision is asdict'd
    assert payload["user_decision"]["final_action"] == "move_immo_portal"
    # 3.5c-shape preserved
    assert payload["result"]["type"] == "classification"
    assert payload["result"]["value"] == "geschaeftspost"
    assert isinstance(payload["evidence"], list)
    assert isinstance(payload["stats"], dict)
    assert payload["stats"]["wall_clock_ms"] == 1_500
    # JSON-serialisable
    json.dumps(payload)



def test_task_body_format_matches_plugin_parser_regex():
    """Worker's build_task_body_from_envelope() must produce a body that
    the plugin's parse_email_body() can extract sender + subject from.

    Regex source: ~/.hermes/plugins/email-classification/lib/pipeline.py:28-29.
    Replicated inline (not imported) to avoid plugin module side-effects
    (lm_studio / model_swap loaders). If the plugin regex drifts, update here.
    """
    import re
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from production_worker import build_task_body_from_envelope  # noqa: PLC0415

    class StubEnv:
        uid = 99999
        from_name = "Homegate AG"
        from_addr = "noreply@homegate.ch"
        subject = "Wohnung Basel — 4.5 Zimmer"
        date = "Mon, 16 May 2026 03:00:00 +0000"
        message_id = "<msg-1@homegate.ch>"
        body_text = "Listing-Details: CHF 1'200'000, 120m², Basel-Stadt."

    body = build_task_body_from_envelope(StubEnv())

    # Verbatim from plugin pipeline.py
    RE_SENDER = re.compile(r"^\s*-\s*\*\*Sender:\*\*\s*(.+?)\s*$", re.MULTILINE)
    RE_SUBJECT = re.compile(r"^\s*-\s*\*\*Subject:\*\*\s*(.+?)\s*$", re.MULTILINE)

    m_sender = RE_SENDER.search(body)
    m_subject = RE_SUBJECT.search(body)
    assert m_sender is not None, f"Plugin _RE_SENDER did not match body:\n{body}"
    assert m_subject is not None, f"Plugin _RE_SUBJECT did not match body:\n{body}"
    assert "noreply@homegate.ch" in m_sender.group(1)
    assert m_subject.group(1) == "Wohnung Basel — 4.5 Zimmer"
