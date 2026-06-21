#!/usr/bin/env python3
"""
sender_learner.py - Learn new sender-heuristics from Ground-Truth mismatches.

Input: CSV with columns (subset used):
  sender, ground_truth, heuristic_value, final_value, uid, task_id

Source can be:
  - Phase-3.5b diagnose CSV (state/diagnose-mismatches-2026-05-10.csv)
  - Future production-rerun-vN export

Learn-Rules (Schema v2.1):
  A) GT=privat   AND heuristic_value=geschaeftspost AND natural-person-pattern
       -> private_senders (kind=learned-private)
  B) GT=werbung  AND heuristic_value=geschaeftspost
       -> ambiguous_senders (kind=learned-ambiguous-marketing,
                             category_hint=marketing-leaning)
  C) GT=spam     AND heuristic_value=geschaeftspost
       -> ambiguous_senders (kind=learned-ambiguous-spam,
                             category_hint=spam-prone)

Idempotent: same (sender, target-list) pair is added only once.
Backup: state/sender-heuristics.json.backup-<TS> before any write.
Dry-run via --dry-run.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import shutil
import sys
import time
from pathlib import Path

STATE_DIR = (Path.home() / "Projects" / "aion-lumen"
             / "multi-agent" / "state")
HEUR_FILE = STATE_DIR / "sender-heuristics.json"
LOG_FILE = STATE_DIR / "sender-learner.log"

NEGATIVE_LOCALPARTS = {
    "noreply", "no-reply", "info", "service", "system", "admin",
    "support", "hello", "notifications", "news", "marketing",
    "deals", "promo", "promotions", "contact", "mail", "postmaster",
    "team", "office", "kontakt", "newsletter", "donotreply",
    "do-not-reply", "auto", "automated", "bounce", "abuse",
    "sales", "billing", "accounts", "alerts",
}

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("sender_learner")


def extract_email(sender_field: str) -> str:
    if not sender_field:
        return ""
    m = re.search(r"<([^>]+)>", sender_field)
    if m:
        return m.group(1).strip().lower()
    return sender_field.strip().lower()


def local_part(email: str) -> str:
    return email.split("@", 1)[0] if "@" in email else email


def is_natural_person_localpart(email: str) -> bool:
    """
    True iff local-part looks like a personal name (firstname / firstname.lastname).
    Rules:
      - not in NEGATIVE_LOCALPARTS
      - must start with a letter
      - allowed chars: a-z, dot, dash; NO digits at end of name component
      - <=2 dot-separated tokens, each alphabetic, length 2..20
    """
    lp = local_part(email)
    if not lp:
        return False
    if lp in NEGATIVE_LOCALPARTS:
        return False
    if not re.match(r"^[a-z]", lp):
        return False
    parts = lp.split(".")
    if len(parts) > 2:
        return False
    for p in parts:
        if not (2 <= len(p) <= 20):
            return False
        if not re.match(r"^[a-z][a-z\-]*$", p):
            return False
    return True


def load_heuristics() -> dict:
    if not HEUR_FILE.exists():
        return {"private_senders": [], "service_senders": [],
                "marketing_senders": [], "ambiguous_senders": []}
    data = json.loads(HEUR_FILE.read_text())
    data.setdefault("private_senders", [])
    data.setdefault("service_senders", [])
    data.setdefault("marketing_senders", [])
    data.setdefault("ambiguous_senders", [])
    return data


def sender_already_in(data: dict, list_key: str, email: str) -> bool:
    for entry in data.get(list_key, []):
        if extract_email(entry.get("sender", "")) == email:
            return True
    return False


def backup_file() -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = HEUR_FILE.with_suffix(f".json.backup-{ts}")
    shutil.copy2(HEUR_FILE, dst)
    return dst


def decide_rule(gt: str, heur: str, sender: str) -> tuple[str, str, str] | None:
    """Return (target_list, kind, category_hint) or None."""
    gt = (gt or "").strip().lower()
    heur = (heur or "").strip().lower()
    email = extract_email(sender)
    if not email or "@" not in email:
        return None

    if gt == "privat" and heur == "geschaeftspost":
        if is_natural_person_localpart(email):
            return ("private_senders", "learned-private", "")
        return None
    if gt == "werbung" and heur == "geschaeftspost":
        return ("ambiguous_senders", "learned-ambiguous-marketing",
                "marketing-leaning")
    if gt == "spam" and heur == "geschaeftspost":
        return ("ambiguous_senders", "learned-ambiguous-spam",
                "spam-prone")
    return None


def process_rows(rows: list[dict], data: dict,
                 source_label: str) -> list[dict]:
    """Mutate data, return list of applied changes."""
    changes = []
    for row in rows:
        sender = row.get("sender", "") or ""
        gt = row.get("ground_truth", "")
        heur = row.get("heuristic_value", "")
        decision = decide_rule(gt, heur, sender)
        if not decision:
            continue
        target, kind, hint = decision
        email = extract_email(sender)
        if sender_already_in(data, target, email):
            log.info("skip (already in %s): %s", target, email)
            continue
        entry = {
            "sender": sender,
            "count": 1,
            "kind": kind,
            "learned_from": source_label,
        }
        if hint:
            entry["category_hint"] = hint
        data[target].append(entry)
        changes.append({
            "uid": row.get("uid", ""),
            "task_id": row.get("task_id", ""),
            "sender": sender,
            "target": target,
            "kind": kind,
            "category_hint": hint,
            "rule": f"GT={gt} heur={heur}",
        })
        log.info("LEARN %s -> %s (%s%s)", email, target, kind,
                 f"/{hint}" if hint else "")
    return changes


def read_csv_source(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


def read_kanban_board_source(board: str) -> list[dict]:
    """Phase J / Variante A: read task_runs.metadata from a done kanban board.

    Returns rows compatible with the CSV-source format used by process_rows().
    Each record contributes one row with sender/subject extracted from task body
    and ground-truth/heuristic-value pulled from the worker's metadata JSON.
    """
    import subprocess
    listing = subprocess.run(
        ["hermes", "kanban", "--board", board, "list",
         "--status", "done", "--json"],
        capture_output=True, text=True, timeout=30,
    )
    if listing.returncode != 0:
        log.warning("kanban list --board %s failed: %s",
                    board, listing.stderr[:200])
        return []
    try:
        tasks = json.loads(listing.stdout)
    except json.JSONDecodeError:
        log.warning("kanban list returned non-JSON")
        return []

    rows: list[dict] = []
    for t in (tasks if isinstance(tasks, list) else tasks.get("tasks", [])):
        task_id = t.get("id")
        if not task_id:
            continue
        show = subprocess.run(
            ["hermes", "kanban", "--board", board, "show", task_id, "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if show.returncode != 0:
            continue
        try:
            full = json.loads(show.stdout)
        except json.JSONDecodeError:
            continue
        runs = full.get("runs") or []
        if not runs:
            continue
        meta_raw = runs[-1].get("metadata")
        if not meta_raw:
            continue
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
        except json.JSONDecodeError:
            continue

        body_text = (full.get("task") or {}).get("body") or ""
        sender = ""
        subject = ""
        # Heuristic body-parse: look for "Sender:" / "Subject:" lines and JSON blocks
        for line in body_text.splitlines():
            sl = line.strip()
            if sl.lower().startswith("sender:") and not sender:
                sender = sl.split(":", 1)[1].strip()
            elif sl.lower().startswith("subject:") and not subject:
                subject = sl.split(":", 1)[1].strip()
        # Fallback: parse JSON block
        if not sender:
            m = re.search(r'"sender"\s*:\s*"([^"]+)"', body_text)
            if m:
                sender = m.group(1)
        if not subject:
            m = re.search(r'"subject"\s*:\s*"([^"]+)"', body_text)
            if m:
                subject = m.group(1)

        rows.append({
            "uid": meta.get("uid", ""),
            "task_id": task_id,
            "sender": sender,
            "subject": subject,
            "ground_truth": (meta.get("user_final")
                             or meta.get("value")
                             or meta.get("executor_value", "")),
            "heuristic_value": meta.get("heuristic_value", ""),
            "executor_value": meta.get("executor_value", ""),
            "validator_value": meta.get("validator_value", ""),
            "final_value": meta.get("value", ""),
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="diagnose-csv",
                    choices=["diagnose-csv", "pilot-rerun-csv", "kanban-board"],
                    help="Source format (default: diagnose-csv)")
    ap.add_argument("--csv", type=Path,
                    help="CSV path (required for diagnose-csv / pilot-rerun-csv)")
    ap.add_argument("--source-board", default=None,
                    help="Kanban board slug — reads task_runs.metadata. "
                         "Mutually exclusive with --csv (or additive: both → union).")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--source-label", default=None,
                    help="Label used in 'learned_from' field. "
                         "Default: csv filename or board slug")
    args = ap.parse_args()

    if not args.csv and not args.source_board:
        sys.exit("ERROR: provide --csv OR --source-board (or both)")
    if args.csv and not args.csv.exists():
        sys.exit(f"CSV not found: {args.csv}")

    label_parts = []
    if args.csv:
        label_parts.append(args.csv.name)
    if args.source_board:
        label_parts.append(f"board:{args.source_board}")
    source_label = args.source_label or " + ".join(label_parts)

    log.info("=" * 60)
    log.info("sender_learner source=%s csv=%s board=%s dry_run=%s",
             args.source, args.csv, args.source_board, args.dry_run)

    rows: list[dict] = []
    if args.csv:
        rows.extend(read_csv_source(args.csv))
        log.info("loaded %d rows from CSV", len(rows))
    if args.source_board:
        kanban_rows = read_kanban_board_source(args.source_board)
        log.info("loaded %d rows from kanban board %s",
                 len(kanban_rows), args.source_board)
        rows.extend(kanban_rows)
    log.info("total rows: %d", len(rows))

    data = load_heuristics()
    before_counts = {k: len(data.get(k, [])) for k in
                     ("private_senders", "service_senders",
                      "marketing_senders", "ambiguous_senders")}

    changes = process_rows(rows, data, source_label)

    after_counts = {k: len(data.get(k, [])) for k in
                    ("private_senders", "service_senders",
                     "marketing_senders", "ambiguous_senders")}

    log.info("counts before: %s", before_counts)
    log.info("counts after:  %s", after_counts)
    log.info("changes applied: %d", len(changes))
    for ch in changes:
        log.info("  %s", ch)

    if not changes:
        log.info("no changes - nothing to write")
        return

    if args.dry_run:
        log.info("DRY-RUN - sender-heuristics.json NOT modified")
        return

    backup = backup_file()
    log.info("backup written: %s", backup)
    HEUR_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    log.info("HEUR_FILE updated: %s", HEUR_FILE)


if __name__ == "__main__":
    main()
