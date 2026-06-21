#!/usr/bin/env python3
"""
copy_pilot_subset.py - Copy N tasks from pilot snapshot DB to a target board.

Usage:
  python3 copy_pilot_subset.py <count> <target_board> [--notify-telegram] [--offset N]
  python3 copy_pilot_subset.py 5 production-smoke
  python3 copy_pilot_subset.py 20 migration-integration --offset 20 --notify-telegram

With --notify-telegram and TELEGRAM_HOME_CHANNEL in env: subscribes each
created task to the Hermes-Gateway Telegram notifier (v0.13 native).
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

STATE = Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "state"


def _arg_after(flag: str, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main() -> None:
    # Positional args (count, target). Skip --flags during positional walk.
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    n = int(positional[0]) if len(positional) > 0 else 5
    target = positional[1] if len(positional) > 1 else "production-smoke"

    # Added 2026-05-14 (Phase H): notify-subscribe integration for Hermes v0.13
    # native Telegram push. Reusable for Variante A and Phase 4 tranche setup.
    notify_telegram = "--notify-telegram" in sys.argv
    offset = int(_arg_after("--offset", 0) or 0)
    # Phase J (Variante A): override default assignee + force-load skill per task
    assignee = _arg_after("--assignee", "executor") or "executor"
    # --skill can appear multiple times — collect all occurrences
    skills: list[str] = []
    for i, a in enumerate(sys.argv):
        if a == "--skill" and i + 1 < len(sys.argv):
            skills.append(sys.argv[i + 1])

    chat_id = os.environ.get("TELEGRAM_HOME_CHANNEL", "").strip()
    if notify_telegram and not chat_id:
        print("WARN: --notify-telegram set but TELEGRAM_HOME_CHANNEL empty — skipping subscribes",
              file=sys.stderr)

    snapshots = sorted(STATE.glob("pilot-snapshot-post-*.db"))
    if not snapshots:
        sys.exit("No pilot-snapshot-post-*.db found")
    db = snapshots[-1]
    print(f"Source: {db}")
    print(f"Target board: {target}")
    if offset:
        print(f"Offset: {offset}")
    if notify_telegram:
        print(f"notify-telegram: {'on' if chat_id else 'requested but no chat_id'}")

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT title, body FROM tasks ORDER BY created_at ASC LIMIT ? OFFSET ?",
        (n, offset),
    ).fetchall()
    conn.close()
    print(f"Copying {len(rows)} tasks...")

    success = 0
    subscribed = 0
    for i, (title, body) in enumerate(rows, 1):
        idem = f"copy-{target}-{offset + i}-{hash((title or '')[:50]) & 0xffff}"
        cmd = ["hermes", "kanban", "--board", target, "create",
               title or f"task {offset + i}",
               "--body", body or "", "--assignee", assignee,
               "--idempotency-key", idem, "--json"]
        for sk in skills:
            cmd.extend(["--skill", sk])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print(f"[{i}/{n}] FAIL: {r.stderr[:200]}")
            continue
        try:
            d = json.loads(r.stdout)
            task_id = d.get("id")
            if i % 20 == 0 or i <= 3:
                print(f"[{i}/{n}] created {task_id}")
            success += 1
            # v0.13: optional notify-subscribe to Telegram
            if notify_telegram and chat_id and task_id:
                sub = subprocess.run(
                    ["hermes", "kanban", "--board", target, "notify-subscribe",
                     task_id, "--platform", "telegram", "--chat-id", chat_id],
                    capture_output=True, text=True, timeout=10,
                )
                if sub.returncode == 0:
                    subscribed += 1
        except json.JSONDecodeError:
            print(f"[{i}/{n}] non-json: {r.stdout[:80]}")

    print(f"\nDone: {success}/{len(rows)} created on board '{target}'")
    if notify_telegram:
        print(f"Telegram subscribed: {subscribed}/{success}")


if __name__ == "__main__":
    main()
