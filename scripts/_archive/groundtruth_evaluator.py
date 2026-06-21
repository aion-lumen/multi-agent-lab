#!/usr/bin/env python3
"""
groundtruth_evaluator.py - Match user ground-truth labels against worker outputs.

Phase J / Variante A: reads task_runs.metadata via `hermes kanban show --json`
(metadata-first), falls back to fenced-JSON in task_comments [WORKER-OUTPUT]
(backward-compat with Phase 3.5c data).

CLI:
  --board <slug>     active kanban board (e.g. production-rerun-v3, variante-a-test-…)
  --csv <path>       ground-truth CSV (default: state/pilot-sample-groundtruth-labeled.csv)

Legacy positional usage (no flags) reads latest pilot-snapshot-post-*.db
for backward-compat with Phase 3 pre-board workflows.

Output:
- state/groundtruth-evaluation-<board>-<date>.json
- console table
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

STATE = Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "state"
DEFAULT_CSV = STATE / "pilot-sample-groundtruth-labeled.csv"


def find_latest_snapshot() -> Path:
    snapshots = sorted(STATE.glob("pilot-snapshot-post-*.db"))
    if not snapshots:
        sys.exit("FEHLER: kein post-Snapshot in {}".format(STATE))
    return snapshots[-1]


def find_board_db(board: str) -> Path:
    p = Path.home() / ".hermes" / "kanban" / "boards" / board / "kanban.db"
    if not p.exists():
        sys.exit(f"DB not found for board {board}: {p}")
    return p


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_worker_json_from_comment(body: str) -> dict | None:
    """Extract JSON from last comment that contains 'outcome'."""
    if not body:
        return None
    candidates = _FENCE_RE.findall(body)
    if not candidates:
        i = body.find("{")
        j = body.rfind("}")
        if i >= 0 and j > i:
            candidates = [body[i:j + 1]]
    for cand in candidates:
        try:
            d = json.loads(cand)
            if isinstance(d, dict) and "outcome" in d:
                return d
        except json.JSONDecodeError:
            continue
    return None


def extract_result_from_metadata(meta_raw) -> dict | None:
    """Parse task_runs.metadata into a worker-output-like dict."""
    if not meta_raw:
        return None
    try:
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
    except json.JSONDecodeError:
        return None
    if not isinstance(meta, dict):
        return None
    value = (meta.get("value") or meta.get("executor_value") or
             (meta.get("result") or {}).get("value"))
    if not value:
        return None
    return {
        "outcome": meta.get("outcome", "unknown"),
        "profile": meta.get("profile", "unknown"),
        "result": {
            "value": str(value).lower(),
            "confidence": (meta.get("confidence")
                           or (meta.get("result") or {}).get("confidence")
                           or 1.0),
        },
        "schema_version": meta.get("schema_version", "?"),
        "_source": "metadata",
    }


def get_worker_output_for_uid(conn: sqlite3.Connection, uid: str,
                              board: str | None = None
                              ) -> tuple[str | None, dict | None]:
    """Return (task_id, worker_json_or_metadata) for given mail uid.

    Forward-compat: if board is provided, fetch `hermes kanban show --json`
    and extract task_runs[-1].metadata first.
    Backward-compat: fall back to fenced JSON in task_comments.
    """
    task = conn.execute(
        "SELECT id FROM tasks WHERE body LIKE ?",
        (f"%mail_uid:{uid}%",),
    ).fetchone()
    if not task:
        return None, None
    task_id = task[0]

    if board:
        show = subprocess.run(
            ["hermes", "kanban", "--board", board, "show", task_id, "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if show.returncode == 0:
            try:
                full = json.loads(show.stdout)
                runs = full.get("runs") or []
                if runs:
                    meta_result = extract_result_from_metadata(
                        runs[-1].get("metadata"))
                    if meta_result:
                        return task_id, meta_result
            except json.JSONDecodeError:
                pass

    # Fallback: fenced JSON in comments
    rows = conn.execute(
        "SELECT body FROM task_comments WHERE task_id = ? ORDER BY created_at DESC",
        (task_id,),
    ).fetchall()
    for (body,) in rows:
        d = parse_worker_json_from_comment(body or "")
        if d:
            d["_source"] = "fenced_comment"
            return task_id, d
    return task_id, None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", default=None,
                    help="Kanban board slug. If omitted: legacy latest "
                         "pilot-snapshot-*.db is used.")
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV,
                    help="Ground-truth CSV path "
                         f"(default: {DEFAULT_CSV.name})")
    args = ap.parse_args()

    csv_path = args.csv
    if not csv_path.exists():
        sys.exit(f"CSV nicht gefunden: {csv_path}")

    if args.board:
        db = find_board_db(args.board)
        out_name = f"groundtruth-evaluation-{args.board}-{datetime.now():%Y-%m-%d}.json"
    else:
        db = find_latest_snapshot()
        out_name = "groundtruth-evaluation-2026-05-08.json"
    out_json = STATE / out_name
    print(f"Snapshot: {db}")
    print(f"Output:   {out_json}")

    labeled: list[dict] = []
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            gt = (row.get("ground_truth") or "").strip().lower()
            if gt:
                labeled.append(row)
    if not labeled:
        sys.exit("Keine ground_truth-Labels in CSV.")
    print(f"Gelabelt: {len(labeled)}")

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    results: list[dict] = []
    by_gt: dict[str, dict] = defaultdict(lambda: {"total": 0, "correct": 0, "mismatches": []})
    by_target: dict[str, dict] = defaultdict(lambda: {"total": 0, "correct": 0})
    confusion: dict[tuple[str, str], int] = defaultdict(int)
    sources: dict[str, int] = defaultdict(int)

    for row in labeled:
        uid = (row.get("uid") or "").strip()
        gt = (row.get("ground_truth") or "").strip().lower()
        target = (row.get("target_category") or "").strip().lower()
        notes = (row.get("gt_notes") or "").strip()
        subject = (row.get("subject") or "")[:60]

        task_id, worker = get_worker_output_for_uid(conn, uid, board=args.board)
        if worker:
            sources[worker.get("_source", "?")] += 1
        worker_value: str | None = None
        confidence: float | None = None
        if worker:
            r = worker.get("result") or {}
            worker_value = (r.get("value") or "").strip().lower() or None
            c = r.get("confidence")
            if isinstance(c, (int, float)):
                confidence = float(c)

        match = (worker_value == gt) if worker_value else False
        results.append({
            "uid": uid,
            "task_id": task_id,
            "subject": subject,
            "target_bucket": target,
            "ground_truth": gt,
            "worker_value": worker_value,
            "worker_confidence": confidence,
            "match": match,
            "gt_notes": notes,
        })
        by_gt[gt]["total"] += 1
        by_target[target]["total"] += 1
        if match:
            by_gt[gt]["correct"] += 1
            by_target[target]["correct"] += 1
        else:
            by_gt[gt]["mismatches"].append({
                "uid": uid, "worker": worker_value or "<none>",
                "subject": subject, "confidence": confidence,
            })
        confusion[(gt, worker_value or "<none>")] += 1

    total_evaluated = sum(1 for r in results if r["worker_value"] is not None)
    total_correct = sum(1 for r in results if r["match"])

    accuracy_by_gt = {}
    for cat, d in by_gt.items():
        accuracy_by_gt[cat] = {
            "total": d["total"],
            "correct": d["correct"],
            "accuracy": d["correct"] / d["total"] if d["total"] else None,
            "mismatches": d["mismatches"],
        }

    accuracy_by_target = {}
    for cat, d in by_target.items():
        accuracy_by_target[cat] = {
            "total": d["total"],
            "correct": d["correct"],
            "accuracy": d["correct"] / d["total"] if d["total"] else None,
        }

    confusion_pairs = [
        {"gt": gt, "worker": w, "count": n}
        for (gt, w), n in sorted(confusion.items(), key=lambda kv: -kv[1])
    ]

    summary = {
        "snapshot": str(db),
        "board": args.board,
        "total_labeled": len(labeled),
        "total_evaluated": total_evaluated,
        "total_correct": total_correct,
        "overall_accuracy": (total_correct / total_evaluated) if total_evaluated else None,
        "result_source_distribution": dict(sources),
        "accuracy_by_ground_truth": accuracy_by_gt,
        "accuracy_by_target_bucket": accuracy_by_target,
        "confusion_pairs": confusion_pairs,
        "results_per_mail": results,
    }
    out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    # Console output
    print()
    print("=" * 70)
    overall = summary["overall_accuracy"]
    overall_str = f"{overall:.1%}" if overall is not None else "N/A"
    print(f"OVERALL: {total_correct}/{total_evaluated} = {overall_str}")
    print()
    print("Pro Ground-Truth-Bucket (User-Label):")
    for cat in sorted(accuracy_by_gt.keys()):
        d = accuracy_by_gt[cat]
        acc = d["accuracy"]
        acc_str = f"{acc:.1%}" if acc is not None else "N/A"
        print(f"  {cat:<22} {d['correct']:>2}/{d['total']:<2} = {acc_str}")
        for mm in d["mismatches"]:
            cnf = f"{mm['confidence']:.2f}" if mm["confidence"] is not None else "?"
            print(f"      Worker: {mm['worker']:<22} (conf={cnf}) subj: {mm['subject']}")

    print()
    print("Pro Target-Bucket (Pilot-Vorgabe):")
    for cat in sorted(accuracy_by_target.keys()):
        d = accuracy_by_target[cat]
        acc = d["accuracy"]
        acc_str = f"{acc:.1%}" if acc is not None else "N/A"
        print(f"  {cat:<22} {d['correct']:>2}/{d['total']:<2} = {acc_str}")

    print()
    print("Confusion-Pairs (gt → worker, count):")
    for p in confusion_pairs:
        print(f"  {p['gt']:<22} -> {p['worker']:<22} {p['count']}")

    print()
    print(f"Detail-JSON: {out_json}")


if __name__ == "__main__":
    main()
