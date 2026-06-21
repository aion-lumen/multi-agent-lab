#!/usr/bin/env python3
"""
pilot_worker.py - Custom executor-loop fuer Phase-3-Pilot.

Hintergrund: Hermes-Dispatcher behandelt Custom-Profile aus
config.yaml als 'non-spawnable terminal lane'. Echte Hermes-Profile
muessten via `hermes profile create` einzeln angelegt werden -
fuer Pilot zu invasiv. Stattdessen: dieser Worker.

Workflow pro Task:
1. `hermes kanban list` -> Tasks mit assignee=executor, status=ready
2. `hermes kanban claim <id>` (atomar)
3. Body lesen, Mail-Daten extrahieren
4. LM Studio executor (gpt-oss-20b) klassifiziert -> JSON
5. JSON-Parse + Validierung (best-effort)
6. `hermes kanban comment <id> <json>` -> Worker-Output dokumentiert
7. Auf Basis von outcome:
   - completed -> `complete --result <value>`
   - needs_validation -> reassign validator (best effort, ggf. nur Comment)
   - escalate_to_user -> reassign user, block (Bridge greift)

Constraints:
- Sequentiell (ein Task nach dem anderen) - LM Studio Single-Model
- Read-only DB fuer Discovery, CLI fuer State-Aenderungen
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import requests

DB = Path.home() / ".hermes" / "kanban" / "boards" / "life-mail-pilot" / "kanban.db"
LOG = (Path.home() / "Projects" / "aion-lumen" / "multi-agent"
       / "state" / "pilot-worker.log")

LMSTUDIO_URL = "http://127.0.0.1:1234/v1/chat/completions"
EXECUTOR_MODEL = "gpt-oss-20b"
LLM_TIMEOUT = 90

CATEGORIES = ("werbung", "newsletter_business", "geschaeftspost",
              "privat", "spam", "unklar")

SYSTEM_PROMPT = """Du bist Executor in einem Multi-Agent-Stack. Du klassifizierst eine Email in EINE der Kategorien:
werbung, newsletter_business, geschaeftspost, privat, spam, unklar

Du antwortest AUSSCHLIESSLICH mit gueltigem JSON nach diesem Schema:

{
  "task_id": "<wird unten gegeben>",
  "profile": "executor",
  "outcome": "completed" | "needs_validation" | "escalate_to_user",
  "result": {
    "type": "classification",
    "value": "<eine der Kategorien>",
    "confidence": <float 0.0-1.0>,
    "reasoning_summary": "<max 200 Zeichen>"
  },
  "evidence": [
    {"type": "text_snippet", "content": "<Zitat>", "source": "email_body|email_header", "weight": <0.0-1.0>}
  ],
  "tool_trace": [],
  "next_action_suggestion": null | "<text>"
}

Regeln:
- confidence < 0.7 -> outcome=needs_validation
- bei klarer Mehrdeutigkeit (>=2 plausible Kategorien) -> outcome=escalate_to_user, next_action_suggestion mit den Kandidaten
- ansonsten outcome=completed
- evidence MUSS mindestens 1 Eintrag haben mit konkretem Zitat aus der Email
- Reine JSON-Antwort ohne Markdown-Fences, ohne Erklaerung davor/danach"""

USER_PROMPT_TEMPLATE = """Task-ID: {task_id}

EMAIL:
{body}

Antworte als JSON nach Schema."""


LOG.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("pilot-worker")


def fetch_ready_tasks() -> list[tuple[str, str]]:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = conn.execute("""
        SELECT id, body FROM tasks
        WHERE status='ready' AND assignee='executor'
        ORDER BY created_at ASC
    """).fetchall()
    conn.close()
    return rows


def claim_task(task_id: str) -> bool:
    r = subprocess.run(
        ["hermes", "kanban", "claim", task_id],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        log.warning("claim %s failed: %s", task_id, r.stderr.strip()[:200])
        return False
    return True


def call_executor(task_id: str, body: str) -> str:
    payload = {
        "model": EXECUTOR_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",
             "content": USER_PROMPT_TEMPLATE.format(task_id=task_id, body=body[:4000])},
        ],
        "temperature": 0.2,
        "max_tokens": 1500,
        "reasoning_effort": "low",
    }
    r = requests.post(LMSTUDIO_URL, json=payload, timeout=LLM_TIMEOUT)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def parse_json_loose(text: str) -> dict | None:
    text = text.strip()
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
    # Direct
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Search for first {...} block
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def post_comment(task_id: str, text: str) -> None:
    subprocess.run(
        ["hermes", "kanban", "comment", task_id, text, "--author", "executor"],
        capture_output=True, text=True, timeout=15,
    )


def complete_task(task_id: str, value: str, summary: str) -> None:
    subprocess.run(
        ["hermes", "kanban", "complete", task_id,
         "--result", value, "--summary", summary[:300]],
        capture_output=True, text=True, timeout=15,
    )


def escalate_to_user(task_id: str, reason: str) -> None:
    # 'user' is not an on-disk Hermes profile, reassign would fail.
    # We just block the task with a clear reason; manual triage post-run.
    subprocess.run(
        ["hermes", "kanban", "block", task_id, f"USER-ESCALATION: {reason}"],
        capture_output=True, text=True, timeout=15,
    )


def reassign_validator(task_id: str, reason: str) -> None:
    # 'validator' likewise not an on-disk profile. Mark via comment + block
    # so the task stays inspectable in the dashboard.
    subprocess.run(
        ["hermes", "kanban", "block", task_id,
         f"NEEDS-VALIDATOR (executor confidence<0.7): {reason}"],
        capture_output=True, text=True, timeout=15,
    )


def process_one(task_id: str, body: str) -> str:
    """Process a single task. Return outcome label for logging."""
    if not claim_task(task_id):
        return "claim_failed"
    try:
        raw = call_executor(task_id, body)
    except Exception as e:
        log.warning("LLM call failed for %s: %s", task_id, e)
        post_comment(task_id, f"Executor LLM-Fehler: {e}")
        escalate_to_user(task_id, "LLM-Fehler im Executor")
        return "llm_error"

    parsed = parse_json_loose(raw)
    if not parsed:
        log.warning("JSON parse failed for %s: %r", task_id, raw[:200])
        post_comment(task_id, f"Executor-Output (kein parsebares JSON):\n{raw[:1000]}")
        escalate_to_user(task_id, "JSON-Parse-Fehler im Executor")
        return "json_error"

    # Validate result.value
    result = parsed.get("result") or {}
    value = result.get("value", "")
    if value not in CATEGORIES:
        post_comment(task_id, f"Executor-Output (ungueltige Kategorie={value!r}):\n{raw[:800]}")
        escalate_to_user(task_id, f"Ungueltige Kategorie: {value}")
        return "bad_category"

    confidence = float(result.get("confidence", 0.0))
    summary = result.get("reasoning_summary", "")
    outcome = parsed.get("outcome") or "completed"

    # Always post the JSON as comment
    post_comment(task_id, "```json\n" + json.dumps(parsed, indent=2, ensure_ascii=False) + "\n```")

    if outcome == "escalate_to_user" or confidence < 0.5:
        escalate_to_user(task_id, f"Executor escalated: value={value} conf={confidence:.2f}")
        return f"escalated_user(conf={confidence:.2f})"
    if outcome == "needs_validation" or confidence < 0.7:
        reassign_validator(task_id, f"value={value} conf={confidence:.2f}")
        return f"to_validator(conf={confidence:.2f})"
    complete_task(task_id, value, summary or value)
    return f"completed:{value}(conf={confidence:.2f})"


def main() -> None:
    tasks = fetch_ready_tasks()
    log.info("Found %d ready/executor tasks", len(tasks))
    if not tasks:
        log.info("Nothing to do")
        return

    counts: dict[str, int] = {}
    t0 = time.time()
    for i, (tid, body) in enumerate(tasks, 1):
        outcome = process_one(tid, body or "")
        key = outcome.split("(")[0].split(":")[0]
        counts[key] = counts.get(key, 0) + 1
        log.info("[%d/%d] %s -> %s", i, len(tasks), tid, outcome)

    elapsed = time.time() - t0
    log.info("Done in %.1fs (%d tasks). Outcome counts: %s",
             elapsed, len(tasks), counts)


if __name__ == "__main__":
    main()
