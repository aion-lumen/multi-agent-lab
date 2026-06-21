---
name: email-classification
version: 2.1.0
description: |
  Deterministic email classification via a Python subprocess (B3-CLI-Pivot).
  The pipeline (heuristic, executor-LLM, optional validator-LLM cascade) runs
  inside a CLI entry-point at ~/.hermes/plugins/email-classification/cli.py.
  The worker does 3 steps: show, run CLI via terminal, complete.

provides_tools: []
---

# Email Classification Skill

## Purpose

Classify a single email task by running the deterministic classification CLI
as a subprocess. The full pipeline (heuristic → executor-LLM → optional
validator-LLM cascade) is internal to the CLI; you do not run any of it
yourself.

## Pipeline (3 steps, in order)

### Step 1 — Read the task

Call `kanban_show(task_id)`.

### Step 2 — Run the classification CLI

Use the `terminal` tool to run **exactly** this command (substitute `<task_id>`
with the real id from Step 1):

    /Users/afschinmirhamed/.hermes/hermes-agent/venv/bin/python /Users/afschinmirhamed/.hermes/plugins/email-classification/cli.py <task_id>

The command prints a single JSON object on stdout and exits 0 on success
or 1 on pipeline-error. The JSON object is canonical metadata; parse it
with `json.loads` (or your tool runtime's equivalent).

**Do NOT** call `classify_email_full` as a Hermes tool — that tool is not
routed into the worker tool surface in Hermes v0.13. The CLI subprocess is
the working path.

**Do NOT** invoke `python3` or `python3.12` (no pyyaml installed). Use the
full path to the hermes-agent venv-python shown above; it has all required
dependencies.

### Step 3 — Complete the task

Call `kanban_complete(task_id=<the task id>, metadata=<the parsed dict>)`.

Pass the dict from Step 2 unchanged.

## Metadata schema (v2.1)

The CLI's JSON output always has these keys:

- `outcome`: one of `heuristic | llm | llm-agreed | llm-disagreed | pipeline-error`
- `value`: one of `werbung, geschaeftspost, privat, spam, unklar`
- `confidence`: float `0.0–1.0`
- `evidence`: list of strings
- `schema_version`: `v2.1`

Optional: `heuristic_value`, `executor_value`, `validator_value`,
`requires_human_review`, `reasoning`.

## Rules

- Do **not** classify the email yourself; only the CLI decides.
- Do **not** modify the dict returned by the CLI.
- Do **not** call `kanban_create` — cascade is internal to the CLI.
- Do **not** block the task. If `outcome == "pipeline-error"`, still complete
  with the returned metadata; the `reasoning` field carries the error.
- Do **not** retry the CLI for the same task.
