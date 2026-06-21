#!/usr/bin/env python3
"""
pilot_telemetry.py - Aggregate pilot run telemetry.

Reads life-mail-pilot kanban DB and emits JSON with:
- status_distribution
- profile_switches per task (via task_runs)
- median_claim_to_done_seconds
- median_create_to_done_seconds
- user_escalations
- worker_outputs (best-effort parse from comments/result for confidence)
- ground_truth_accuracy (where ground_truth labels exist in body)

Output: state/pilot-telemetry-2026-05-08.json
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

DB = Path.home() / ".hermes" / "kanban" / "boards" / "life-mail-pilot" / "kanban.db"
OUT = (Path.home() / "Projects" / "aion-lumen" / "multi-agent"
       / "state" / "pilot-telemetry-2026-05-08.json")


def median_or_none(values):
    return statistics.median(values) if values else None


def main() -> None:
    if not DB.exists():
        print(f"DB not found: {DB}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    # 1. Status distribution
    status_dist = dict(conn.execute(
        "SELECT status, COUNT(*) FROM tasks GROUP BY status"
    ).fetchall())
    total = sum(status_dist.values())

    # 2. Profile switches per task
    runs_per_task = conn.execute("""
        SELECT task_id, COUNT(*) AS n_runs
        FROM task_runs
        GROUP BY task_id
    """).fetchall()
    runs_dist = Counter(n for _, n in runs_per_task)

    # 3. Latency: created_at -> completed_at, and started_at -> completed_at
    rows = conn.execute("""
        SELECT id, created_at, started_at, completed_at, status, assignee
        FROM tasks
    """).fetchall()
    create_to_done = []
    start_to_done = []
    for tid, created, started, completed, _, _ in rows:
        if completed and created:
            create_to_done.append(completed - created)
        if completed and started:
            start_to_done.append(completed - started)

    # 4. User escalations
    user_escalations = sum(1 for r in rows if r[5] == "user")

    # 5. Worker outputs - best effort: scan task_comments + tasks.result
    worker_jsons = []
    # tasks.result first
    for r in conn.execute("SELECT id, result FROM tasks WHERE result IS NOT NULL").fetchall():
        try:
            worker_jsons.append((r[0], "result_field", json.loads(r[1])))
        except (json.JSONDecodeError, TypeError):
            pass
    # task_comments may carry the JSON. Worker posts as ```json ... ``` fence
    # OR the result may live elsewhere; parse fenced blocks first, then any
    # raw {...} payload that contains "outcome".
    fence_re = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
    for tid, body in conn.execute("SELECT task_id, body FROM task_comments").fetchall():
        if not body:
            continue
        candidates = fence_re.findall(body)
        if not candidates:
            # Greedy fallback: first { to last }
            i = body.find("{")
            j = body.rfind("}")
            if i >= 0 and j > i:
                candidates = [body[i:j + 1]]
        for cand in candidates:
            try:
                d = json.loads(cand)
                if isinstance(d, dict) and "outcome" in d:
                    worker_jsons.append((tid, "comment", d))
            except json.JSONDecodeError:
                continue

    # Confidence distribution (per-task best score)
    confidences_by_task: dict[str, float] = {}
    outcomes_by_task: dict[str, str] = {}
    values_by_task: dict[str, str] = {}
    for tid, _src, j in worker_jsons:
        if not isinstance(j, dict):
            continue
        result = j.get("result")
        if isinstance(result, dict):
            c = result.get("confidence")
            if isinstance(c, (int, float)):
                confidences_by_task[tid] = max(confidences_by_task.get(tid, 0.0), float(c))
            v = result.get("value")
            if isinstance(v, str):
                values_by_task[tid] = v
        outc = j.get("outcome")
        if isinstance(outc, str):
            outcomes_by_task[tid] = outc

    confidences = list(confidences_by_task.values())
    outcome_dist = Counter(outcomes_by_task.values())

    # 6. Ground-truth accuracy (parse from body)
    gt_correct = 0
    gt_total = 0
    for tid, body in conn.execute("SELECT id, body FROM tasks WHERE body LIKE '%ground_truth:%'").fetchall():
        m = re.search(r"`ground_truth:([a-z_]+)`", body or "")
        if not m:
            continue
        gt = m.group(1)
        worker_value = values_by_task.get(tid)
        gt_total += 1
        if worker_value and worker_value == gt:
            gt_correct += 1

    telemetry = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db": str(DB),
        "total_tasks": total,
        "status_distribution": status_dist,
        "user_escalations": user_escalations,
        "user_escalation_rate": (user_escalations / total) if total else None,
        "profile_runs_distribution": dict(runs_dist),
        "median_create_to_done_seconds": median_or_none(create_to_done),
        "median_start_to_done_seconds": median_or_none(start_to_done),
        "n_with_worker_json": len(set(t for t, _, _ in worker_jsons)),
        "outcome_distribution": dict(outcome_dist),
        "median_confidence": median_or_none(confidences),
        "low_confidence_count": sum(1 for c in confidences if c < 0.7),
        "ground_truth_evaluation": {
            "labeled_tasks": gt_total,
            "correct": gt_correct,
            "accuracy": (gt_correct / gt_total) if gt_total else None,
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(telemetry, indent=2))
    print(json.dumps(telemetry, indent=2))
    print(f"\nTelemetry written to: {OUT}")


if __name__ == "__main__":
    main()
