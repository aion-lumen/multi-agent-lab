#!/usr/bin/env python3
"""
probe_worker.py - Fallback custom worker for Phase 3.5a probes.

Reason: Hermes default kanban-worker skill spawns LLM calls but does not
reliably post JSON output as a comment (protocol_violation observed).
This worker reads each profile's config.yaml, claims its assigned task,
calls LM Studio directly with the profile's model, posts the JSON output
as a comment + completes the task.

Usage:
  python3 probe_worker.py <profile-name> <task-id>
  python3 probe_worker.py architect t_1f3d3144

For Phase 3.5a probes: one call per profile is enough.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

import requests
import yaml

PROFILES_ROOT = Path.home() / ".hermes" / "profiles"
LOG_FILE = (Path.home() / "Projects" / "aion-lumen" / "multi-agent"
            / "state" / "probe-worker.log")
LLM_TIMEOUT = 90

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("probe-worker")


def load_profile(name: str) -> dict:
    cfg_path = PROFILES_ROOT / name / "config.yaml"
    if not cfg_path.exists():
        sys.exit(f"Profile not found: {cfg_path}")
    return yaml.safe_load(cfg_path.read_text())


def call_llm(model: str, base_url: str, system_prompt: str, user_prompt: str,
             reasoning_effort: str = "low") -> tuple[str, float]:
    # gpt-oss eats tokens for chain-of-thought even with reasoning_effort=low;
    # 'minimal' is more reliable for short JSON answers.
    if "gpt-oss" in model.lower() and reasoning_effort == "low":
        reasoning_effort = "minimal"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 3000,
        "temperature": 0.2,
        "reasoning_effort": reasoning_effort,
    }
    t0 = time.time()
    r = requests.post(f"{base_url}/chat/completions", json=payload, timeout=LLM_TIMEOUT)
    r.raise_for_status()
    elapsed = time.time() - t0
    return r.json()["choices"][0]["message"]["content"], elapsed


def get_task_body(task_id: str) -> str:
    """Read body via hermes show (parsed) - assume task exists."""
    r = subprocess.run(["hermes", "kanban", "show", task_id],
                       capture_output=True, text=True, timeout=15)
    out = r.stdout
    # Body block starts after "Body:" until next blank-double-line
    m = re.search(r"\nBody:\n(.*?)\n\n(?:Latest summary:|Comments|Events|Runs|$)",
                  out, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Fallback: lines after "Body:"
    if "\nBody:\n" in out:
        return out.split("\nBody:\n", 1)[1][:4000]
    return ""


def parse_json_loose(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # Truncated JSON repair: balance braces / brackets
    i = text.find("{")
    if i < 0:
        return None
    payload = text[i:]
    # remove trailing partial values after last comma/colon
    payload = re.sub(r',\s*"[^"]*"\s*:\s*"[^"]*$', "", payload)
    payload = re.sub(r',\s*"[^"]*$', "", payload)
    payload = payload.rstrip(", \n\t")
    open_braces = payload.count("{") - payload.count("}")
    open_brackets = payload.count("[") - payload.count("]")
    payload = payload + ("]" * max(0, open_brackets)) + ("}" * max(0, open_braces))
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def post_comment(task_id: str, text: str, author: str) -> None:
    subprocess.run(
        ["hermes", "kanban", "comment", task_id, text, "--author", author],
        capture_output=True, text=True, timeout=15,
    )


def claim(task_id: str) -> bool:
    r = subprocess.run(["hermes", "kanban", "claim", task_id],
                       capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        log.warning("claim %s failed: %s", task_id, r.stderr.strip()[:200])
        return False
    return True


def complete_task(task_id: str, summary: str) -> None:
    subprocess.run(
        ["hermes", "kanban", "complete", task_id, "--summary", summary[:300]],
        capture_output=True, text=True, timeout=15,
    )


def unblock_if_needed(task_id: str) -> None:
    """If task is blocked from a prior attempt, unblock first."""
    r = subprocess.run(["hermes", "kanban", "show", task_id],
                       capture_output=True, text=True, timeout=15)
    if "status:    blocked" in r.stdout or "status:   blocked" in r.stdout:
        log.info("Task %s is blocked; unblocking first.", task_id)
        subprocess.run(["hermes", "kanban", "unblock", task_id],
                       capture_output=True, text=True, timeout=15)


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("Usage: probe_worker.py <profile> <task_id>")
    profile, task_id = sys.argv[1], sys.argv[2]
    log.info("Profile=%s, task=%s", profile, task_id)

    cfg = load_profile(profile)
    model_block = cfg["model"]
    model = model_block["default"]
    base_url = model_block["base_url"]
    reasoning = (cfg.get("agent") or {}).get("reasoning_effort", "low")
    log.info("Model=%s reasoning_effort=%s", model, reasoning)

    soul_path = PROFILES_ROOT / profile / "SOUL.md"
    soul = soul_path.read_text() if soul_path.exists() else ""

    body = get_task_body(task_id)
    if not body:
        sys.exit(f"Could not read task body for {task_id}")
    log.info("Body chars: %d", len(body))

    unblock_if_needed(task_id)
    if not claim(task_id):
        sys.exit(f"Cannot claim {task_id}")

    sys_prompt = (
        f"{soul}\n\n"
        "Antworte AUSSCHLIESSLICH mit gueltigem JSON, keine Markdown-Fences, "
        "keine Erklaerungen davor oder danach."
    )

    raw, elapsed = call_llm(model, base_url, sys_prompt, body, reasoning)
    log.info("LLM call took %.1fs, output chars=%d", elapsed, len(raw))

    parsed = parse_json_loose(raw)
    if not parsed:
        post_comment(task_id, f"probe-worker: kein parsebares JSON. Raw output:\n{raw[:1500]}", profile)
        log.warning("JSON parse failed - posted raw output as comment")
        return

    formatted = "```json\n" + json.dumps(parsed, indent=2, ensure_ascii=False) + "\n```"
    post_comment(task_id, formatted, profile)

    summary = (parsed.get("result") or {}).get("reasoning_summary", "") \
              or parsed.get("outcome", "completed")
    complete_task(task_id, summary)
    log.info("Task %s completed (%s)", task_id, summary[:80])


if __name__ == "__main__":
    main()
