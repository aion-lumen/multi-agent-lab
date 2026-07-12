"""council_state — single-source-of-truth read of Council registration.

PARITY LOCK: the CASES table below is the shared truth table that must match
folio/src/lib/server/env.ts `isCouncilRegistered()`. The TS side asserts the same
table in src/lib/server/env.scoping.test.ts. If you change one, change the other —
otherwise the two implementations diverge silently (the exact trap this guards).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import council_state  # noqa: E402
from domain_actionability import _apply_tier1_blocker_filter  # noqa: E402

# (name, active-vault.json content or None=missing-file, expected council_registered)
CASES = [
    ("demo_flag",                 {"path": "/home/u/real", "demo": True},                 False),
    ("demo_flag_beats_council",   {"path": "/home/u/real", "demo": True, "council": True}, False),
    ("real_council_true",         {"path": "/home/u/real", "council": True},              True),
    ("real_council_false",        {"path": "/home/u/real", "council": False},             False),
    ("real_council_absent",       {"path": "/home/u/real"},                               False),
    ("demo_path_heuristic",       {"path": "/x/demo-vault/y", "council": True},           False),
    ("folio_demo_path_heuristic", {"path": "/x/folio-demo", "council": True},             False),
    ("empty_object",              {},                                                     False),
    ("missing_file",              None,                                                   False),
]


@pytest.mark.parametrize("name,content,expected", CASES, ids=[c[0] for c in CASES])
def test_council_registered_truth_table(tmp_path, monkeypatch, name, content, expected):
    avj = tmp_path / "active-vault.json"
    if content is not None:
        avj.write_text(json.dumps(content))
    # else: leave file absent → unreadable path
    monkeypatch.setenv("FOLIO_ACTIVE_VAULT_JSON", str(avj))
    assert council_state.council_registered() is expected


def _write_vault(tmp_path, monkeypatch, council: bool):
    avj = tmp_path / "active-vault.json"
    avj.write_text(json.dumps({"path": str(tmp_path / "vault"), "council": council}))
    monkeypatch.setenv("FOLIO_ACTIVE_VAULT_JSON", str(avj))


def test_tier1_block_skipped_when_council_off(tmp_path, monkeypatch):
    _write_vault(tmp_path, monkeypatch, council=False)
    markers: list[str] = []
    # a mail carrying a tier1 block marker must NOT be forced archive-silent
    out = _apply_tier1_blocker_filter(
        "job", "actionable", ["tier1:zwangsversteigerung:true"], markers
    )
    assert out == "actionable"
    assert markers == []  # no blocked_by written


def test_tier1_block_active_when_council_on(tmp_path, monkeypatch):
    _write_vault(tmp_path, monkeypatch, council=True)
    markers: list[str] = []
    out = _apply_tier1_blocker_filter(
        "immo", "actionable", ["tier1:zwangsversteigerung:true"], markers
    )
    assert out == "archive-silent"
    assert markers == ["blocked_by:zwangsversteigerung:true"]
