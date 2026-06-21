#!/usr/bin/env python3
"""
pilot_task_loader.py - Load 100 selected sample mails as Hermes-Kanban tasks.

Reads pilot-sample-final.csv (output of bucket selection) and creates one
Hermes-Kanban task per row, assigned to executor profile.

Body references multi-agent/docs/worker-output-schema.md and contains:
- Mail metadata (sender, subject, date)
- Body excerpt (max 1000 chars)
- Classification instructions
- ground_truth (if set, for later evaluation)
- mail_uid (for traceability)

Constraints:
- Pilot board must be active before running
- Idempotency-key per mail UID -> safe to re-run
"""
from __future__ import annotations

import csv
import json
import logging
import subprocess
import sys
from pathlib import Path

CSV_PATH = (Path.home() / "Projects" / "aion-lumen" / "multi-agent"
            / "state" / "pilot-sample-final.csv")

TASK_BODY_TEMPLATE = """## Aufgabe

Klassifiziere diese Email in genau EINE Kategorie:
- `werbung` — Marketing, Sales, Promotionen
- `newsletter_business` — informative Geschaefts-Newsletter
- `geschaeftspost` — direkte Geschaeftskommunikation, Bestaetigungen
- `privat` — persoenliche Korrespondenz
- `spam` — offensichtlich unerwuenscht / Phishing
- `unklar` — Mehrdeutigkeit, Eskalation noetig

## Email-Daten

- **Mail-UID:** {uid}
- **Sender:** {sender}
- **Subject:** {subject}
- **Datum:** {date}
- **Sprache (geschaetzt):** {language}
- **Laenge:** {length_chars} Zeichen
- **Vorklassifikation (life-agent):** {suggested_category}

### Body (erste 1000 Zeichen)

```
{body_excerpt}
```

## Output-Format

Antworte als JSON nach Schema in `~/Projects/aion-lumen/multi-agent/docs/worker-output-schema.md`.

Pflicht-Felder:
- `task_id`: (wird automatisch eingesetzt)
- `profile`: "executor"
- `outcome`: completed | needs_validation | escalate_to_user
- `result.value`: eine der Kategorien
- `result.confidence`: 0.0-1.0
- `result.reasoning_summary`: max. 200 Zeichen
- `evidence`: konkrete Text-Snippets oder Sender-Indikatoren mit weight
- `tool_trace`: leere Liste (keine Tool-Calls bei dieser Aufgabe)

## Eskalations-Regeln

- `confidence < 0.7` -> `outcome=needs_validation`
- klare Mehrdeutigkeit (>=2 plausible Kategorien) -> `outcome=escalate_to_user` mit `next_action_suggestion` als Liste der Kandidaten
- ansonsten `outcome=completed`

## Tags (im Body, da `--tag` nicht unterstuetzt)

`domain:life-mail` `phase:pilot` `mail_uid:{uid}`{ground_truth_tag}
"""

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("loader")


def make_title(subject: str) -> str:
    s = (subject or "(no subject)").strip().replace("\n", " ")
    if len(s) > 50:
        s = s[:47] + "..."
    return f"Klassifiziere: {s}"


def make_body(row: dict) -> str:
    gt = row.get("ground_truth", "").strip()
    gt_tag = f" `ground_truth:{gt}`" if gt else ""
    return TASK_BODY_TEMPLATE.format(
        uid=row["uid"],
        sender=row["sender"],
        subject=row["subject"],
        date=row.get("date", ""),
        language=row.get("language", "unknown"),
        length_chars=row.get("length_chars", "?"),
        suggested_category=row.get("suggested_category", "?"),
        body_excerpt=(row.get("body_excerpt", "") or "")[:1000],
        ground_truth_tag=gt_tag,
    )


def create_task(row: dict) -> tuple[bool, str]:
    title = make_title(row["subject"])
    body = make_body(row)
    idempotency_key = f"pilot-life-mail-uid-{row['uid']}"
    cmd = [
        "hermes", "kanban", "create", title,
        "--body", body,
        "--assignee", "executor",
        "--idempotency-key", idempotency_key,
        "--json",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    if r.returncode != 0:
        return False, r.stderr.strip()[:200]
    try:
        d = json.loads(r.stdout)
        return True, d.get("id", "?")
    except json.JSONDecodeError:
        return True, r.stdout.strip()[:60]


def main() -> None:
    if not CSV_PATH.exists():
        log.error("CSV not found: %s", CSV_PATH)
        sys.exit(1)

    selected = []
    with open(CSV_PATH) as f:
        for row in csv.DictReader(f):
            # Pilot CSV is already filtered to 100 rows; treat all as selected.
            # But honour 'selected=true' if present.
            if "selected" in row and row.get("selected", "").strip().lower() not in ("", "true", "1", "yes"):
                continue
            selected.append(row)
    log.info("Selected %d rows from %s", len(selected), CSV_PATH)

    successes = 0
    failures = 0
    for i, row in enumerate(selected, 1):
        ok, info = create_task(row)
        if ok:
            successes += 1
            if i % 10 == 0 or i <= 3:
                log.info("[%d/%d] created %s", i, len(selected), info)
        else:
            failures += 1
            log.warning("[%d/%d] FAIL uid=%s: %s",
                        i, len(selected), row.get("uid"), info)

    log.info("Done: %d created, %d failed", successes, failures)


if __name__ == "__main__":
    main()
