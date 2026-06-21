#!/usr/bin/env python3
"""
marketing_learner.py - Build state/marketing-patterns.json from defaults
+ optional learning from CSV (rows where ground_truth=werbung AND
final_value != werbung -> Subject tokens are extracted as candidate
marketing patterns).

The output file is consumed by sender_heuristic.subject_marketing_score()
and ultimately by heuristic_classify_v2().

Idempotent: re-running is safe (set semantics for both pattern lists).
Backup: state/marketing-patterns.json.backup-<TS> before any overwrite.
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
OUT_FILE = STATE_DIR / "marketing-patterns.json"
LOG_FILE = STATE_DIR / "marketing-learner.log"

DEFAULT_SUBJECT_PATTERNS: list[str] = [
    "smart money",
    "% rabatt",
    "% off",
    "click here",
    "jetzt kaufen",
    "nur diese woche",
    "letzte chance",
    "sale",
    "deal",
    "deals",
    "rabatt",
    "aktion",
    "newsletter",
    "angebot",
    "% sparen",
]

DEFAULT_EMOJIS: list[str] = [
    "🐳", "💰", "🚀", "🎉", "🛍️", "🔥", "💸", "🎁",
]

# Stop-words excluded from learned subject tokens
STOP_WORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "at",
    "die", "der", "das", "den", "dem", "des", "ein", "eine", "einen",
    "ist", "wir", "sie", "ihr", "fur", "und", "oder", "mit",
    "zu", "auf", "von", "im", "am", "be", "is", "are", "was", "you",
    "your", "our", "us", "this", "that", "for", "with", "from",
    "re", "fwd", "afschin", "mirhamed",
}

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("marketing_learner")

EMOJI_RE = re.compile(
    "["                          # one big char-class
    "\U0001F300-\U0001F9FF"     # symbols & pictographs / supplemental
    "\U0001FA00-\U0001FAFF"     # symbols extended-A/B
    "\U00002600-\U000027BF"     # misc symbols + dingbats
    "]"
)


def normalize(s: str) -> str:
    return s.lower().strip()


def load_existing() -> dict:
    if not OUT_FILE.exists():
        return {
            "subject_patterns": [],
            "emoji_patterns": [],
            "metadata": {
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "sources": [],
            },
        }
    return json.loads(OUT_FILE.read_text())


def merge_patterns(existing: list[str], add: list[str]) -> list[str]:
    seen = {normalize(p) for p in existing}
    out = list(existing)
    for p in add:
        n = normalize(p)
        if n and n not in seen:
            out.append(n)
            seen.add(n)
    return out


def merge_emojis(existing: list[str], add: list[str]) -> list[str]:
    seen = set(existing)
    out = list(existing)
    for e in add:
        if e and e not in seen:
            out.append(e)
            seen.add(e)
    return out


def extract_subject_candidates(subject: str) -> list[str]:
    """
    From a subject 'Here's how you can copy smart money...' extract candidate
    multi-word marketing tokens. We do a light, lossy extraction:
      - lower-case bigrams of length >=4 chars per token, non-stopword
      - lower-case content emojis
    """
    if not subject:
        return []
    text = subject.lower()
    # Split on non-letter so % stays attached for "% off" style? -> let
    # explicit defaults cover those. Here we focus on bigrams.
    tokens = re.findall(r"[a-z]{4,}", text)
    tokens = [t for t in tokens if t not in STOP_WORDS]
    bigrams: list[str] = []
    for i in range(len(tokens) - 1):
        bg = f"{tokens[i]} {tokens[i + 1]}"
        bigrams.append(bg)
    return bigrams


def extract_emojis(text: str) -> list[str]:
    return EMOJI_RE.findall(text or "")


def process_csv(rows: list[dict]) -> tuple[list[str], list[str]]:
    new_patterns: list[str] = []
    new_emojis: list[str] = []
    for row in rows:
        gt = (row.get("ground_truth") or "").strip().lower()
        final = (row.get("final_value") or "").strip().lower()
        if gt != "werbung":
            continue
        if final == "werbung":
            continue  # already classified correctly
        subject = row.get("subject", "")
        cands = extract_subject_candidates(subject)
        # be conservative: limit candidates per row to 3
        for c in cands[:3]:
            new_patterns.append(c)
        for e in extract_emojis(subject):
            new_emojis.append(e)
    return new_patterns, new_emojis


def read_kanban_board_rows(board: str) -> list[dict]:
    """Phase J / Variante A: read task_runs.metadata from a done kanban board.

    Returns CSV-compatible rows (subject, ground_truth, final_value).
    A row contributes a learning candidate when the worker flagged the email
    as werbung via user_override but heuristic/executor missed it.
    """
    import subprocess
    listing = subprocess.run(
        ["hermes", "kanban", "--board", board, "list",
         "--status", "done", "--json"],
        capture_output=True, text=True, timeout=30,
    )
    if listing.returncode != 0:
        return []
    try:
        tasks = json.loads(listing.stdout)
    except json.JSONDecodeError:
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
        subject = ""
        for line in body_text.splitlines():
            sl = line.strip()
            if sl.lower().startswith("subject:"):
                subject = sl.split(":", 1)[1].strip()
                break
        if not subject:
            m = re.search(r'"subject"\s*:\s*"([^"]+)"', body_text)
            if m:
                subject = m.group(1)

        rows.append({
            "subject": subject,
            "ground_truth": (meta.get("user_final") or meta.get("value") or "").lower(),
            "final_value": (meta.get("value") or meta.get("executor_value") or "").lower(),
        })
    return rows


def backup(dst: Path) -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    b = dst.with_suffix(f".json.backup-{ts}")
    shutil.copy2(dst, b)
    return b


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, default=None,
                    help="CSV with subject/ground_truth/final_value. Optional.")
    ap.add_argument("--source-board", default=None,
                    help="Kanban board slug to read task_runs.metadata from.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--source-label", default=None)
    args = ap.parse_args()

    log.info("=" * 60)
    log.info("marketing_learner csv=%s board=%s dry_run=%s",
             args.csv, args.source_board, args.dry_run)

    existing = load_existing()
    before_sp = len(existing.get("subject_patterns", []))
    before_ep = len(existing.get("emoji_patterns", []))

    # Always seed defaults
    existing["subject_patterns"] = merge_patterns(
        existing.get("subject_patterns", []), DEFAULT_SUBJECT_PATTERNS)
    existing["emoji_patterns"] = merge_emojis(
        existing.get("emoji_patterns", []), DEFAULT_EMOJIS)

    learned: dict = {"subject_patterns": [], "emoji_patterns": []}

    all_rows: list[dict] = []
    if args.csv:
        if not args.csv.exists():
            sys.exit(f"CSV not found: {args.csv}")
        with args.csv.open(newline="", encoding="utf-8") as f:
            csv_rows = list(csv.DictReader(f))
        log.info("loaded %d rows from CSV", len(csv_rows))
        all_rows.extend(csv_rows)
    if args.source_board:
        kanban_rows = read_kanban_board_rows(args.source_board)
        log.info("loaded %d rows from kanban board %s",
                 len(kanban_rows), args.source_board)
        all_rows.extend(kanban_rows)

    if all_rows:
        sp, ep = process_csv(all_rows)
        learned["subject_patterns"] = sp
        learned["emoji_patterns"] = ep
        existing["subject_patterns"] = merge_patterns(
            existing["subject_patterns"], sp)
        existing["emoji_patterns"] = merge_emojis(
            existing["emoji_patterns"], ep)

    after_sp = len(existing["subject_patterns"])
    after_ep = len(existing["emoji_patterns"])

    log.info("subject_patterns: %d -> %d (added %d)",
             before_sp, after_sp, after_sp - before_sp)
    log.info("emoji_patterns:   %d -> %d (added %d)",
             before_ep, after_ep, after_ep - before_ep)
    if learned["subject_patterns"]:
        log.info("learned subject_patterns: %s",
                 learned["subject_patterns"])
    if learned["emoji_patterns"]:
        log.info("learned emoji_patterns: %s", learned["emoji_patterns"])

    label_parts: list[str] = []
    if args.csv:
        label_parts.append(args.csv.name)
    if args.source_board:
        label_parts.append(f"board:{args.source_board}")
    source_label = args.source_label or (" + ".join(label_parts)
                                         if label_parts else "defaults-only")
    meta = existing.setdefault("metadata", {})
    meta.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    meta["last_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    sources = meta.setdefault("sources", [])
    if source_label not in sources:
        sources.append(source_label)

    if args.dry_run:
        log.info("DRY-RUN - marketing-patterns.json NOT written")
        return

    if OUT_FILE.exists():
        b = backup(OUT_FILE)
        log.info("backup: %s", b)
    OUT_FILE.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n")
    log.info("written: %s", OUT_FILE)


if __name__ == "__main__":
    main()
