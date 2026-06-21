#!/usr/bin/env python3
"""
production_telemetry.py - Aggregate production-rerun telemetry.

Reads board kanban.db. Forward-compat (Phase J / Variante A): pulls
classification result from task_runs.metadata via `hermes kanban show
--json` if available. Backward-compat (Phase 3.5c): falls back to
fenced-JSON in task_comments with [WORKER-OUTPUT] marker.

Emits state/production-telemetry-<board>-<date>.json.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

STATE = Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "state"


def find_db(board_slug: str) -> Path:
    p = Path.home() / ".hermes" / "kanban" / "boards" / board_slug / "kanban.db"
    if not p.exists():
        sys.exit(f"DB not found: {p}")
    return p


def median_or_none(xs):
    return statistics.median(xs) if xs else None


# === Result extraction with metadata-first + fenced-comment-fallback ===

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def get_classification_result(task_full_json: dict) -> dict | None:
    """Extract classification dict from a `hermes kanban show --json` payload.

    Priority order:
      1. task_runs[-1].metadata (Phase J / v0.13 native)
      2. task_comments[*] with [WORKER-OUTPUT] fenced JSON (Phase 3.5c legacy)

    Returns dict with keys: value, confidence, outcome, source. None if not parseable.
    """
    runs = task_full_json.get("runs") or []
    if runs and runs[-1].get("metadata"):
        meta_raw = runs[-1]["metadata"]
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
            if isinstance(meta, dict):
                value = (meta.get("value") or meta.get("executor_value") or
                         (meta.get("result") or {}).get("value"))
                if value:
                    return {
                        "value": str(value).lower(),
                        "confidence": (meta.get("confidence")
                                       or (meta.get("result") or {}).get("confidence")
                                       or 1.0),
                        "outcome": meta.get("outcome", "unknown"),
                        "profile": meta.get("profile", "unknown"),
                        "llm_calls": (meta.get("stats") or {}).get("llm_calls", 0),
                        "schema_version": meta.get("schema_version", "?"),
                        "source": "metadata",
                    }
        except (json.JSONDecodeError, AttributeError):
            pass

    # Fallback: fenced JSON in comments
    for comment in (task_full_json.get("comments") or []):
        body = comment.get("body", "")
        if "[WORKER-OUTPUT]" not in body and "```" not in body:
            continue
        for cand in _FENCE_RE.findall(body):
            try:
                d = json.loads(cand)
                if not isinstance(d, dict) or "outcome" not in d:
                    continue
                r = d.get("result") or {}
                value = (r.get("value") or d.get("value") or "").lower()
                if not value:
                    continue
                return {
                    "value": value,
                    "confidence": r.get("confidence", 1.0),
                    "outcome": d.get("outcome", "unknown"),
                    "profile": d.get("profile", "unknown"),
                    "llm_calls": (d.get("stats") or {}).get("llm_calls", 0),
                    "schema_version": d.get("schema_version", "?"),
                    "source": "fenced_comment",
                }
            except json.JSONDecodeError:
                continue
    return None


def fetch_task_full(board: str, task_id: str) -> dict | None:
    """Run `hermes kanban show <id> --json` and return parsed payload."""
    r = subprocess.run(
        ["hermes", "kanban", "--board", board, "show", task_id, "--json"],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", required=True, help="Board slug")
    # Positional fallback (backward-compat with old usage: `production_telemetry.py <board>`)
    args, extra = ap.parse_known_args()
    if not args.board and extra:
        args.board = extra[0]
    board = args.board
    db = find_db(board)
    out = STATE / f"production-telemetry-{board}-{datetime.now():%Y-%m-%d}.json"
    print(f"DB: {db}")
    print(f"Output: {out}")

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

    # Status distribution
    status_dist = dict(conn.execute(
        "SELECT status, COUNT(*) FROM tasks GROUP BY status"
    ).fetchall())
    total = sum(status_dist.values())

    # Latency
    latencies = []
    for created, completed in conn.execute(
        "SELECT created_at, completed_at FROM tasks WHERE completed_at IS NOT NULL"
    ).fetchall():
        if created and completed:
            latencies.append(completed - created)

    # Get all task IDs
    task_ids = [row[0] for row in conn.execute(
        "SELECT id FROM tasks ORDER BY created_at ASC"
    ).fetchall()]

    # Count user-escalations + schema-failures (via comments grep, fast)
    n_user_escalations = conn.execute(
        "SELECT COUNT(*) FROM task_comments WHERE body LIKE '%[USER-ESCALATION]%'"
    ).fetchone()[0]
    n_schema_failures = conn.execute(
        "SELECT COUNT(*) FROM task_comments WHERE body LIKE '%[SCHEMA-FAILURE%'"
    ).fetchone()[0]
    conn.close()

    # Per-task: fetch full show --json + extract classification via metadata-first
    outcomes: Counter = Counter()
    profiles: Counter = Counter()
    values: Counter = Counter()
    sources: Counter = Counter()
    schema_versions: Counter = Counter()
    total_llm_calls = 0
    n_with_result = 0
    confidences: list[float] = []

    for tid in task_ids:
        full = fetch_task_full(board, tid)
        if not full:
            continue
        result = get_classification_result(full)
        if not result:
            continue
        n_with_result += 1
        outcomes[result["outcome"]] += 1
        profiles[result["profile"]] += 1
        values[result["value"]] += 1
        sources[result["source"]] += 1
        schema_versions[str(result.get("schema_version", "?"))] += 1
        c = result.get("confidence")
        if isinstance(c, (int, float)):
            confidences.append(float(c))
        if isinstance(result.get("llm_calls"), int):
            total_llm_calls += result["llm_calls"]

    telemetry = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "board": board,
        "db": str(db),
        "total_tasks": total,
        "status_distribution": status_dist,
        "median_create_to_done_seconds": median_or_none(latencies),
        "n_with_result": n_with_result,
        "outcome_distribution": dict(outcomes),
        "profile_distribution": dict(profiles),
        "value_distribution": dict(values),
        "result_source_distribution": dict(sources),
        "schema_version_distribution": dict(schema_versions),
        "median_confidence": median_or_none(confidences),
        "total_llm_calls": total_llm_calls,
        "user_escalation_comments": n_user_escalations,
        "schema_failure_comments": n_schema_failures,
        "heuristic_hit_rate": (
            outcomes.get("heuristic_complete", 0)
            + outcomes.get("heuristic", 0)
        ) / total if total else None,
        "disagreement_rate": (
            outcomes.get("user_override", 0)
            + outcomes.get("user_override_after_schema_fail", 0)
        ) / total if total else None,
    }

    out.write_text(json.dumps(telemetry, indent=2))
    print(json.dumps(telemetry, indent=2))
    print(f"\nWritten: {out}")


if __name__ == "__main__":
    main()
