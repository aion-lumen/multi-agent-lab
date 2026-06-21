#!/usr/bin/env python3
"""
diagnose_mismatches.py - Generates a diagnostic CSV per ground-truth mail.

Reads:
  - state/pilot-sample-groundtruth-labeled.csv (15 user-labeled mails)
  - state/production-rerun-post-*.db (latest re-run snapshot)

For each labeled mail:
  - Find Hermes task via 'mail_uid:<uid>' substring in task.body
  - Parse worker JSON outputs from comments
  - Determine path: heuristic | multi_agent | user_override | executor_only
  - Extract executor/validator values + confidences when applicable
  - Compare worker final value vs ground truth

Output:
  state/diagnose-mismatches-2026-05-10.csv with empty diagnose_kategorie /
  diagnose_notiz columns for user to fill in Numbers.
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
from pathlib import Path

PROJECT = Path.home() / "Projects" / "aion-lumen" / "multi-agent"
GT_CSV = PROJECT / "state" / "pilot-sample-groundtruth-labeled.csv"
STATE_DIR = PROJECT / "state"
OUT_CSV = STATE_DIR / "diagnose-mismatches-2026-05-10.csv"


def find_snapshot() -> Path:
    snaps = sorted(STATE_DIR.glob("production-rerun-post-*.db"))
    if not snaps:
        sys.exit("No production-rerun-post-*.db snapshot found")
    return snaps[-1]


_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_worker_json(comment_body: str) -> dict | None:
    if not comment_body:
        return None
    for cand in _FENCE.findall(comment_body):
        try:
            d = json.loads(cand)
            if isinstance(d, dict) and "outcome" in d:
                return d
        except json.JSONDecodeError:
            continue
    return None


def extract_task_meta(body: str) -> dict:
    if not body:
        return {"sender": "", "subject": "", "body_excerpt": ""}
    m_sender = re.search(r"\*\*Sender:\*\*\s*(.+)", body)
    m_subject = re.search(r"\*\*Subject:\*\*\s*(.+)", body)
    m_body = re.search(r"### Body[^\n]*\n+```\n(.*?)\n```", body, re.DOTALL)
    return {
        "sender": (m_sender.group(1).strip() if m_sender else "")[:200],
        "subject": (m_subject.group(1).strip() if m_subject else "")[:80],
        "body_excerpt": (m_body.group(1).strip() if m_body else "")[:300],
    }


def parse_escalation_comment(text: str) -> dict:
    """Extract executor/validator values+confidences from [USER-ESCALATION] body."""
    out = {"executor_value": "", "executor_confidence": "",
           "validator_value": "", "validator_confidence": ""}
    if "[USER-ESCALATION]" not in text:
        return out
    m_exec = re.search(r"Executor:\s*\*\*([a-z_]+)\*\*\s*\(conf=([\d.]+)\)",
                       text, re.IGNORECASE)
    if m_exec:
        out["executor_value"] = m_exec.group(1).lower()
        out["executor_confidence"] = m_exec.group(2)
    m_val = re.search(r"Validator:\s*\*\*([a-z_]+)\*\*\s*\(conf=([\d.]+)\)",
                      text, re.IGNORECASE)
    if m_val:
        out["validator_value"] = m_val.group(1).lower()
        out["validator_confidence"] = m_val.group(2)
    return out


def analyse_comments(comments: list[tuple[str, str]]) -> dict:
    """comments: list of (body, author) ordered ASC by created_at."""
    out = {
        "pfad": "unknown",
        "heuristic_value": "",
        "heuristic_reason": "",
        "executor_value": "",
        "executor_confidence": "",
        "validator_value": "",
        "validator_confidence": "",
        "final_value": "",
    }
    for body, _author in comments:
        # 1. USER-ESCALATION-Marker -> executor/validator-Werte
        if "[USER-ESCALATION]" in body:
            esc = parse_escalation_comment(body)
            for k, v in esc.items():
                if v and not out[k]:
                    out[k] = v
            continue
        # 2. Worker-JSON
        d = parse_worker_json(body)
        if not d:
            continue
        outcome = d.get("outcome", "")
        profile = d.get("profile", "")
        result = d.get("result") or {}
        value = (result.get("value") or "").lower()

        if outcome == "heuristic_complete" or profile == "librarian":
            out["pfad"] = "heuristic"
            out["heuristic_value"] = value
            out["final_value"] = value
            for ev in d.get("evidence") or []:
                if ev.get("type") == "rule_match":
                    out["heuristic_reason"] = ev.get("reason") or ev.get("rule", "")
                    break

        elif outcome == "agreed" or profile == "multi_agent":
            out["pfad"] = "multi_agent"
            out["final_value"] = value
            for ev in d.get("evidence") or []:
                etype = ev.get("type")
                if etype == "executor_output":
                    out["executor_value"] = (ev.get("value") or "").lower()
                    if ev.get("confidence") is not None:
                        out["executor_confidence"] = str(ev["confidence"])
                elif etype == "validator_output":
                    out["validator_value"] = (ev.get("value") or "").lower()
                    if ev.get("confidence") is not None:
                        out["validator_confidence"] = str(ev["confidence"])

        elif outcome in ("completed_via_user_override",):
            out["pfad"] = "user_override"
            out["final_value"] = value
            # exec/val values may already be set from [USER-ESCALATION]; also try evidence
            for ev in d.get("evidence") or []:
                etype = ev.get("type")
                if etype == "executor_output" and not out["executor_value"]:
                    out["executor_value"] = (ev.get("value") or "").lower()
                elif etype == "validator_output" and not out["validator_value"]:
                    out["validator_value"] = (ev.get("value") or "").lower()

        elif outcome == "completed_without_validation":
            out["pfad"] = "executor_only"
            out["executor_value"] = value
            out["final_value"] = value

    return out


def main() -> None:
    snap = find_snapshot()
    print(f"Snapshot: {snap.name}")
    conn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)

    if not GT_CSV.exists():
        sys.exit(f"GT CSV not found: {GT_CSV}")

    rows_out = []
    with open(GT_CSV) as f:
        for row in csv.DictReader(f):
            uid = (row.get("uid") or "").strip()
            gt = (row.get("ground_truth") or "").strip().lower()
            if not uid or not gt:
                continue
            task = conn.execute(
                "SELECT id, body FROM tasks WHERE body LIKE ?",
                (f"%mail_uid:{uid}%",),
            ).fetchone()
            if not task:
                print(f"  uid={uid}: no task found in re-run snapshot")
                continue
            tid, tbody = task
            meta = extract_task_meta(tbody)
            comments = conn.execute(
                "SELECT body, author FROM task_comments WHERE task_id=? ORDER BY created_at ASC",
                (tid,),
            ).fetchall()
            an = analyse_comments(comments)
            match = "true" if an["final_value"] == gt else "false"
            rows_out.append({
                "uid": uid,
                "task_id": tid,
                "subject": meta["subject"],
                "sender": meta["sender"],
                "body_excerpt": meta["body_excerpt"],
                "ground_truth": gt,
                "pfad": an["pfad"],
                "heuristic_value": an["heuristic_value"],
                "heuristic_reason": an["heuristic_reason"],
                "executor_value": an["executor_value"],
                "executor_confidence": an["executor_confidence"],
                "validator_value": an["validator_value"],
                "validator_confidence": an["validator_confidence"],
                "final_value": an["final_value"],
                "match": match,
                "diagnose_kategorie": "",
                "diagnose_notiz": "",
            })

    fieldnames = list(rows_out[0].keys()) if rows_out else [
        "uid", "task_id", "subject", "sender", "body_excerpt", "ground_truth",
        "pfad", "heuristic_value", "heuristic_reason",
        "executor_value", "executor_confidence",
        "validator_value", "validator_confidence",
        "final_value", "match", "diagnose_kategorie", "diagnose_notiz",
    ]
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows_out)

    print(f"\nWrote: {OUT_CSV}")
    print(f"Total rows: {len(rows_out)}")
    matches = sum(1 for r in rows_out if r["match"] == "true")
    mismatches = sum(1 for r in rows_out if r["match"] == "false")
    print(f"Matches:    {matches}")
    print(f"Mismatches: {mismatches}\n")

    print("Mismatches:")
    for r in rows_out:
        if r["match"] != "false":
            continue
        ex = r["executor_value"] or "-"
        va = r["validator_value"] or "-"
        hv = r["heuristic_value"] or "-"
        print(f"  uid={r['uid']:<8} pfad={r['pfad']:<14} gt={r['ground_truth']:<16}"
              f" final={r['final_value']:<16} (heur={hv}, exec={ex}, val={va})  subj={r['subject'][:50]}")


if __name__ == "__main__":
    main()
