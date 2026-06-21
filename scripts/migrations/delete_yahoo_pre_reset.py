#!/usr/bin/env python3
"""delete_yahoo_pre_reset.py — Phase DB-Reset Step 4.

Löscht yahoo-Scope aus beiden DBs:
  - feedback.db:  feedback WHERE account_id='yahoo'
  - folio.db:     validator_opinions WHERE account_id='yahoo'
                  review_state WHERE account_id='yahoo'
                  corrections WHERE feedback_id IN (yahoo-IDs)

NICHT angetastet:
  - worker_runs (Audit/Telemetrie)
  - validator_opinions__pre_* (historische Snapshots)
  - gmail + mirhamed Accounts

Dry-run-default. `--apply` für Commit.

Run:
    .venv/bin/python3 scripts/delete_yahoo_pre_reset.py           # dry-run
    .venv/bin/python3 scripts/delete_yahoo_pre_reset.py --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

FEEDBACK_DB = Path("/Users/afschinmirhamed/Projects/aion-lumen/multi-agent/state/feedback.db")
FOLIO_DB = Path.home() / ".folio" / "folio.db"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not FEEDBACK_DB.exists() or not FOLIO_DB.exists():
        print(f"ERROR: missing DB(s)")
        return 1

    fb_conn = sqlite3.connect(FEEDBACK_DB)
    folio_conn = sqlite3.connect(FOLIO_DB)

    # --- Pre-count ---
    yahoo_fb = fb_conn.execute(
        "SELECT id FROM feedback WHERE account_id='yahoo'"
    ).fetchall()
    yahoo_ids = [r[0] for r in yahoo_fb]
    other_fb = fb_conn.execute(
        "SELECT account_id, COUNT(*) FROM feedback WHERE account_id != 'yahoo' GROUP BY account_id"
    ).fetchall()

    vo_yahoo = folio_conn.execute(
        "SELECT COUNT(*) FROM validator_opinions WHERE account_id='yahoo'"
    ).fetchone()[0]
    vo_other = folio_conn.execute(
        "SELECT account_id, COUNT(*) FROM validator_opinions WHERE account_id != 'yahoo' GROUP BY account_id"
    ).fetchall()
    rs_yahoo = folio_conn.execute(
        "SELECT COUNT(*) FROM review_state WHERE account_id='yahoo'"
    ).fetchone()[0]
    rs_other = folio_conn.execute(
        "SELECT account_id, COUNT(*) FROM review_state WHERE account_id != 'yahoo' GROUP BY account_id"
    ).fetchall()
    placeholders = ",".join("?" * len(yahoo_ids)) if yahoo_ids else "NULL"
    if yahoo_ids:
        corr_yahoo = folio_conn.execute(
            f"SELECT COUNT(*) FROM corrections WHERE feedback_id IN ({placeholders})",
            yahoo_ids,
        ).fetchone()[0]
    else:
        corr_yahoo = 0
    corr_total = folio_conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]
    corr_other = corr_total - corr_yahoo

    print("=" * 78)
    print(f"DB-RESET DELETE — yahoo-scope ({'APPLY' if args.apply else 'DRY-RUN'})")
    print("=" * 78)
    print()
    print("## Plan")
    print()
    print(f"  feedback.db   feedback (yahoo)            DELETE  {len(yahoo_ids):4d}  rows")
    print(f"  folio.db      validator_opinions (yahoo)  DELETE  {vo_yahoo:4d}  rows")
    print(f"  folio.db      review_state (yahoo)        DELETE  {rs_yahoo:4d}  rows")
    print(f"  folio.db      corrections (via fb-IDs)    DELETE  {corr_yahoo:4d}  rows")
    print()
    print("## Untouched")
    print()
    for acc, cnt in other_fb:
        print(f"  feedback.db   feedback ({acc})         KEEP    {cnt:4d}  rows")
    for acc, cnt in vo_other:
        print(f"  folio.db      validator_opinions ({acc})  KEEP    {cnt:4d}  rows")
    for acc, cnt in rs_other:
        print(f"  folio.db      review_state ({acc})         KEEP    {cnt:4d}  rows")
    print(f"  folio.db      corrections (non-yahoo)        KEEP    {corr_other:4d}  rows")
    print(f"  folio.db      worker_runs                    KEEP    (all)")
    print(f"  folio.db      validator_opinions__pre_*      KEEP    (snapshots)")
    print()

    if not args.apply:
        print("Dry-run only. Re-run with --apply to commit.")
        fb_conn.close()
        folio_conn.close()
        return 0

    # --- DELETE ---
    fb_conn.execute("DELETE FROM feedback WHERE account_id='yahoo'")
    folio_conn.execute("DELETE FROM validator_opinions WHERE account_id='yahoo'")
    folio_conn.execute("DELETE FROM review_state WHERE account_id='yahoo'")
    if yahoo_ids:
        folio_conn.execute(
            f"DELETE FROM corrections WHERE feedback_id IN ({placeholders})",
            yahoo_ids,
        )
    fb_conn.commit()
    folio_conn.commit()

    # --- Post-count ---
    fb_after = fb_conn.execute("SELECT account_id, COUNT(*) FROM feedback GROUP BY account_id").fetchall()
    vo_after = folio_conn.execute("SELECT account_id, COUNT(*) FROM validator_opinions GROUP BY account_id").fetchall()
    rs_after = folio_conn.execute("SELECT account_id, COUNT(*) FROM review_state GROUP BY account_id").fetchall()
    corr_after = folio_conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]
    print("## After DELETE")
    print()
    print(f"  feedback.db   feedback: {dict(fb_after)}")
    print(f"  folio.db      validator_opinions: {dict(vo_after)}")
    print(f"  folio.db      review_state: {dict(rs_after)}")
    print(f"  folio.db      corrections (total): {corr_after}")

    fb_conn.close()
    folio_conn.close()
    print()
    print("APPLIED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
