#!/usr/bin/env python3
"""lead_emitter.py — build a folio interchange .md from an extracted lead and
drop it into the folio import-inbox (Spec-sanctioned; no cross-DB write).

Frontmatter is hand-assembled (string list + "\\n".join, mirroring job_digest.py)
— NO yaml.dump, NO LLM free text. Only extracted fields land in the document.

14-day semantic dedup: a versioned JSON ledger (state/lead-emit-ledger.json,
worker-owned) records emitted dedup_keys. A lead whose dedup_key was already
emitted within 14 days is still emitted (grouped, not discarded) but tagged
`duplicate_of: <first-id>` so folio can group it in review.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from paths import FOLIO_INBOX_PATH, STATE_DIR
from lead_heuristic import LeadExtract

LEDGER_PATH = STATE_DIR / "lead-emit-ledger.json"
DEDUP_WINDOW_DAYS = 14


# --- Ledger (versioned for future DB migration) ---

def _load_ledger(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version") == 1 and isinstance(data.get("entries"), list):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"version": 1, "entries": []}


def _save_ledger(path: Path, ledger: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _prior_duplicate_id(ledger: dict, dedup_key: str, now: datetime) -> Optional[str]:
    """Earliest ledger id with same dedup_key emitted within the dedup window."""
    cutoff = now - timedelta(days=DEDUP_WINDOW_DAYS)
    hits = []
    for e in ledger["entries"]:
        if e.get("dedup_key") != dedup_key:
            continue
        try:
            ts = datetime.fromisoformat(e["emitted_at"])
        except (KeyError, ValueError):
            continue
        if ts >= cutoff:
            hits.append((ts, e.get("id", "")))
    if not hits:
        return None
    hits.sort(key=lambda t: t[0])
    return hits[0][1]


# --- Frontmatter assembly (YAML-safe, hand-built) ---

def _yv(s: str) -> str:
    """Double-quoted YAML scalar with minimal escaping."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_document(lead: LeadExtract, lead_id: str, created_iso: str,
                   duplicate_of: Optional[str] = None) -> str:
    title = f"Lead: {lead.rolle or '(unbekannte Rolle)'} @ {lead.quelle or '(unbekannt)'}"
    lines = [
        "---",
        "folio_import: v1",
        "type: lead",
        "source: mail-pipeline",
        "derived_from_external: true",
        "target: current",  # sentinel — folio resolves to the current chapter
        f"id: {lead_id}",
        f"created: {created_iso}",
        f"title: {_yv(title)}",
        f"rolle: {_yv(lead.rolle)}",
        f"quelle: {_yv(lead.quelle)}",
    ]
    if lead.deadline:
        lines.append(f"deadline: {lead.deadline}")
    if lead.satz:
        lines.append(f"satz: {_yv(lead.satz)}")
    if lead.ort:
        lines.append(f"ort: {_yv(lead.ort)}")
    if lead.link:
        lines.append(f"link: {_yv(lead.link)}")
    lines.append(f"dedup_key: {lead.dedup_key}")
    if duplicate_of:
        lines.append(f"duplicate_of: {duplicate_of}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")
    # Deterministic field summary — no LLM free text.
    for label, val in (
        ("Rolle", lead.rolle), ("Quelle", lead.quelle), ("Deadline", lead.deadline or ""),
        ("Satz", lead.satz), ("Ort", lead.ort), ("Link", lead.link),
    ):
        if val:
            lines.append(f"- **{label}:** {val}")
    return "\n".join(lines) + "\n"


def emit_lead(
    lead: LeadExtract,
    mail_id,
    *,
    inbox_path: Optional[Path] = None,
    ledger_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Path:
    """Write the lead .md into the folio inbox; record + dedup via ledger.

    Returns the written file path. `now`/paths are injectable for tests.
    """
    now = now or datetime.now(timezone.utc)
    inbox = inbox_path or FOLIO_INBOX_PATH
    ledger_file = ledger_path or LEDGER_PATH

    lead_id = f"lead-{lead.dedup_key}-{mail_id}"
    created_iso = now.strftime("%Y-%m-%dT%H:%M:%S%z") or now.isoformat()

    ledger = _load_ledger(ledger_file)
    duplicate_of = _prior_duplicate_id(ledger, lead.dedup_key, now)

    doc = build_document(lead, lead_id, created_iso, duplicate_of)
    inbox.mkdir(parents=True, exist_ok=True)
    out_path = inbox / f"{lead_id}.md"
    out_path.write_text(doc, encoding="utf-8")

    ledger["entries"].append({
        "dedup_key": lead.dedup_key,
        "id": lead_id,
        "emitted_at": now.isoformat(),
        "duplicate_of": duplicate_of,
    })
    _save_ledger(ledger_file, ledger)

    return out_path
