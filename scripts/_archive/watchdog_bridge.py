#!/usr/bin/env python3
"""
Watchdog-Bridge — Polling-Service fuer Hermes-Kanban-User-Eskalationen.

Liest task_events aus allen aktiven Kanban-DBs und sendet Telegram-Pings
via HERMES_WATCHDOG_BOT_TOKEN bei blocked/gave_up/crashed-Events
mit assignee=user.

Bewusst getrennt von Hermes' eingebautem Telegram-Adapter, der fest
TELEGRAM_BOT_TOKEN (ImmoAlert) liest.

Deployment: launchd-Service, laeuft im Hintergrund unter User-Account.
"""

import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Iterable

import requests
from dotenv import load_dotenv


ENV_PATHS = [
    Path.home() / ".hermes" / ".env",
    Path.home() / "Projects" / "aion-lumen" / "council" / ".env",
]

BOARDS_ROOT = Path.home() / ".hermes" / "kanban" / "boards"
DEFAULT_BOARD_DB = Path.home() / ".hermes" / "kanban.db"

STATE_DIR = Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "state"
STATE_FILE = STATE_DIR / "watchdog_state.json"
LOG_FILE = STATE_DIR / "watchdog.log"

POLL_INTERVAL_SECONDS = 30
ESCALATION_KINDS = ("blocked", "gave_up", "crashed")


STATE_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("watchdog")


def load_env() -> tuple[str, str]:
    for path in ENV_PATHS:
        if not path.exists():
            continue
        load_dotenv(path)
        token = os.environ.get("HERMES_WATCHDOG_BOT_TOKEN")
        chat = os.environ.get("HERMES_WATCHDOG_CHAT_ID")
        if token and chat:
            log.info("Env loaded from %s", path)
            return token, chat
    log.error("HERMES_WATCHDOG_BOT_TOKEN/CHAT_ID not found in: %s",
              [str(p) for p in ENV_PATHS])
    sys.exit(1)


TOKEN, CHAT_ID = load_env()


def load_state() -> dict:
    """State schema (per board):
      {<board_slug>: {"last_event_id": <int>, "notified_tasks": [<task_id>, ...]}}
    Legacy schema (just int per board) is auto-migrated.
    """
    if STATE_FILE.exists():
        try:
            raw = json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            log.warning("State file corrupt, starting fresh")
            return {}
        # Migrate legacy int-per-board to new structure
        out: dict = {}
        for k, v in raw.items():
            if isinstance(v, int):
                out[k] = {"last_event_id": v, "notified_tasks": []}
            elif isinstance(v, dict):
                out[k] = {
                    "last_event_id": int(v.get("last_event_id", 0)),
                    "notified_tasks": list(v.get("notified_tasks", [])),
                }
        return out
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def discover_kanban_dbs() -> Iterable[tuple[str, Path]]:
    """Find all active Kanban DBs (default + named boards, skip _archived)."""
    if DEFAULT_BOARD_DB.exists():
        yield "default", DEFAULT_BOARD_DB
    if BOARDS_ROOT.exists():
        for board_dir in BOARDS_ROOT.iterdir():
            if not board_dir.is_dir():
                continue
            if board_dir.name.startswith("_"):
                continue
            db = board_dir / "kanban.db"
            if db.exists():
                yield board_dir.name, db


def fetch_new_events(db_path: Path, since_event_id: int) -> list[tuple]:
    """Fetch new escalation events for tasks assigned to user.

    Schema (verified 2026-05-09):
      task_events(id, task_id, run_id, kind, payload, created_at)
      tasks(id, title, body, assignee, status, ...)
    """
    placeholders = ",".join("?" for _ in ESCALATION_KINDS)
    sql = f"""
        SELECT te.id, te.task_id, te.kind, te.payload, te.created_at,
               t.title, t.body, t.assignee
        FROM task_events te
        JOIN tasks t ON te.task_id = t.id
        WHERE te.id > ?
          AND te.kind IN ({placeholders})
          AND (
            t.assignee = 'user'
            OR EXISTS (
                SELECT 1 FROM task_comments tc
                WHERE tc.task_id = t.id
                  AND tc.body LIKE '%[USER-ESCALATION]%'
            )
          )
        ORDER BY te.id ASC
        LIMIT 50
    """
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = conn.execute(sql, (since_event_id, *ESCALATION_KINDS)).fetchall()
        conn.close()
        return rows
    except sqlite3.Error as e:
        log.warning("DB error for %s: %s", db_path, e)
        return []


def format_message(board: str, task_id: str, title: str, body: str,
                   kind: str, payload: dict) -> str:
    reason = "-"
    if isinstance(payload, dict):
        reason = payload.get("reason", "-")
    body_excerpt = (body or "").strip()[:300]
    if body and len(body) > 300:
        body_excerpt += "..."
    return (
        f"\U0001f514 [Aion Lumen Watchdog]\n\n"
        f"Event: {kind}\n"
        f"Board: {board}\n"
        f"Task: {task_id}\n\n"
        f"Title: {title}\n\n"
        f"Body:\n{body_excerpt}\n\n"
        f"Reason: {reason}\n\n"
        f"Antwort via:\n"
        f"hermes kanban comment {task_id} \"deine Antwort\"\n"
        f"hermes kanban unblock {task_id}"
    )


def send_telegram(text: str) -> bool:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text,
                  "disable_web_page_preview": True},
            timeout=10,
        )
        if r.ok:
            return True
        log.warning("Telegram send failed: HTTP %d %s",
                    r.status_code, r.text[:200])
        return False
    except requests.RequestException as e:
        log.warning("Telegram request error: %s", e)
        return False


def process_board(state: dict, board: str, db: Path) -> None:
    bs = state.setdefault(board, {"last_event_id": 0, "notified_tasks": []})
    notified = set(bs.get("notified_tasks", []))
    last_seen = bs.get("last_event_id", 0)
    events = fetch_new_events(db, last_seen)
    if not events:
        return
    for evt_id, task_id, kind, payload_str, _, title, body, _ in events:
        # Dedup: only first escalation event per task is notified.
        # Re-eskalation requires manual reset (clear notified_tasks for the board).
        if task_id in notified:
            log.debug("dedup: skip %s/%s (%s) - already notified",
                      board, task_id, kind)
            bs["last_event_id"] = evt_id
            continue
        try:
            payload = json.loads(payload_str) if payload_str else {}
        except json.JSONDecodeError:
            payload = {}
        text = format_message(board, task_id, title or "", body or "", kind, payload)
        if send_telegram(text):
            log.info("Notification sent: %s/%s (%s)", board, task_id, kind)
            notified.add(task_id)
            bs["notified_tasks"] = sorted(notified)
            bs["last_event_id"] = evt_id
        else:
            log.warning("Notification failed: %s/%s - retry next poll",
                        board, task_id)
            break


def main_loop() -> None:
    log.info("Watchdog-Bridge started, poll_interval=%ds", POLL_INTERVAL_SECONDS)
    state = load_state()
    try:
        while True:
            for board, db in discover_kanban_dbs():
                process_board(state, board, db)
            save_state(state)
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        log.info("Stopped via SIGINT")
        save_state(state)


if __name__ == "__main__":
    main_loop()
