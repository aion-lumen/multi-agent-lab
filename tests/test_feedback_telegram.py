"""Unit tests for feedback_telegram — pure functions (parse_callback_query +
UserDecision builders). The async Telegram conversation is exercised by the
production_worker_smoke test via mocks; here we test the deterministic surface.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from feedback_telegram import (
    UserDecision,
    parse_callback_query,
    _ack_label,
    _decision_from_stage1_only,
    _decision_from_stage2,
    _select_stage2_keyboard,
    _stage2_keyboard,
    _stage2_keyboard_paketzustellung,
    _stage2_required,
)


FIXTURES_PATH = Path(__file__).resolve().parent / "fixtures" / "telegram" / "callback_queries.json"


def _load_fixtures() -> list[dict]:
    return json.loads(FIXTURES_PATH.read_text())


# ---------------------------------------------------------------------------
# parse_callback_query — drive from JSON fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _load_fixtures())
def test_parse_callback_query_matrix(case: dict):
    parsed = parse_callback_query({"data": case["data"]})
    if case.get("expected_task_id") is None:
        assert parsed is None, f"{case['name']}: expected rejection, got {parsed}"
        return
    assert parsed is not None, f"{case['name']}: expected parse, got None"
    assert parsed.task_id == case["expected_task_id"]
    assert parsed.action == case["expected_action"]
    assert parsed.stage == case["expected_stage"]


def test_parse_callback_query_accepts_nested_payload():
    """Accepts {'callback_query': {'data': '...'}} layouts too."""
    parsed = parse_callback_query({"callback_query": {"data": "o1:abc:ok"}})
    assert parsed is not None
    assert parsed.task_id == "abc"
    assert parsed.action == "ok"


def test_parse_callback_query_rejects_empty_payload():
    assert parse_callback_query({}) is None
    assert parse_callback_query(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Stage-2 trigger logic (Prompt §4.2)
# ---------------------------------------------------------------------------


def test_stage2_required_geschaeftspost_with_move():
    assert _stage2_required("geschaeftspost", "move_immo_portal") is True
    assert _stage2_required("geschaeftspost", "move_immo_privat") is True
    assert _stage2_required("geschaeftspost", "move_zu_pruefen") is True


def test_stage2_NOT_required_keep_action():
    assert _stage2_required("geschaeftspost", "keep") is False


def test_stage2_NOT_required_non_geschaeftspost():
    assert _stage2_required("werbung", "move_immo_portal") is False
    assert _stage2_required("privat", "move_immo_portal") is False


# ---------------------------------------------------------------------------
# UserDecision builders
# ---------------------------------------------------------------------------


def test_stage1_ok_confirms_plugin_classification():
    parsed = parse_callback_query({"data": "o1:T1:ok"})
    decision = _decision_from_stage1_only(
        parsed=parsed,
        plugin_value="geschaeftspost",
        suggested_action="move_immo_portal",
        response_time_ms=1234,
    )
    assert decision.classification == "geschaeftspost"
    assert decision.suggested_action_confirmed is True
    assert decision.final_action == "move_immo_portal"
    assert decision.response_time_ms == 1234
    assert decision.timeout_occurred is False


def test_stage1_skip_returns_skip_action_no_classification():
    parsed = parse_callback_query({"data": "o1:T2:skip"})
    decision = _decision_from_stage1_only(
        parsed=parsed,
        plugin_value="geschaeftspost",
        suggested_action="move_immo_portal",
        response_time_ms=890,
    )
    assert decision.classification == ""
    assert decision.suggested_action_confirmed is False
    assert decision.final_action == "skip"


def test_stage1_override_werbung_does_not_move_mailbox():
    parsed = parse_callback_query({"data": "o1:T3:werbung"})
    decision = _decision_from_stage1_only(
        parsed=parsed,
        plugin_value="geschaeftspost",
        suggested_action="move_immo_portal",
        response_time_ms=500,
    )
    assert decision.classification == "werbung"
    assert decision.suggested_action_confirmed is False
    assert decision.final_action == "keep"


def test_stage2_ok_confirms_suggested_action():
    decision = _decision_from_stage2(
        stage1_classification="geschaeftspost",
        stage2_action="stage2_ok",
        suggested_action="move_immo_portal",
        response_time_ms=2500,
    )
    assert decision.classification == "geschaeftspost"
    assert decision.suggested_action_confirmed is True
    assert decision.final_action == "move_immo_portal"


def test_stage2_portal_button_sets_move_immo_portal():
    decision = _decision_from_stage2(
        stage1_classification="geschaeftspost",
        stage2_action="portal",
        suggested_action="move_zu_pruefen",
        response_time_ms=1700,
    )
    assert decision.final_action == "move_immo_portal"
    assert decision.suggested_action_confirmed is False


def test_stage2_privat_immo_button_sets_move_immo_privat():
    decision = _decision_from_stage2(
        stage1_classification="geschaeftspost",
        stage2_action="privat_immo",
        suggested_action="move_immo_portal",
        response_time_ms=2200,
    )
    assert decision.final_action == "move_immo_privat"


def test_stage2_zupruefen_sets_move_zu_pruefen():
    decision = _decision_from_stage2(
        stage1_classification="geschaeftspost",
        stage2_action="zupruefen",
        suggested_action="move_immo_portal",
        response_time_ms=3000,
    )
    assert decision.final_action == "move_zu_pruefen"


# ---------------------------------------------------------------------------
# UserDecision dataclass sanity
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Stage-2 keyboard variants (CC-Update 2026-05-16)
# ---------------------------------------------------------------------------


def test_stage2_keyboard_picks_paketzustellung_variant_for_tier0():
    """For Tier-0 hits (suggested move_paketzustellung), the 2-button variant fires."""
    kb_pz = _select_stage2_keyboard("t-pz", "move_paketzustellung")
    kb_im = _select_stage2_keyboard("t-im", "move_immo_portal")

    # Paketzustellung: 2 rows, 2 buttons total (OK Aktion übernehmen + Stattdessen…)
    assert len(kb_pz.inline_keyboard) == 2
    assert sum(len(row) for row in kb_pz.inline_keyboard) == 2
    pz_actions = {btn.callback_data.split(":")[-1]
                  for row in kb_pz.inline_keyboard for btn in row}
    assert pz_actions == {"stage2_ok", "stage2_back"}

    # Immo: 2 rows, 4 buttons total
    assert len(kb_im.inline_keyboard) == 2
    assert sum(len(row) for row in kb_im.inline_keyboard) == 4
    im_actions = {btn.callback_data.split(":")[-1]
                  for row in kb_im.inline_keyboard for btn in row}
    assert im_actions == {"stage2_ok", "portal", "privat_immo", "zupruefen"}


def test_stage2_back_yields_keep_with_stage1_classification():
    """stage2_back (Reading B per D-U3): final_action=keep, classification preserved."""
    decision = _decision_from_stage2(
        stage1_classification="geschaeftspost",
        stage2_action="stage2_back",
        suggested_action="move_paketzustellung",
        response_time_ms=2100,
    )
    assert decision.classification == "geschaeftspost"
    assert decision.final_action == "keep"
    assert decision.suggested_action_confirmed is False
    assert decision.timeout_occurred is False
    assert decision.response_time_ms == 2100


def test_parse_callback_query_accepts_stage2_back():
    parsed = parse_callback_query({"data": "o1:taskuuid-pz:stage2_back"})
    assert parsed is not None
    assert parsed.task_id == "taskuuid-pz"
    assert parsed.action == "stage2_back"
    assert parsed.stage == 2


def test_user_decision_field_types():
    d = UserDecision(
        classification="geschaeftspost",
        suggested_action_confirmed=True,
        final_action="move_immo_portal",
        response_time_ms=100,
        timeout_occurred=False,
    )
    assert d.classification == "geschaeftspost"
    assert isinstance(d.suggested_action_confirmed, bool)
    assert isinstance(d.response_time_ms, int)
    assert isinstance(d.timeout_occurred, bool)


# ---------------------------------------------------------------------------
# UX-1 callback acknowledgement labels
# (callback-ack-signoff 2026-05-16; Architekt-Option-B)
# ---------------------------------------------------------------------------


def test_ack_label_stage1_known_actions():
    """Stage-1 actions return their architect-specified labels."""
    assert _ack_label(1, "ok", "keep") == "✓ Klassifikation übernommen"
    assert _ack_label(1, "werbung", "keep") == "✓ Werbung"
    assert _ack_label(1, "geschaeftspost", "move_immo_portal") == "✓ Geschäftspost"
    assert _ack_label(1, "skip", "keep") == "⊘ Übersprungen"


def test_ack_label_stage2_immo_variant():
    """Stage-2 Immo actions return the Immo labels."""
    assert _ack_label(2, "stage2_ok", "move_immo_portal") == "✓ Aktion übernommen"
    assert _ack_label(2, "portal", "move_immo_portal") == "✓ → Portal"
    assert _ack_label(2, "privat_immo", "move_immo_portal") == "✓ → Privat"
    assert _ack_label(2, "zupruefen", "move_immo_portal") == "✓ → Zu-Prüfen"


def test_ack_label_stage2_paketzustellung_disambiguates_stage2_ok():
    """stage2_ok with heuristic=move_paketzustellung yields the Paketzustellung label."""
    assert _ack_label(2, "stage2_ok", "move_paketzustellung") == "✓ → Paketzustellung"
    assert _ack_label(2, "stage2_back", "move_paketzustellung") == "↺ Neu klassifizieren"


def test_ack_label_unknown_action_falls_back():
    """Unknown action returns the generic fallback."""
    assert _ack_label(1, "totally_unknown", "keep") == "✓ Empfangen"
    assert _ack_label(2, "weirdaction", "move_immo_portal") == "✓ Empfangen"
    assert _ack_label(99, "ok", "keep") == "✓ Empfangen"
