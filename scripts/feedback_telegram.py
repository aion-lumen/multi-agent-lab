#!/usr/bin/env python3
"""feedback_telegram.py — 2-stage Telegram feedback flow for production_worker.

Design source: state/prompt-o1-design-notes.md §G3-B + Prompt §4.

Public API:
    UserDecision                — outcome dataclass (Prompt §4.1)
    send_classification_request — main entry point, runs the 2-stage flow synchronously
    parse_callback_query        — pure-function, used by tests + the handler

Callback-data scheme: `o1:<task_id>:<action>` where action ∈
  Stage 1: ok | werbung | geschaeftspost | privat | spam | unklar | skip
  Stage 2 (Immo variant): stage2_ok | portal | privat_immo | zupruefen
  Stage 2 (Paketzustellung variant, CC-Update 2026-05-16): stage2_ok | stage2_back

The Stage-2 keyboard variant is chosen at runtime based on
`heuristic_suggested_action` — paketzustellung gets a 2-button confirm/back
layout (D-U3), Immo move_* gets the 4-button classify-where layout.

A `/show <task_id>` command reveals the full untruncated body of the mail
currently awaiting feedback. The worker keeps awaiting the button-press.

1h timeout per request → UserDecision(timeout_occurred=True,
final_action="keep") and the worker carries on.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

log = logging.getLogger("feedback_telegram")

DEFAULT_TIMEOUT_SECONDS = 3600  # 1h per Prompt §4.4

SCHEMA_CATEGORIES = ("werbung", "geschaeftspost", "privat", "spam", "unklar")

# UX-1 fix (callback-ack-signoff 2026-05-16): callback acknowledgement
# toast labels. Keyed by (stage, action). `stage2_ok` is heuristic-aware
# at lookup time (paketzustellung context yields different label).
_ACK_LABELS: dict[tuple[int, str], str] = {
    (1, "ok"):             "✓ Klassifikation übernommen",
    (1, "werbung"):        "✓ Werbung",
    (1, "geschaeftspost"): "✓ Geschäftspost",
    (1, "privat"):         "✓ Privat",
    (1, "spam"):           "✓ Spam",
    (1, "unklar"):         "✓ Unklar",
    (1, "skip"):           "⊘ Übersprungen",
    (2, "stage2_ok"):      "✓ Aktion übernommen",
    (2, "portal"):         "✓ → Portal",
    (2, "privat_immo"):    "✓ → Privat",
    (2, "zupruefen"):      "✓ → Zu-Prüfen",
    (2, "stage2_back"):    "↺ Neu klassifizieren",
}
_ACK_FALLBACK = "✓ Empfangen"


def _ack_label(stage: int, action: str, heuristic_suggested_action: str) -> str:
    if stage == 2 and action == "stage2_ok" and heuristic_suggested_action == "move_paketzustellung":
        return "✓ → Paketzustellung"
    return _ACK_LABELS.get((stage, action), _ACK_FALLBACK)

# Mapping from Stage-2 action tokens to canonical final_action values.
# `stage2_ok` resolves to the heuristic's suggested_action (caller-supplied).
# `stage2_back` (paketzustellung-variant only) is dynamically resolved: see
# `_decision_from_stage2`. Reading B semantics per Plan D-U3 — engineer flips
# on architect request to UI-re-prompt Reading A.
_STAGE2_TO_FINAL_ACTION = {
    "stage2_ok": "<heuristic_suggested>",  # placeholder — replaced by caller
    "stage2_back": "<keep_with_stage1_classification>",  # Paketzustellung variant
    "portal": "move_immo_portal",
    "privat_immo": "move_immo_privat",
    "zupruefen": "move_zu_pruefen",
}


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


@dataclass
class UserDecision:
    classification: str   # one of SCHEMA_CATEGORIES or "" (skip/timeout)
    suggested_action_confirmed: bool
    # final_action ∈ {keep, move_immo_portal, move_immo_privat, move_zu_pruefen,
    #                 move_paketzustellung, skip}
    final_action: str
    response_time_ms: int
    timeout_occurred: bool


# ---------------------------------------------------------------------------
# Callback-data parsing (pure function; tested directly)
# ---------------------------------------------------------------------------


@dataclass
class _ParsedCallback:
    task_id: str
    action: str
    stage: int  # 1 or 2


def parse_callback_query(payload: dict) -> Optional[_ParsedCallback]:
    """Extract (task_id, action, stage) from a Telegram callback_query payload.

    Accepts either the raw dict from `Update.callback_query.to_dict()` or a
    pre-extracted `{"data": "o1:..."}` envelope.
    Returns None for foreign callback-data (missing `o1:` prefix).
    """
    if not payload:
        return None
    data = payload.get("data") if isinstance(payload.get("data"), str) else None
    if not data:
        # Maybe the payload is the full callback_query object
        cq = payload.get("callback_query") or {}
        data = cq.get("data")
    if not data or not isinstance(data, str):
        return None
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "o1":
        return None
    _, task_id, action = parts
    stage1_actions = {"ok", *SCHEMA_CATEGORIES, "skip"}
    # All stage-2 keys including stage2_back (paketzustellung variant)
    stage2_actions = set(_STAGE2_TO_FINAL_ACTION.keys())
    if action in stage1_actions:
        stage = 1
    elif action in stage2_actions:
        stage = 2
    else:
        return None
    return _ParsedCallback(task_id=task_id, action=action, stage=stage)


# ---------------------------------------------------------------------------
# Message-rendering helpers
# ---------------------------------------------------------------------------


def _stage1_text(
    sender: str,
    subject: str,
    body_snippet: str,
    plugin_value: str,
    plugin_confidence: float,
    heuristic_reason: str,
    suggested_action: str,
    task_id: str,
) -> str:
    snippet = body_snippet.strip()
    if len(snippet) > 200:
        snippet = snippet[:200].rstrip() + "…"
    return (
        f"📧 From: {sender}\n"
        f"📋 Subject: {subject}\n\n"
        f"{snippet}\n\n"
        f"[Use /show {task_id} for full body]\n\n"
        f"Plugin: {plugin_value} (confidence: {plugin_confidence:.2f})\n"
        f"Heuristic: {heuristic_reason}\n"
        f"Suggested: {suggested_action}"
    )


def _stage1_keyboard(task_id: str) -> InlineKeyboardMarkup:
    def _b(label: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(label, callback_data=f"o1:{task_id}:{action}")

    return InlineKeyboardMarkup([
        [_b("OK", "ok")],
        [_b("werbung", "werbung"), _b("geschaeftspost", "geschaeftspost"), _b("privat", "privat")],
        [_b("spam", "spam"), _b("unklar", "unklar"), _b("SKIP", "skip")],
    ])


def _stage2_text(suggested_action: str, heuristic_reason: str) -> str:
    return (
        f"🏠 Immo-Heuristik schlägt vor: {suggested_action}\n"
        f"Grund: {heuristic_reason}\n\n"
        "Bestätigen oder anders entscheiden?"
    )


def _stage2_keyboard(task_id: str) -> InlineKeyboardMarkup:
    """4-button Stage-2 — for Immo move_* suggestions."""
    def _b(label: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(label, callback_data=f"o1:{task_id}:{action}")

    return InlineKeyboardMarkup([
        [_b("OK Aktion übernehmen", "stage2_ok")],
        [_b("Portal", "portal"), _b("Privat", "privat_immo"), _b("Zu-Pruefen", "zupruefen")],
    ])


def _stage2_keyboard_paketzustellung(task_id: str) -> InlineKeyboardMarkup:
    """2-button Stage-2 — for Tier-0 paketzustellung suggestions (CC-Update 2026-05-16).

    Paketzustellung is deterministic enough that the OVERRIDE path is rare.
    Per architect-spec: a single "stattdessen klassifizieren" button reverts
    to the user's Stage-1 classification (Reading B semantics, D-U3).
    """
    def _b(label: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(label, callback_data=f"o1:{task_id}:{action}")

    return InlineKeyboardMarkup([
        [_b("OK Aktion übernehmen", "stage2_ok")],
        [_b("Stattdessen klassifizieren", "stage2_back")],
    ])


def _select_stage2_keyboard(task_id: str, suggested_action: str) -> InlineKeyboardMarkup:
    """Pick the right Stage-2 keyboard variant for the heuristic's suggestion."""
    if suggested_action == "move_paketzustellung":
        return _stage2_keyboard_paketzustellung(task_id)
    return _stage2_keyboard(task_id)


# ---------------------------------------------------------------------------
# Stage-2 trigger logic (Prompt §4.2)
# ---------------------------------------------------------------------------


def _stage2_required(new_classification: str, suggested_action: str) -> bool:
    """Stage 2 is shown when:
       - the override classification is 'geschaeftspost', AND
       - the heuristic.suggested_action starts with 'move_'.
    """
    return (
        new_classification == "geschaeftspost"
        and suggested_action.startswith("move_")
    )


# ---------------------------------------------------------------------------
# UserDecision construction
# ---------------------------------------------------------------------------


def _decision_from_stage1_only(
    parsed: _ParsedCallback,
    plugin_value: str,
    suggested_action: str,
    response_time_ms: int,
) -> UserDecision:
    """For Stage-1-terminating actions (OK / SKIP / non-geschaeftspost-overrides)."""
    if parsed.action == "ok":
        return UserDecision(
            classification=plugin_value,
            suggested_action_confirmed=True,
            final_action=suggested_action,
            response_time_ms=response_time_ms,
            timeout_occurred=False,
        )
    if parsed.action == "skip":
        return UserDecision(
            classification="",
            suggested_action_confirmed=False,
            final_action="skip",
            response_time_ms=response_time_ms,
            timeout_occurred=False,
        )
    # Stage-1 classification override that does NOT trigger stage 2
    # (anything other than 'geschaeftspost' with a move-suggestion)
    return UserDecision(
        classification=parsed.action,
        suggested_action_confirmed=False,
        final_action="keep",  # non-geschaeftspost overrides default to keep mailbox
        response_time_ms=response_time_ms,
        timeout_occurred=False,
    )


def _decision_from_stage2(
    stage1_classification: str,
    stage2_action: str,
    suggested_action: str,
    response_time_ms: int,
) -> UserDecision:
    if stage2_action == "stage2_ok":
        return UserDecision(
            classification=stage1_classification,
            suggested_action_confirmed=True,
            final_action=suggested_action,
            response_time_ms=response_time_ms,
            timeout_occurred=False,
        )
    if stage2_action == "stage2_back":
        # Paketzustellung-variant only — Reading B per D-U3.
        # User saw "OK move_paketzustellung" but said "stattdessen klassifizieren":
        # keep mailbox, record their Stage-1 classification override.
        return UserDecision(
            classification=stage1_classification,
            suggested_action_confirmed=False,
            final_action="keep",
            response_time_ms=response_time_ms,
            timeout_occurred=False,
        )
    final = _STAGE2_TO_FINAL_ACTION.get(stage2_action, "keep")
    return UserDecision(
        classification=stage1_classification,
        suggested_action_confirmed=False,
        final_action=final,
        response_time_ms=response_time_ms,
        timeout_occurred=False,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def send_classification_request(
    task_id: str,
    sender: str,
    subject: str,
    body_snippet: str,
    full_body: str,
    plugin_output: dict,
    heuristic_suggested_action: str,
    heuristic_reason: str,
    chat_id: str,
    bot_token: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> UserDecision:
    """Send Stage-1 message, wait for button-press (with /show support).

    Synchronous wrapper around the async coroutine. Spins up a fresh
    `Application` per call (the worker is sequential), polls Telegram via
    long-polling, processes the first relevant callback for this task_id, then
    cleanly shuts down.
    """
    return asyncio.run(
        _run_conversation(
            task_id=task_id,
            sender=sender,
            subject=subject,
            body_snippet=body_snippet,
            full_body=full_body,
            plugin_output=plugin_output,
            heuristic_suggested_action=heuristic_suggested_action,
            heuristic_reason=heuristic_reason,
            chat_id=chat_id,
            bot_token=bot_token,
            timeout_seconds=timeout_seconds,
        )
    )


async def _run_conversation(
    task_id: str,
    sender: str,
    subject: str,
    body_snippet: str,
    full_body: str,
    plugin_output: dict,
    heuristic_suggested_action: str,
    heuristic_reason: str,
    chat_id: str,
    bot_token: str,
    timeout_seconds: int,
) -> UserDecision:
    """Async core. Builds Application, sends Stage-1, awaits callback, optional Stage-2."""
    started_at_ms = int(time.time() * 1000)

    decision_future: asyncio.Future[UserDecision] = asyncio.get_running_loop().create_future()
    state: dict[str, Any] = {
        "stage": 1,
        "stage1_classification": "",
        "stage1_message_id": None,
        "stage2_message_id": None,
    }

    plugin_value = (plugin_output or {}).get("value", "unklar")
    plugin_confidence = float((plugin_output or {}).get("confidence", 0.0) or 0.0)

    async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if decision_future.done():
            return
        cq = update.callback_query
        if cq is None:
            return
        parsed = parse_callback_query(cq.to_dict())
        if parsed is None or parsed.task_id != task_id:
            await cq.answer(text="not for this task")
            return
        await cq.answer(text=_ack_label(parsed.stage, parsed.action, heuristic_suggested_action))
        elapsed_ms = int(time.time() * 1000) - started_at_ms

        if parsed.stage == 1:
            if parsed.action == "ok":
                decision_future.set_result(_decision_from_stage1_only(
                    parsed=parsed,
                    plugin_value=plugin_value,
                    suggested_action=heuristic_suggested_action,
                    response_time_ms=elapsed_ms,
                ))
                return
            if parsed.action == "skip":
                decision_future.set_result(_decision_from_stage1_only(
                    parsed=parsed,
                    plugin_value=plugin_value,
                    suggested_action=heuristic_suggested_action,
                    response_time_ms=elapsed_ms,
                ))
                return
            # Classification override
            if _stage2_required(parsed.action, heuristic_suggested_action):
                state["stage"] = 2
                state["stage1_classification"] = parsed.action
                msg = await ctx.bot.send_message(
                    chat_id=int(chat_id),
                    text=_stage2_text(heuristic_suggested_action, heuristic_reason),
                    reply_markup=_select_stage2_keyboard(
                        task_id, heuristic_suggested_action
                    ),
                )
                state["stage2_message_id"] = msg.message_id
                return
            # Non-geschaeftspost override → terminate Stage-1
            decision_future.set_result(_decision_from_stage1_only(
                parsed=parsed,
                plugin_value=plugin_value,
                suggested_action=heuristic_suggested_action,
                response_time_ms=elapsed_ms,
            ))
            return

        # stage == 2
        decision_future.set_result(_decision_from_stage2(
            stage1_classification=state["stage1_classification"],
            stage2_action=parsed.action,
            suggested_action=heuristic_suggested_action,
            response_time_ms=elapsed_ms,
        ))

    async def on_show(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if msg is None or not msg.text:
            return
        parts = msg.text.split(maxsplit=1)
        if len(parts) < 2 or parts[1].strip() != task_id:
            # /show for a different task
            return
        # Telegram message limit ~4096 chars; chunk if needed
        chunks = [full_body[i : i + 3500] for i in range(0, max(len(full_body), 1), 3500)]
        for chunk in chunks or [""]:
            await ctx.bot.send_message(chat_id=int(chat_id), text=chunk or "<empty body>")

    application = (
        Application.builder()
        .token(bot_token)
        .build()
    )
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(CommandHandler("show", on_show))

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    try:
        # Send Stage-1
        stage1_msg = await application.bot.send_message(
            chat_id=int(chat_id),
            text=_stage1_text(
                sender=sender,
                subject=subject,
                body_snippet=body_snippet,
                plugin_value=plugin_value,
                plugin_confidence=plugin_confidence,
                heuristic_reason=heuristic_reason,
                suggested_action=heuristic_suggested_action,
                task_id=task_id,
            ),
            reply_markup=_stage1_keyboard(task_id),
        )
        state["stage1_message_id"] = stage1_msg.message_id

        try:
            decision = await asyncio.wait_for(decision_future, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            elapsed_ms = int(time.time() * 1000) - started_at_ms
            log.warning("[timeout] task=%s sender=%s", task_id, sender)
            decision = UserDecision(
                classification="",
                suggested_action_confirmed=False,
                final_action="keep",
                response_time_ms=elapsed_ms,
                timeout_occurred=True,
            )

        return decision
    finally:
        try:
            await application.updater.stop()
        except Exception:
            pass
        try:
            await application.stop()
        except Exception:
            pass
        try:
            await application.shutdown()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI for manual smoke (not used by worker)
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Manual feedback round-trip.")
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--sender", default="test@example.com")
    ap.add_argument("--subject", default="Test")
    ap.add_argument("--body", default="Test body")
    args = ap.parse_args()
    token = os.environ.get("AION_EMAIL_FEEDBACK_BOT_TOKEN")
    chat_id = os.environ.get("AION_EMAIL_FEEDBACK_CHAT_ID")
    if not token or not chat_id:
        raise SystemExit("set AION_EMAIL_FEEDBACK_BOT_TOKEN + _CHAT_ID in env")
    result = send_classification_request(
        task_id=args.task_id,
        sender=args.sender,
        subject=args.subject,
        body_snippet=args.body[:200],
        full_body=args.body,
        plugin_output={"value": "unklar", "confidence": 0.5},
        heuristic_suggested_action="keep",
        heuristic_reason="manual test",
        chat_id=chat_id,
        bot_token=token,
    )
    print(result)


if __name__ == "__main__":
    main()
