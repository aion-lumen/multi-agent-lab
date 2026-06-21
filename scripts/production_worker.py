#!/usr/bin/env python3
"""production_worker.py — Yahoo IMAP + Plugin-CLI + Immo-Heuristik + Telegram-Feedback.

Prompt O.1 implementation, post-Gate-3-signoff (2026-05-16). Replaces the
Phase-3.5c LM-Studio-direct pipeline.

Pipeline per mail:
  1. Pre-flight (Bitwarden, accounts.toml, plugin path, env, Hermes CLI, LM Studio)
  2. IMAP SELECT INBOX, take newest N UIDs (--tranche-size), skip UIDs already in feedback.db
  3. For each UID (newest-first):
       a. Fetch envelope via life-mail IMAPSession (sparse extraction)
       b. hermes kanban create --idempotency-key imap-o1-<uidvalidity>-<uid> --json
          → reuse existing task_id on dedup-hit (fallback via `kanban list` lookup)
       c. claim() the task (cheap insurance against multi-process races)
       d. python3 ~/.hermes/plugins/email-classification/cli.py <task_id>  → JSON
       e. classify_immo(...)  → HeuristicResult
       f. send_classification_request(...)  → UserDecision (1h Telegram timeout)
       g. hermes kanban comment <id> --body '```json …```'  (full O.1 payload)
       h. hermes kanban complete <id> --summary <short>
       i. INSERT into state/feedback.db

External coupling (documented per Gate-1-signoff §3.1):
  - imports IMAPSession + MailEnvelope from ~/Projects/life-mail/scripts/mail_fetcher.py
  - reads ~/Projects/life-mail/accounts.toml for Yahoo creds
  - shells out to `life-mail-passwd <bw_item>` for the Bitwarden lookup
  If life-mail moves, expect breakage at the `sys.path.insert` line below.

Constraints (HARD):
  - read-only on Yahoo (no MOVE, no DELETE, no FLAG changes — Prompt §9)
  - no edits to ~/.hermes/plugins/email-classification/lib/* (Plugin substance sacred)
  - no edits to ~/Projects/life-mail/scripts/mail_fetcher.py (read-only import)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
from email.utils import parsedate_to_datetime
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    import tomllib  # 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

import requests

# Local scripts/ on sys.path so absolute imports work whether we're run from
# ./scripts or the repo root.
_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))

from paths import (  # noqa: E402
    ACCOUNTS_TOML,
    FEEDBACK_DB,
    FOLIO_DB,
    LIFE_MAIL_SCRIPTS,
    LOG_FILE,
    PLUGIN_CLI,
    REPO_ROOT,
    STATE_DIR,
)

# life-mail integration (read-only per Gate-1-signoff §9): IMAPSession + MailEnvelope.
if str(LIFE_MAIL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(LIFE_MAIL_SCRIPTS))

from immo_heuristic import classify_immo, HeuristicResult  # noqa: E402
from domain_actionability import (  # noqa: E402
    classify_domain_actionability,
    load_user_context,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LM_STUDIO_MODELS_URL = "http://127.0.0.1:1234/v1/models"
EXPECTED_LM_MODELS = ("qwen3.6-35b-a3b-ud-mlx", "gemma-4-e4b-it-ud-mlx")

PLUGIN_TIMEOUT_SECONDS = 240  # plugin docs: 47ms heuristic + 21.6s exec + ~42s cascade

SCHEMA_CATEGORIES = {"werbung", "geschaeftspost", "privat", "spam", "unklar"}

STATE_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("worker")


# ---------------------------------------------------------------------------
# Hermes-Kanban CLI wrapper (retained from Phase 3.5c, kept lean)
# ---------------------------------------------------------------------------


_BOARD: Optional[str] = None


def _preflight_board_exists(board_slug: str) -> bool:
    """F.6: verify the kanban board exists BEFORE the worker starts processing.

    Eliminates the `kanban_create_failed` loop that previously surfaced for
    missing boards (was masked by the buggy --idempotency-key fallback).
    """
    proc = subprocess.run(
        ["hermes", "kanban", "boards", "list", "--json"],
        capture_output=True, text=True, timeout=15,
    )
    if proc.returncode != 0:
        log.error("preflight: `hermes kanban boards` failed: %s",
                  (proc.stderr or "")[:300])
        return False
    try:
        boards = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as e:
        log.error("preflight: kanban boards JSON unparseable: %s", e)
        return False
    slugs = [b.get("slug") for b in boards if isinstance(b, dict)]
    if board_slug not in slugs:
        existing = ", ".join(sorted(s for s in slugs if s))
        log.error(
            "preflight: board '%s' not found. Existing boards: %s",
            board_slug, existing,
        )
        return False
    return True


def _run(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    cmd = ["hermes", "kanban"]
    if _BOARD:
        cmd += ["--board", _BOARD]
    cmd += list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def kanban_create(
    title: str,
    body: str,
    assignee: str,
    idempotency_key: str,
) -> Optional[str]:
    """Create a kanban task (or return the existing task_id on dedup-hit).

    Defensive per Engineer-Decision D-E4: parse `--json` stdout. If empty or
    parses to no `id`, fall back to `kanban list --idempotency-key`.
    """
    r = _run(
        "create",
        title,
        "--body", body,
        "--assignee", assignee,
        "--idempotency-key", idempotency_key,
        "--json",
    )
    if r.returncode != 0:
        log.error("kanban create failed: %s", (r.stderr or "")[:300])
        return None
    stdout = (r.stdout or "").strip()
    if stdout:
        try:
            data = json.loads(stdout)
            tid = data.get("id") if isinstance(data, dict) else None
            if tid:
                return tid
        except json.JSONDecodeError:
            log.warning("kanban create stdout not JSON: %r", stdout[:120])
    # F.6: Removed buggy --idempotency-key fallback (Phase-1a-§3.3 Option-A).
    # `hermes kanban list --idempotency-key` is not a valid flag in v0.13 — the
    # original fallback was based on misread docs. Most failures here are
    # missing-board (now caught by _preflight_board_exists in main()).
    log.error("kanban create empty stdout — see stderr above for cause")
    return None


def kanban_claim(task_id: str) -> bool:
    r = _run("claim", task_id)
    if r.returncode != 0:
        log.warning("claim %s failed: %s", task_id, (r.stderr or "").strip()[:200])
        return False
    return True


def kanban_comment(task_id: str, body: str, author: str = "production_worker") -> None:
    _run("comment", task_id, body, "--author", author)


def kanban_complete(task_id: str, summary: str) -> None:
    _run("complete", task_id, "--summary", summary[:300])


def kanban_block(task_id: str, reason: str) -> None:
    _run("block", task_id, reason)


def post_json_comment(task_id: str, payload: dict, author: str = "production_worker") -> None:
    body = "```json\n" + json.dumps(payload, indent=2, ensure_ascii=False) + "\n```"
    kanban_comment(task_id, body, author=author)


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------


def _preflight(args: argparse.Namespace) -> list[str]:
    """Return a list of failure-messages. Empty list means OK."""
    failures: list[str] = []

    if args.imap_fixture:
        if not Path(args.imap_fixture).exists():
            failures.append(f"--imap-fixture path does not exist: {args.imap_fixture}")
    else:
        # Real-Yahoo path
        if not ACCOUNTS_TOML.exists():
            failures.append(f"missing accounts.toml at {ACCOUNTS_TOML}")
        else:
            try:
                accounts = tomllib.loads(ACCOUNTS_TOML.read_text(encoding="utf-8"))
                if args.account not in (accounts.get("accounts") or {}):
                    failures.append(
                        f"accounts.toml has no [accounts.{args.account}] entry"
                    )
            except (tomllib.TOMLDecodeError, OSError) as e:
                failures.append(f"accounts.toml unreadable: {e}")
        if shutil.which("bw") is None:
            failures.append("`bw` (Bitwarden CLI) not in PATH")
        if shutil.which("life-mail-passwd") is None:
            failures.append("`life-mail-passwd` helper not in PATH")

    if not PLUGIN_CLI.exists():
        failures.append(f"plugin CLI missing at {PLUGIN_CLI}")

    if shutil.which("hermes") is None:
        failures.append("`hermes` CLI not in PATH")

    # F.7: silent-Mode überspringt Telegram-Approval → keine Telegram-env-vars nötig.
    if not args.no_telegram and args.mode != "silent":
        if not os.environ.get("AION_EMAIL_FEEDBACK_BOT_TOKEN"):
            failures.append("AION_EMAIL_FEEDBACK_BOT_TOKEN missing in env (~/.hermes/.env)")
        if not os.environ.get("AION_EMAIL_FEEDBACK_CHAT_ID"):
            failures.append("AION_EMAIL_FEEDBACK_CHAT_ID missing in env")

    # LM Studio is required for non-dry-run real runs (plugin-CLI calls it).
    if not args.dry_run:
        try:
            resp = requests.get(LM_STUDIO_MODELS_URL, timeout=5)
            if resp.status_code == 200:
                models = {m.get("id") for m in (resp.json().get("data") or [])}
                missing = [m for m in EXPECTED_LM_MODELS if m not in models]
                if missing:
                    log.warning(
                        "LM Studio responding but missing expected models: %s "
                        "(plugin may still load via lms-on-demand)",
                        missing,
                    )
            else:
                failures.append(
                    f"LM Studio reachable but returned HTTP {resp.status_code}"
                )
        except requests.RequestException as e:
            failures.append(f"LM Studio unreachable at {LM_STUDIO_MODELS_URL}: {e}")

    return failures


# ---------------------------------------------------------------------------
# IMAP — real session or mock fixture
# ---------------------------------------------------------------------------


VALID_ACCOUNTS = ("yahoo", "gmail", "mirhamed")


def _load_account(account_id: str) -> dict:
    """Load credentials for a given accounts.toml key.

    F.6: was _load_yahoo_account(). Caller passes account_id (e.g. 'yahoo',
    'gmail', 'mirhamed') which must match a [accounts.<id>] section in
    life-mail's accounts.toml.
    """
    raw = tomllib.loads(ACCOUNTS_TOML.read_text(encoding="utf-8"))
    accounts = raw.get("accounts") or {}
    if account_id not in accounts:
        raise KeyError(
            f"account '{account_id}' has no [accounts.{account_id}] entry in {ACCOUNTS_TOML}"
        )
    return accounts[account_id]


def _open_imap_session(args: argparse.Namespace):
    """Return a context-managed IMAP session.

    Two modes:
      - args.imap_fixture: MockIMAPSession (tests/mocks/imap.py) reading JSON
      - else: real life-mail IMAPSession with Bitwarden creds
    """
    if args.imap_fixture:
        # Late import so production runs don't pull pytest fixtures.
        # Worker may be launched from any cwd, so push repo root onto sys.path
        # explicitly before resolving `tests.mocks.imap`.
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from tests.mocks.imap import MockIMAPSession  # type: ignore
        return MockIMAPSession(fixture_path=Path(args.imap_fixture))
    # Real path
    from mail_fetcher import IMAPSession  # type: ignore[import-not-found]
    acct = _load_account(args.account)
    return IMAPSession(
        host=acct["host"],
        port=acct["port"],
        login=acct["login"],
        bw_item=acct["bw_item"],
    )


# ---------------------------------------------------------------------------
# Mail body → kanban task body (markdown)
# ---------------------------------------------------------------------------


def build_task_body_from_envelope(env) -> str:
    """Format a MailEnvelope into the markdown shape that the plugin's
    parse_email_body() expects: bullet-list lines `- **Sender:** …`,
    `- **Subject:** …`, plus `### Body` heading + fenced code block.
    Plugin regex source: ~/.hermes/plugins/email-classification/lib/pipeline.py:28-37.
    """
    sender_display = (f"{env.from_name} <{env.from_addr}>".strip()
                      if env.from_name else env.from_addr)
    body_text = (env.body_text or "").strip()
    return (
        f"- **Sender:** {sender_display}\n"
        f"- **Subject:** {env.subject or '(no subject)'}\n"
        f"- **Date:** {env.date or ''}\n"
        f"- **Message-ID:** {env.message_id or ''}\n\n"
        f"### Body (sparse extraction, ≤2000 chars)\n\n"
        f"```\n{body_text}\n```\n"
    )


# ---------------------------------------------------------------------------
# Plugin CLI subprocess
# ---------------------------------------------------------------------------


def call_plugin(task_id: str) -> dict:
    """Run the email-classification plugin CLI for one task.

    Per cli.py docstring: exit 0 = success, exit 1 = pipeline-error with valid
    JSON, exit 2 = usage error. We parse stdout for 0 and 1; raise on 2.
    """
    proc = subprocess.run(
        [sys.executable, str(PLUGIN_CLI), task_id],
        capture_output=True,
        text=True,
        timeout=PLUGIN_TIMEOUT_SECONDS,
    )
    if proc.returncode == 2:
        raise RuntimeError(
            f"plugin CLI usage error (exit 2) for task {task_id}: "
            f"{(proc.stderr or '')[:300]}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"plugin CLI returned non-JSON for task {task_id}: "
            f"{(proc.stdout or '')[:300]} (err={e})"
        ) from e


# ---------------------------------------------------------------------------
# O.1 payload (the JSON-block posted as kanban comment)
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def build_o1_payload(
    task_id: str,
    env,
    uidvalidity: int,
    plugin_output: dict,
    heuristic_result: HeuristicResult,
    user_decision,  # UserDecision
    timestamps: dict,
) -> dict:
    """Produce the §G1-3.4 JSON payload (schema_version=o1.0).

    Backward-compatible with the 3.5c build_output shape: keeps task_id,
    outcome, profile, result, evidence, tool_trace, stats at the top level.
    """
    plugin_value = (plugin_output or {}).get("value", "unklar")
    plugin_conf = float((plugin_output or {}).get("confidence", 0.0) or 0.0)
    plugin_reasoning = (plugin_output or {}).get("reasoning", "")

    outcome = (
        "completed_via_telegram_timeout"
        if getattr(user_decision, "timeout_occurred", False)
        else (
            "skipped_via_telegram_skip_button"
            if getattr(user_decision, "final_action", "") == "skip"
            else "completed_with_telegram_feedback"
        )
    )
    final_classification = (
        getattr(user_decision, "classification", "") or plugin_value
    )

    return {
        "schema_version": "o1.0",
        "task_id": task_id,
        "imap_uid": env.uid,
        "uidvalidity": uidvalidity,
        "outcome": outcome,
        "profile": "production_worker_o1",
        "plugin_output": plugin_output,
        "heuristic_result": asdict(heuristic_result),
        "user_decision": asdict(user_decision),
        "envelope": {
            "from_addr": env.from_addr,
            "from_name": env.from_name,
            "subject": env.subject,
            "date": env.date,
            "message_id": env.message_id,
        },
        "result": {
            "type": "classification",
            "value": final_classification,
            "confidence": plugin_conf,
            "reasoning_summary": (
                f"Plugin: {plugin_value} ({plugin_conf:.2f}); "
                f"Heuristik: {heuristic_result.suggested_action} ({heuristic_result.confidence}); "
                f"User: {getattr(user_decision, 'final_action', '?')}."
            )[:300],
        },
        "evidence": [
            {"type": "plugin_output", "value": plugin_value, "confidence": plugin_conf},
            {
                "type": "heuristic_match",
                "action": heuristic_result.suggested_action,
                "reason": heuristic_result.reason,
            },
            {
                "type": "user_decision",
                "classification": getattr(user_decision, "classification", ""),
                "final_action": getattr(user_decision, "final_action", ""),
                "timeout": getattr(user_decision, "timeout_occurred", False),
            },
        ],
        "tool_trace": [],
        "stats": {
            "plugin_calls": 1,
            "telegram_messages": 1 if not getattr(user_decision, "timeout_occurred", False) else 0,
            "wall_clock_ms": max(
                0,
                (timestamps.get("kanban_completed_at_ms", 0)
                 - timestamps.get("imap_fetched_at_ms", 0)),
            ),
        },
        "timestamps": {
            "imap_fetched_at": timestamps.get("imap_fetched_at"),
            "plugin_completed_at": timestamps.get("plugin_completed_at"),
            "telegram_sent_at": timestamps.get("telegram_sent_at"),
            "user_responded_at": timestamps.get("user_responded_at"),
            "kanban_completed_at": timestamps.get("kanban_completed_at"),
        },
    }


# ---------------------------------------------------------------------------
# feedback.db writer
# ---------------------------------------------------------------------------


def _envelope_date_iso(raw: str | None) -> str | None:
    """F.7-BUG-A: parse RFC2822 envelope.date → ISO-8601-with-TZ.

    Liefert None bei ungültigem Input — Folio fallback auf created_at.
    String-Sort-Konsistenz: ISO sortiert lexikographisch korrekt, RFC2822 nicht.
    """
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        return None


def write_feedback_row(
    payload: dict,
    env,
    body_hash: str,
    body_excerpt: str,
    account_id: str,
    dry_run: bool,
) -> int | None:
    """Returns the new feedback.id (lastrowid) — None bei dry_run oder
    INSERT-OR-IGNORE-Skip (Dup-UID)."""
    if dry_run:
        log.info("[dry-run] would INSERT feedback row account=%s uid=%d sender=%s",
                 account_id, env.uid, env.from_addr)
        return None
    user = payload["user_decision"]
    heur = payload["heuristic_result"]
    plugin = payload["plugin_output"] or {}
    da = payload.get("domain_actionability") or {}  # F.8
    # 2026-06-05 Marker-Persistierungs-Fix: feedback.heuristic_markers
    # bekommt heuristic.matched_markers + classify_domain_actionability-Marker
    # (out_of_corridor:*, out_of_country:*, blocked_by:*, user_override:*),
    # deduped via dict.fromkeys (order-preserving). Vorher: nur heur-Marker,
    # Korridor-Marker verloren ausserhalb der Kanban-Payload.
    _combined_markers = list(dict.fromkeys(
        (heur.get("matched_markers") or []) + (da.get("markers") or [])
    ))
    row = (
        payload["task_id"],
        account_id,
        env.uid,
        env.from_addr,
        env.subject or "",
        body_hash,
        plugin.get("value"),
        plugin.get("confidence"),
        json.dumps(plugin.get("evidence", []), ensure_ascii=False),
        heur.get("suggested_action"),
        heur.get("reason"),
        heur.get("confidence"),
        json.dumps(_combined_markers, ensure_ascii=False),
        user.get("classification"),
        user.get("final_action"),
        int(bool(user.get("suggested_action_confirmed"))),
        int(user.get("response_time_ms") or 0),
        int(bool(user.get("timeout_occurred"))),
        payload["timestamps"]["imap_fetched_at"],
        _envelope_date_iso(getattr(env, "date", None)),
        da.get("domain"),                  # F.8
        da.get("actionability"),           # F.8 — frozen-at-insert
        None,                              # effective_actionability — Folio berechnet dynamisch
        body_excerpt,                      # I2-Fix 2026-05-26: validator reads from here, not body_hash
    )
    with sqlite3.connect(FEEDBACK_DB) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO feedback "
            "(task_id, account_id, imap_uid, sender, subject, body_hash, "
            "plugin_value, plugin_confidence, plugin_evidence, "
            "heuristic_suggested_action, heuristic_reason, heuristic_confidence, "
            "heuristic_markers, user_classification, user_final_action, "
            "suggested_action_confirmed, response_time_ms, timeout_occurred, "
            "created_at, mail_date, domain, actionability, effective_actionability, "
            "body_excerpt) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            row,
        )
        conn.commit()
        # lastrowid ist 0 bei OR-IGNORE-Skip (Dup-UID). Caller wertet None aus.
        return cur.lastrowid if cur.lastrowid else None


def loaded_processed_uids(account_id: str) -> set[int]:
    """Return the set of imap_uid values already in feedback.db for this account.

    F.6: scoped per account_id. Yahoo-UID 12345 != Gmail-UID 12345 (per IMAP-spec
    UIDs sind nur per-account/per-uidvalidity eindeutig).
    """
    if not FEEDBACK_DB.exists():
        return set()
    try:
        with sqlite3.connect(FEEDBACK_DB) as conn:
            return {
                r[0] for r in conn.execute(
                    "SELECT imap_uid FROM feedback WHERE account_id = ?",
                    (account_id,),
                )
            }
    except sqlite3.OperationalError:
        return set()


# ---------------------------------------------------------------------------
# Telegram dispatch
# ---------------------------------------------------------------------------


def _call_telegram(
    task_id: str,
    env,
    plugin_output: dict,
    heuristic_result: HeuristicResult,
    mode: str,
    dry_run: bool,
    no_telegram: bool,
):
    """Return a UserDecision. Honors --mode=silent, --dry-run and --no-telegram.

    F.7 silent-mode: auto-confirms heuristic suggestion (User reviewt + korrigiert
    später in Folio via F.5 Re-Classify-Flow). Im Gegensatz zu --no-telegram (das
    final_action='keep' setzt — Audit-default) übernimmt silent die Heuristik.
    """
    from feedback_telegram import send_classification_request, UserDecision  # noqa: WPS433

    if mode == "silent":
        log.info(
            "[mode=silent] auto-confirm task=%s sender=%s suggested=%s",
            task_id, env.from_addr, heuristic_result.suggested_action,
        )
        return UserDecision(
            classification=heuristic_result.suggested_action,
            suggested_action_confirmed=True,
            final_action=heuristic_result.suggested_action,
            response_time_ms=0,
            timeout_occurred=False,
        )

    if no_telegram or dry_run:
        prefix = "[no-telegram]" if no_telegram else "[dry-run]"
        log.info(
            "%s would send Stage-1 task=%s sender=%s suggested=%s",
            prefix, task_id, env.from_addr, heuristic_result.suggested_action,
        )
        return UserDecision(
            classification="",
            suggested_action_confirmed=False,
            final_action="keep",
            response_time_ms=0,
            timeout_occurred=False,
        )

    bot_token = os.environ.get("AION_EMAIL_FEEDBACK_BOT_TOKEN", "")
    chat_id = os.environ.get("AION_EMAIL_FEEDBACK_CHAT_ID", "")
    snippet = (env.body_text or "")[:200]
    return send_classification_request(
        task_id=task_id,
        sender=env.from_addr,
        subject=env.subject or "",
        body_snippet=snippet,
        full_body=env.body_text or "",
        plugin_output=plugin_output,
        heuristic_suggested_action=heuristic_result.suggested_action,
        heuristic_reason=heuristic_result.reason,
        chat_id=chat_id,
        bot_token=bot_token,
    )


# ---------------------------------------------------------------------------
# Per-envelope processing
# ---------------------------------------------------------------------------


def process_envelope(
    env,
    uidvalidity: int,
    args: argparse.Namespace,
) -> str:
    """Returns an outcome label ("processed", "skip", "dry_run_only", "block:...")."""
    task_body = build_task_body_from_envelope(env)
    title = f"[O.1] {(env.subject or '(no subject)')[:80]}"
    idempotency_key = f"imap-o1-{uidvalidity}-{env.uid}"
    body_hash = hashlib.sha256((env.body_text or "")[:5000].encode("utf-8")).hexdigest()
    body_excerpt = (env.body_text or "")[:1000]

    ts_fetched = _utc_now_iso()
    ts_fetched_ms = int(time.time() * 1000)

    if args.dry_run:
        log.info("[dry-run] would create kanban task title=%r idempotency-key=%s",
                 title, idempotency_key)
        task_id = "dryrun-task-id"
    else:
        task_id = kanban_create(
            title=title,
            body=task_body,
            assignee=args.assignee,
            idempotency_key=idempotency_key,
        )
        if not task_id:
            log.error("uid=%d kanban_create returned no task_id", env.uid)
            return "block:kanban_create_failed"
        kanban_claim(task_id)  # cheap insurance per D-E5

    # Plugin CLI
    if args.dry_run:
        log.info("[dry-run] would call plugin CLI for task=%s", task_id)
        plugin_output = {
            "value": "unklar",
            "confidence": 0.0,
            "reasoning": "dry-run, plugin not called",
            "evidence": [],
        }
    else:
        try:
            plugin_output = call_plugin(task_id)
        except Exception as e:
            log.error("plugin call failed for task=%s: %s", task_id, e)
            kanban_block(task_id, f"plugin error: {e}")
            return "block:plugin_error"
    ts_plugin = _utc_now_iso()

    # Heuristik (legacy F.6 — sets heuristic_suggested_action für silent-mode-compat)
    heuristic = classify_immo(
        sender=f"{env.from_name} <{env.from_addr}>".strip(),
        subject=env.subject or "",
        body=env.body_text or "",
        plugin_value=plugin_output.get("value", "unklar"),
        plugin_confidence=float(plugin_output.get("confidence", 0.0) or 0.0),
    )

    # F.8 — 2-Achsen-Classification (Domain × Actionability)
    # 2026-05-28 Wunsch 3: heuristic.matched_markers (enthält plz_country falls
    # immo-Mail mit PLZ-Match) durchgereicht für Step 7 PLZ-Country-Filter.
    user_context = load_user_context()
    classification_f8 = classify_domain_actionability(
        sender=f"{env.from_name} <{env.from_addr}>".strip(),
        subject=env.subject or "",
        mail_date=getattr(env, "date", None),
        plugin_class=plugin_output.get("value"),
        user_context=user_context,
        heuristic_markers=heuristic.matched_markers,
        # Bauteil-7 G5 (2026-06-09): body fuer Auto-Reply-Detection.
        body=getattr(env, "body_text", None) or getattr(env, "body", None),
    )

    ts_tg_sent = _utc_now_iso()
    user = _call_telegram(
        task_id=task_id,
        env=env,
        plugin_output=plugin_output,
        heuristic_result=heuristic,
        mode=args.mode,
        dry_run=args.dry_run,
        no_telegram=args.no_telegram,
    )
    ts_user = _utc_now_iso()

    payload = build_o1_payload(
        task_id=task_id,
        env=env,
        uidvalidity=uidvalidity,
        plugin_output=plugin_output,
        heuristic_result=heuristic,
        user_decision=user,
        timestamps={
            "imap_fetched_at": ts_fetched,
            "imap_fetched_at_ms": ts_fetched_ms,
            "plugin_completed_at": ts_plugin,
            "telegram_sent_at": ts_tg_sent,
            "user_responded_at": ts_user,
            "kanban_completed_at": _utc_now_iso(),
            "kanban_completed_at_ms": int(time.time() * 1000),
        },
    )
    # F.8 Attach 2-Achsen-Classification an payload für DB-Write
    payload["domain_actionability"] = {
        "domain": classification_f8.domain,
        "actionability": classification_f8.actionability,
        "reason": classification_f8.reason,
        "confidence": classification_f8.confidence,
        "markers": classification_f8.matched_markers,
        "plugin_class_hint": classification_f8.plugin_class_hint,
    }

    if args.dry_run:
        log.info("[dry-run] would post JSON comment + complete task=%s outcome=%s",
                 task_id, payload["outcome"])
        log.info("[dry-run] payload preview: %s",
                 json.dumps({k: payload[k] for k in
                            ("schema_version", "imap_uid", "outcome", "envelope")},
                            ensure_ascii=False))
    else:
        post_json_comment(task_id, payload, author="production_worker")
        # SKIP-button doesn't complete the task per Prompt §4.2 (no kanban completion)
        if user.final_action == "skip" and not user.timeout_occurred:
            log.info("[skip] user skipped task=%s — no kanban completion", task_id)
        else:
            kanban_complete(
                task_id,
                summary=f"{payload['result']['value']} → {user.final_action}",
            )

    # feedback.db write — per Engineer-Decision D-E7, also write rows for skip.
    feedback_id = write_feedback_row(
        payload=payload,
        env=env,
        body_hash=body_hash,
        body_excerpt=body_excerpt,
        account_id=args.account,
        dry_run=args.dry_run,
    )

    # 2026-06-07 Pre-Bauteil Pipeline-Persistenz: Per-Mail-Log fuer
    # Heuristik-Stimme. No-op wenn run_uuid None (CLI-Direkt-Aufruf) oder
    # bei dry_run.
    if feedback_id is not None and not args.dry_run:
        from folio_log_writer import write_log, get_run_uuid_from_env_or_args
        run_uuid = get_run_uuid_from_env_or_args(getattr(args, "run_uuid", None))
        if run_uuid:
            da = payload.get("domain_actionability") or {}
            subj_short = (env.subject or "")[:60]
            write_log(
                run_uuid, "heuristik", "classified",
                f"#{feedback_id} · {da.get('domain')}/{da.get('actionability')} · {subj_short}",
                mail_id=feedback_id,
            )

    return "dry_run_only" if args.dry_run else "processed"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _verify_paketzustellung_folder(session) -> bool:
    """Probe the Paketzustellung folder; re-select INBOX afterwards.

    Per CC-Update 2026-05-16: the smoke must verify the folder exists so
    that `mailbox_executor` later doesn't hang on a missing target. This
    worker never writes to Paketzustellung — only confirms it's reachable.
    Returns True if found; False (and logs warning) if select_folder raises.
    """
    try:
        session.select_folder("Paketzustellung")
        log.info("Paketzustellung folder verified")
        ok = True
    except Exception as e:  # noqa: BLE001  — any IMAP error means missing/inaccessible
        log.warning(
            "Paketzustellung folder NOT reachable (%s) — architect must create "
            "in Yahoo Web UI before mailbox_executor goes live",
            e,
        )
        ok = False
    # Always reselect INBOX so the rest of the loop has the right context
    try:
        session.select_folder("INBOX")
    except Exception as e:  # noqa: BLE001
        log.error("Failed to re-select INBOX after Paketzustellung probe: %s", e)
        raise
    return ok


def _iter_uids_newest_first(
    session,
    tranche_size: int,
    already_done: set[int] | None = None,
) -> tuple[Iterable[int], int]:
    """Return (uids_iter, uidvalidity). uids_iter yields newest-first.

    F.7-Bugfix-8: filter `already_done`-Set BEFORE slicing to tranche_size, so
    Worker processes EXACTLY tranche_size new mails (or fewer if exhausted),
    not "newest-N-minus-already-seen" which was effectively zero for re-runs.
    Main-loop dedup-check stays as defense-in-depth guard-rail.
    """
    total, uidvalidity = session.select_folder("INBOX")
    all_uids = session.search_uids(since_uid=0, skip_classified=False)
    done = already_done or set()
    # all_uids ascending. reversed() = newest-first. Filter dedup pre-slice.
    new_uids = [u for u in reversed(all_uids) if u not in done]
    target = new_uids[:tranche_size]
    return iter(target), uidvalidity


def main() -> int:
    global _BOARD
    ap = argparse.ArgumentParser(
        prog="production_worker",
        description=(
            "Prompt O.1 — Yahoo IMAP ingest + plugin classification + "
            "Immo-heuristic + Telegram-feedback. Read-only on the mailbox."
        ),
    )
    ap.add_argument("--board", default=None,
                    help="(deprecated) Hermes kanban board slug — wenn gesetzt, "
                         "wird per-mail eine Hermes-Task angelegt; sonst skip "
                         "(Cleanup 2026-05-27: nicht mehr required, Pipeline-Redesign).")
    ap.add_argument(
        "--account",
        required=True,
        choices=VALID_ACCOUNTS,
        help="accounts.toml key (yahoo|gmail|mirhamed). Determines IMAP creds + feedback.account_id.",
    )
    ap.add_argument("--mode", choices=("learning", "silent", "trust", "audit"),
                    default="learning",
                    help="learning (Telegram-Approval) | silent (auto-confirm heuristic, F.7) | trust/audit (Phase 4.5+)")
    ap.add_argument("--tranche-size", type=int, default=500,
                    help="number of newest mails to process")
    ap.add_argument("--dry-run", action="store_true", default=False,
                    help="skip all external side-effects (kanban, plugin, telegram, db write)")
    ap.add_argument("--no-telegram", action="store_true", default=False,
                    help="real run but skip Telegram (decision defaults to keep)")
    ap.add_argument("--imap-fixture", type=Path, default=None,
                    help="path to a JSON file of MailEnvelope dicts (offline smoke)")
    ap.add_argument("--assignee", default="production_worker",
                    help="kanban assignee for created tasks")
    # 2026-06-07 Pre-Bauteil Pipeline-Persistenz: run_uuid von folio
    # manager.ts; CLI-arg + env-Fallback FOLIO_RUN_UUID. None bei
    # CLI-Direkt-Aufruf — folio_log_writer macht no-op.
    ap.add_argument("--run-uuid", default=None,
                    help="folio worker_runs.run_uuid (für Cross-DB-Logs)")
    args = ap.parse_args()
    _BOARD = args.board

    if args.mode in ("trust", "audit"):
        raise NotImplementedError("trust/audit mode is Phase 4.5")

    log.info("=" * 60)
    log.info(
        "production_worker O.1 account=%s board=%s mode=%s tranche=%d "
        "dry_run=%s no_telegram=%s imap_fixture=%s",
        args.account, args.board, args.mode, args.tranche_size,
        args.dry_run, args.no_telegram, args.imap_fixture,
    )

    # Cleanup 2026-05-27: Board-Preflight nur wenn --board explizit gesetzt.
    # Ohne --board läuft der Worker ohne Hermes-Kanban-Side-Effects.
    if args.board and not _preflight_board_exists(args.board):
        return 2

    failures = _preflight(args)
    if failures:
        log.error("Pre-flight failed:")
        for f in failures:
            log.error("  - %s", f)
        return 2

    counts: dict[str, int] = {}
    processed = 0
    t0 = time.time()
    already_done = loaded_processed_uids(args.account)
    if already_done:
        log.info("feedback.db already has %d processed UIDs — skipping those",
                 len(already_done))

    with _open_imap_session(args) as session:
        # Real-Yahoo-only: verify the Paketzustellung folder exists (CC-Update
        # 2026-05-16). Skipped in --imap-fixture and --dry-run modes (mock
        # session has no notion of named folders beyond INBOX, and dry-run
        # avoids unnecessary IMAP commands). Skipped for non-yahoo accounts —
        # the Paketzustellung target folder is a Yahoo-only concept; gmail and
        # other accounts don't have or need it.
        if not args.imap_fixture and not args.dry_run and args.account == "yahoo":
            _verify_paketzustellung_folder(session)
        uids_iter, uidvalidity = _iter_uids_newest_first(
            session, args.tranche_size, already_done
        )
        for uid in uids_iter:
            if uid in already_done:
                log.info("[skip] uid=%d already in feedback.db", uid)
                counts["skip_dedup"] = counts.get("skip_dedup", 0) + 1
                continue
            envs = list(session.fetch_envelopes([uid]))
            if not envs:
                log.warning("[skip] uid=%d fetch yielded no envelope", uid)
                counts["skip_no_envelope"] = counts.get("skip_no_envelope", 0) + 1
                continue
            env = envs[0]
            try:
                outcome = process_envelope(env, uidvalidity, args)
            except Exception as exc:  # noqa: BLE001
                log.exception("process_envelope failed for uid=%d: %s", uid, exc)
                outcome = "exception"
            counts[outcome] = counts.get(outcome, 0) + 1
            processed += 1
            log.info("[%d/%d] uid=%d → %s",
                     processed, args.tranche_size, uid, outcome)

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(
        "DONE processed=%d elapsed=%.1fs",
        processed, elapsed,
    )
    for k, v in sorted(counts.items()):
        log.info("  %-30s %d", k, v)

    # 2026-06-07 Pre-Bauteil Pipeline-Persistenz: Run-Ende-Summary.
    # geprueft = alle erfolgreich klassifizierten (counts['processed']).
    # uebernommen/actionable/archive_silent kommen erst via validator_batch +
    # auto_uebernahme — production_worker liefert die heuristik-Stimmen-
    # Klassifikation, das ist 'actionable' fuer nicht-silent + sonst silent.
    # Aggregat-Berechnung cross-DB: liest die feedback-rows dieses Runs
    # zurueck (filter via created_at > run-start) und zaehlt aus.
    from folio_log_writer import write_summary, get_run_uuid_from_env_or_args
    run_uuid = get_run_uuid_from_env_or_args(getattr(args, "run_uuid", None))
    mail_ids: list[int] = []
    if run_uuid:
        # Mail-IDs dieses Runs aus worker_run_logs holen (das was wir
        # selbst geschrieben haben) — sicherer als time-based-Query.
        try:
            import sqlite3 as _sql
            from pathlib import Path as _Path
            folio_conn = _sql.connect(f"file:{FOLIO_DB}?mode=ro", uri=True)
            mail_ids = [
                r[0] for r in folio_conn.execute(
                    "SELECT mail_id FROM worker_run_logs WHERE run_uuid=? AND event_type='classified' AND mail_id IS NOT NULL",
                    (run_uuid,),
                ).fetchall()
            ]
            folio_conn.close()
        except Exception as e:  # noqa: BLE001
            log.warning("summary: folio-read failed: %s", e)
            mail_ids = []

        # Klassifikationen + reason_breakdown aus feedback.db lesen
        reason_breakdown: dict[str, int] = {}
        sample: list[dict] = []
        actionable_n = silent_n = uebernommen_n = 0
        try:
            with sqlite3.connect(FEEDBACK_DB) as fconn:
                if mail_ids:
                    placeholders = ",".join("?" for _ in mail_ids)
                    for r in fconn.execute(
                        f"SELECT id, subject, sender, domain, actionability, heuristic_markers "
                        f"FROM feedback WHERE id IN ({placeholders})",
                        mail_ids,
                    ).fetchall():
                        fid, subj, sender, dom, act, markers = r
                        if act == "actionable":
                            actionable_n += 1
                        elif act in ("archive-silent", "archive"):
                            silent_n += 1
                        elif act == "uebernommen":
                            uebernommen_n += 1
                        # reason_breakdown aus heuristic_markers — Block-Marker
                        # nach Praefix bucketten.
                        try:
                            arr = json.loads(markers) if markers else []
                        except Exception:  # noqa: BLE001
                            arr = []
                        for m in arr:
                            for prefix in ("out_of_corridor", "tier1:projektiert",
                                           "tier1:zwangsversteigerung",
                                           "tier1:price_on_request", "decay:",
                                           "blocked_by:"):
                                if m.startswith(prefix):
                                    key = prefix.rstrip(":").replace("tier1:", "")
                                    reason_breakdown[key] = reason_breakdown.get(key, 0) + 1
                                    break
                        if len(sample) < 15:
                            sample.append({
                                "id": fid, "subject": (subj or "")[:80],
                                "sender": sender, "tag": f"{dom}/{act}",
                            })
        except Exception as e:  # noqa: BLE001
            log.warning("summary: feedback-read failed: %s", e)

        write_summary(
            run_uuid,
            geprueft=len(mail_ids),
            uebernommen=uebernommen_n,
            actionable=actionable_n,
            archive_silent=silent_n,
            reason_breakdown=reason_breakdown if reason_breakdown else None,
            worker_imports_sample=sample if sample else None,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
