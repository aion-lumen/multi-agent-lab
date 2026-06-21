#!/usr/bin/env python3
"""diagnose_voices.py — CLI für validator_opinions per feedback_id.

Druckt pro Mail alle voice-rows (Heuristik + ggf. Qwen + Gemma) tabellarisch +
ein Konsens-Indikator. Non-UI-Verifikation für Direktive 2026-05-26 Part 1:
- Belegt dass zwei voices als zwei separate Rows persistieren
- Belegt dass UNIQUE(feedback_id, validator_model) bei Re-Run hält
- Belegt dass die Stimmen-Werte konsistent sind (oder eben nicht — Konsens-Spalte zeigt's)

Usage:
    diagnose_voices.py <feedback_id>          # eine Mail
    diagnose_voices.py --all                  # top-10 multi-voice feedback_ids
    diagnose_voices.py --uid <imap_uid> --account <yahoo|gmail|mirhamed>
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


FEEDBACK_DB = Path.home() / "Projects" / "aion-lumen" / "multi-agent" / "state" / "feedback.db"
FOLIO_DB = Path(os.environ.get("FOLIO_DB_PATH", str(Path.home() / ".folio" / "folio.db")))


def fetch_feedback(feedback_id: int) -> dict | None:
    if not FEEDBACK_DB.exists():
        print(f"✗ feedback.db not found: {FEEDBACK_DB}", file=sys.stderr)
        return None
    conn = sqlite3.connect(f"file:{FEEDBACK_DB}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT id, account_id, imap_uid, sender, subject, mail_date,
                      domain, actionability, effective_actionability,
                      heuristic_suggested_action, heuristic_reason, heuristic_confidence,
                      plugin_value, plugin_confidence
               FROM feedback WHERE id = ?""",
            (feedback_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def fetch_voices(feedback_id: int) -> list[dict]:
    if not FOLIO_DB.exists():
        print(f"✗ folio.db not found: {FOLIO_DB}", file=sys.stderr)
        return []
    conn = sqlite3.connect(f"file:{FOLIO_DB}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT validator_model, validator_domain, validator_actionability,
                      validator_confidence, validator_reasoning, evaluated_at
               FROM validator_opinions
               WHERE feedback_id = ?
               ORDER BY evaluated_at DESC""",
            (feedback_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def consensus_indicator(heur_dom: str | None, heur_act: str | None,
                        voices: list[dict]) -> str:
    """Compute consensus across (heuristic + all voice rows).
    All identical → ●●● einig. Else → ⚠ split (n distinct (domain,action) pairs).
    """
    pairs = []
    if heur_dom and heur_act:
        pairs.append((heur_dom, heur_act))
    for v in voices:
        d = v.get("validator_domain")
        a = v.get("validator_actionability")
        if d and a:
            pairs.append((d, a))
    if not pairs:
        return "— (keine Stimmen)"
    distinct = set(pairs)
    if len(distinct) == 1:
        return f"●●● einig ({pairs[0][0]}/{pairs[0][1]})"
    return f"⚠ split {len(distinct)}-pair: {sorted(distinct)}"


def print_one(feedback_id: int) -> int:
    fb = fetch_feedback(feedback_id)
    if not fb:
        print(f"feedback_id={feedback_id} not found in feedback.db")
        return 1
    voices = fetch_voices(feedback_id)

    print(f"\n=== feedback_id={feedback_id} ===")
    print(f"  account/uid:  {fb['account_id']}/{fb['imap_uid']}")
    print(f"  sender:       {fb['sender']}")
    print(f"  subject:      {fb['subject']}")
    print(f"  mail_date:    {fb.get('mail_date') or '(none)'}")
    print(f"  plugin:       {fb.get('plugin_value')!r:18s} conf={fb.get('plugin_confidence')}")
    print()
    print(f"  HEURISTIC (F.8):  domain={fb.get('domain')} action={fb.get('actionability')}"
          f"  effective={fb.get('effective_actionability') or '(NULL)'}")
    if fb.get('heuristic_suggested_action'):
        print(f"  HEURISTIC (legacy):  suggested={fb['heuristic_suggested_action']}"
              f" conf={fb.get('heuristic_confidence')}")
    if fb.get('heuristic_reason'):
        print(f"    reason: {fb['heuristic_reason']}")
    print()
    print(f"  VALIDATOR_OPINIONS ({len(voices)}):")
    if not voices:
        print("    (no rows)")
    for v in voices:
        model = (v.get("validator_model") or "?")[:30]
        dom = v.get("validator_domain") or "—"
        act = v.get("validator_actionability") or "—"
        conf = v.get("validator_confidence")
        conf_s = f"{conf:.2f}" if conf is not None else "—"
        reason = (v.get("validator_reasoning") or "")[:60]
        evaluated = v.get("evaluated_at") or "—"
        print(f"    [{model:30s}] {dom:10s} {act:16s} conf={conf_s}  {evaluated}")
        if reason:
            print(f"      reasoning: {reason}")
    print()
    print(f"  CONSENSUS: {consensus_indicator(fb.get('domain'), fb.get('actionability'), voices)}")
    return 0


def list_multi_voice(limit: int = 10) -> int:
    """Top-N feedback_ids that have ≥2 voice rows."""
    if not FOLIO_DB.exists():
        print(f"✗ folio.db not found: {FOLIO_DB}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(f"file:{FOLIO_DB}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            """SELECT feedback_id, COUNT(DISTINCT validator_model) AS n_models,
                      GROUP_CONCAT(validator_model, '|') AS models,
                      MAX(evaluated_at) AS last_eval
               FROM validator_opinions
               GROUP BY feedback_id
               HAVING n_models >= 2
               ORDER BY last_eval DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        print("(no feedback_ids with ≥2 voices yet — run validator_batch after this build)")
        return 0
    print(f"\nTop {len(rows)} multi-voice feedback_ids:\n")
    for r in rows:
        print(f"  fb={r[0]:6d}  models={r[1]}  last={r[3]}")
        print(f"    {r[2]}")
    print()
    print("Run `diagnose_voices.py <fb_id>` for full per-mail view.")
    return 0


def find_by_uid(account: str, uid: int) -> int:
    if not FEEDBACK_DB.exists():
        return 1
    conn = sqlite3.connect(f"file:{FEEDBACK_DB}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT id FROM feedback WHERE account_id = ? AND imap_uid = ?",
            (account, uid),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        print(f"no feedback row for account={account} uid={uid}", file=sys.stderr)
        return 1
    return print_one(row[0])


def main() -> int:
    ap = argparse.ArgumentParser(prog="diagnose_voices")
    ap.add_argument("feedback_id", type=int, nargs="?",
                    help="feedback.db row id; omit if --all or --uid")
    ap.add_argument("--all", action="store_true",
                    help="list top-10 feedback_ids with ≥2 voices")
    ap.add_argument("--uid", type=int, help="lookup by imap_uid (needs --account)")
    ap.add_argument("--account", choices=("yahoo", "gmail", "mirhamed"))
    args = ap.parse_args()

    if args.all:
        return list_multi_voice()
    if args.uid is not None:
        if not args.account:
            ap.error("--uid requires --account")
        return find_by_uid(args.account, args.uid)
    if args.feedback_id is None:
        ap.print_help()
        return 1
    return print_one(args.feedback_id)


if __name__ == "__main__":
    sys.exit(main())
