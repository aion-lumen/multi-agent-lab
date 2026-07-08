"""Tests for the lead-adapter: recipient-alias classification, field extraction,
folio-import emission, and 14-day cross-portal dedup."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
CONFIG = REPO_ROOT / "config"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "imap" / "job_leads_smoke.json"

from lead_heuristic import extract_lead  # noqa: E402
from lead_emitter import emit_lead  # noqa: E402


def _read_frontmatter(md: str) -> dict:
    """Tiny parser for our hand-built frontmatter (key: value lines)."""
    lines = md.splitlines()
    assert lines[0] == "---"
    fm = {}
    for line in lines[1:]:
        if line == "---":
            break
        k, _, v = line.partition(":")
        fm[k.strip()] = v.strip().strip('"')
    return fm


# --- Classification (recipient-alias override) ---

def test_recipient_alias_forces_job_lead(monkeypatch):
    import categories_loader as cl
    from domain_actionability import classify_domain_actionability

    monkeypatch.setenv("CATEGORIES_YAML", str(CONFIG / "categories.yaml"))
    cl._CACHE = {}
    r = classify_domain_actionability(
        "Unknown <hello@unknown-portal.xyz>",
        "irgendein betreff ohne keywords",
        "2026-07-08T08:00:00+00:00",
        to_addr="Afschin <freelance@mirhamed.ch>",
    )
    assert r.domain == "job-lead"
    assert r.actionability == "actionable"
    assert any("recipient_alias" in m for m in r.matched_markers)
    cl._CACHE = {}


def test_non_alias_recipient_unchanged(monkeypatch):
    import categories_loader as cl
    from domain_actionability import classify_domain_actionability

    monkeypatch.setenv("CATEGORIES_YAML", str(CONFIG / "categories.yaml"))
    cl._CACHE = {}
    r = classify_domain_actionability(
        "Newsletter <news@substack.com>",
        "wochenrückblick",
        "2026-07-08T08:00:00+00:00",
        to_addr="afschin@mirhamed.ch",
    )
    assert r.domain != "job-lead"
    cl._CACHE = {}


# --- Extraction ---

def test_extract_lead_fields():
    body = (
        "Projekt: Senior Python Developer\nStandort: Zuerich\n"
        "Tagessatz: 800 EUR/Tag\nBewerbungsfrist 15.08.2026\n"
        "Details: https://www.freelancermap.de/projekt/12345\n"
    )
    lead = extract_lead("FreelancerMap <no-reply@freelancermap.de>",
                        "Neues Projekt: Senior Python Developer", body)
    assert lead.rolle == "Senior Python Developer"
    assert lead.quelle == "freelancermap"
    assert lead.deadline == "2026-08-15"
    assert "800" in lead.satz and "EUR" in lead.satz
    assert lead.ort == "Zuerich"
    assert lead.link.startswith("https://www.freelancermap.de/")
    assert len(lead.dedup_key) == 16


# --- Emission (a) alias mail → lead .md with fields ---

def test_emit_writes_lead_md(tmp_path):
    lead = extract_lead(
        "FreelancerMap <no-reply@freelancermap.de>",
        "Neues Projekt: Senior Python Developer",
        "Projekt: Senior Python Developer\nStandort: Zuerich\nTagessatz: 800 EUR/Tag\n"
        "Bewerbungsfrist 15.08.2026\nhttps://www.freelancermap.de/projekt/12345\n",
    )
    p = emit_lead(lead, 90001, inbox_path=tmp_path, ledger_path=tmp_path / "ledger.json",
                  now=datetime(2026, 7, 8, tzinfo=timezone.utc))
    fm = _read_frontmatter(p.read_text())
    assert fm["type"] == "lead"
    assert fm["source"] == "mail-pipeline"
    assert fm["derived_from_external"] == "true"
    assert fm["target"] == "current"
    assert fm["rolle"] == "Senior Python Developer"
    assert fm["deadline"] == "2026-08-15"
    assert fm["dedup_key"] == lead.dedup_key
    assert "duplicate_of" not in fm


# --- Emission (b) cross-portal duplicate → same dedup_key, second tagged ---

def test_cross_portal_dedup(tmp_path):
    ledger = tmp_path / "ledger.json"
    envs = json.loads(FIXTURE.read_text())
    a, b = envs[0], envs[1]  # freelancermap + freelance.de, same role/ort/satz

    lead_a = extract_lead(a["from_addr"], a["subject"], a["body_text"])
    lead_b = extract_lead(b["from_addr"], b["subject"], b["body_text"])
    assert lead_a.dedup_key == lead_b.dedup_key  # cross-portal identity

    pa = emit_lead(lead_a, a["uid"], inbox_path=tmp_path, ledger_path=ledger,
                   now=datetime(2026, 7, 8, tzinfo=timezone.utc))
    pb = emit_lead(lead_b, b["uid"], inbox_path=tmp_path, ledger_path=ledger,
                   now=datetime(2026, 7, 8, 12, tzinfo=timezone.utc))

    assert "duplicate_of" not in _read_frontmatter(pa.read_text())
    fm_b = _read_frontmatter(pb.read_text())
    assert fm_b["duplicate_of"] == pa.stem  # points at the first lead's id
    # both files exist (grouped, not discarded)
    assert pa.exists() and pb.exists() and pa != pb


# --- Emission (c) lead without deadline → still emitted ---

def test_lead_without_deadline(tmp_path):
    envs = json.loads(FIXTURE.read_text())
    c = envs[2]  # GULP DevOps, no deadline
    lead = extract_lead(c["from_addr"], c["subject"], c["body_text"])
    assert lead.deadline is None
    p = emit_lead(lead, c["uid"], inbox_path=tmp_path, ledger_path=tmp_path / "ledger.json",
                  now=datetime(2026, 7, 9, tzinfo=timezone.utc))
    md = p.read_text()
    assert p.exists()
    assert "deadline:" not in _read_frontmatter(md)


# --- Dedup window expiry: >14 days apart → not a duplicate ---

def test_dedup_window_expires(tmp_path):
    ledger = tmp_path / "ledger.json"
    lead = extract_lead("FreelancerMap <x@freelancermap.de>", "Projekt: Data Engineer",
                        "Projekt: Data Engineer\nOrt: Berlin\nTagessatz: 700 EUR/Tag\n")
    emit_lead(lead, 1, inbox_path=tmp_path, ledger_path=ledger,
              now=datetime(2026, 7, 1, tzinfo=timezone.utc))
    p2 = emit_lead(lead, 2, inbox_path=tmp_path, ledger_path=ledger,
                   now=datetime(2026, 7, 20, tzinfo=timezone.utc))  # 19 days later
    assert "duplicate_of" not in _read_frontmatter(p2.read_text())
