#!/usr/bin/env python3
"""export_pre_reset_yahoo.py — Phase DB-Reset Step 2+3.

Exportiert yahoo-Rows aus feedback.db, angereichert mit den ECHTEN
User-Corrections aus ~/.folio/folio.db (folio-detail-panel-Source).
Output: JSON + CSV + Auswertung-md.

Read-only auf beiden DBs.

Run:
    .venv/bin/python3 scripts/export_pre_reset_yahoo.py
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

FEEDBACK_DB = Path("/Users/afschinmirhamed/Projects/aion-lumen/multi-agent/state/feedback.db")
FOLIO_DB = Path.home() / ".folio" / "folio.db"
OUT_DIR = Path.home() / ".folio" / "backups" / "pre-reset-2026-05-27"


def main() -> int:
    if not FEEDBACK_DB.exists() or not FOLIO_DB.exists():
        print(f"ERROR: missing DB(s)")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # --- Load yahoo feedback-Rows ---
    fb_conn = sqlite3.connect(FEEDBACK_DB)
    fb_conn.row_factory = sqlite3.Row
    fb_rows = fb_conn.execute(
        "SELECT id, task_id, imap_uid, sender, subject, "
        "plugin_value, plugin_confidence, "
        "heuristic_suggested_action, heuristic_reason, heuristic_markers, "
        "user_classification, user_final_action, suggested_action_confirmed, "
        "domain, actionability, effective_actionability, mail_date, created_at "
        "FROM feedback WHERE account_id='yahoo' ORDER BY id"
    ).fetchall()
    fb_conn.close()
    fb_by_id = {r["id"]: dict(r) for r in fb_rows}
    yahoo_ids = list(fb_by_id.keys())

    # --- Load corrections from folio.db scoped to yahoo feedback_ids ---
    folio_conn = sqlite3.connect(FOLIO_DB)
    folio_conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(yahoo_ids))
    corr_rows = folio_conn.execute(
        f"SELECT feedback_id, previous_action, corrected_action, "
        f"corrected_domain, corrected_actionability, note, source, corrected_at "
        f"FROM corrections WHERE feedback_id IN ({placeholders}) "
        f"ORDER BY feedback_id, corrected_at DESC",
        yahoo_ids,
    ).fetchall()
    # Latest correction per feedback_id
    latest_corr: dict[int, dict] = {}
    for c in corr_rows:
        fid = c["feedback_id"]
        if fid not in latest_corr:
            latest_corr[fid] = dict(c)
    folio_conn.close()

    # --- Build enriched rows ---
    enriched: list[dict] = []
    for r in fb_rows:
        rec = dict(r)
        rec["correction"] = latest_corr.get(r["id"])
        enriched.append(rec)

    # --- Write JSON ---
    json_path = OUT_DIR / "yahoo-reviewed.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(enriched, f, ensure_ascii=False, indent=2)
    print(f"  wrote {json_path}  ({len(enriched)} rows)")

    # --- Write CSV ---
    csv_path = OUT_DIR / "yahoo-reviewed.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "id", "task_id", "sender", "subject",
            "heuristic_action", "user_final_action", "confirmed",
            "domain", "actionability", "effective_actionability",
            "correction_action", "correction_domain", "correction_source",
            "mail_date",
        ])
        for r in enriched:
            corr = r.get("correction") or {}
            w.writerow([
                r["id"], r["task_id"], r["sender"], r["subject"],
                r["heuristic_suggested_action"], r["user_final_action"],
                r["suggested_action_confirmed"],
                r["domain"], r["actionability"], r["effective_actionability"],
                corr.get("corrected_action"),
                corr.get("corrected_domain"),
                corr.get("source"),
                r["mail_date"],
            ])
    print(f"  wrote {csv_path}")

    # --- Auswertung ---
    md_path = OUT_DIR / "auswertung.md"
    domain_counts = Counter(r["domain"] or "(null)" for r in fb_rows)
    action_counts = Counter(r["actionability"] or "(null)" for r in fb_rows)
    with_correction = sum(1 for r in enriched if r.get("correction"))
    heur_vs_user = sum(
        1 for r in fb_rows
        if r["heuristic_suggested_action"] and r["user_final_action"]
        and r["heuristic_suggested_action"] != r["user_final_action"]
    )
    correction_action_drift = Counter()
    correction_domain_drift = Counter()
    for r in enriched:
        corr = r.get("correction")
        if not corr:
            continue
        if corr.get("corrected_action") and corr["corrected_action"] != r["heuristic_suggested_action"]:
            correction_action_drift[
                f"{r['heuristic_suggested_action']}→{corr['corrected_action']}"
            ] += 1
        if corr.get("corrected_domain") and corr["corrected_domain"] != r["domain"]:
            correction_domain_drift[
                f"{r['domain']}→{corr['corrected_domain']}"
            ] += 1

    lines = []
    lines.append("# Pre-Reset Auswertung — yahoo (2026-05-27)\n")
    lines.append(f"**Pfad:** `~/.folio/backups/pre-reset-2026-05-27/`\n")
    lines.append("## Zählungen\n")
    lines.append(f"- Total yahoo-Rows: **{len(fb_rows)}**")
    lines.append(f"- Mit `user_final_action` gesetzt: **{sum(1 for r in fb_rows if r['user_final_action'])}**")
    lines.append(f"- Mit `suggested_action_confirmed=1`: **{sum(1 for r in fb_rows if r['suggested_action_confirmed'])}**")
    lines.append(f"- Mit echter User-Correction (folio-detail-panel): **{with_correction}**")
    lines.append(f"- Heuristik ≠ user_final_action: **{heur_vs_user}**\n")
    lines.append("## Domain-Verteilung\n")
    lines.append("| Domain | Count |")
    lines.append("|---|---|")
    for d, n in domain_counts.most_common():
        lines.append(f"| {d} | {n} |")
    lines.append("")
    lines.append("## Actionability-Verteilung\n")
    lines.append("| Actionability | Count |")
    lines.append("|---|---|")
    for a, n in action_counts.most_common():
        lines.append(f"| {a} | {n} |")
    lines.append("")
    if correction_action_drift:
        lines.append("## User-Correction: Action-Drift\n")
        lines.append("| Heuristik → User-Korrektur | Count |")
        lines.append("|---|---|")
        for k, n in correction_action_drift.most_common():
            lines.append(f"| {k} | {n} |")
        lines.append("")
    if correction_domain_drift:
        lines.append("## User-Correction: Domain-Drift\n")
        lines.append("| Heuristik-Domain → User-Korrektur | Count |")
        lines.append("|---|---|")
        for k, n in correction_domain_drift.most_common():
            lines.append(f"| {k} | {n} |")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  wrote {md_path}")
    print()
    print("Auswertung-Highlights:")
    print(f"  - {with_correction}/{len(fb_rows)} mit echter User-Correction")
    print(f"  - {heur_vs_user} mit Heuristik≠user_final_action")
    print(f"  - Domain-Top-3: {dict(domain_counts.most_common(3))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
