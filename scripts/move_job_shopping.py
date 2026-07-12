#!/usr/bin/env python3
"""move_job_shopping.py — P0.4c: move the Job + Shopping buckets from INBOX to their
root domain folders. Targeted (Immo/Finance/System/Trash untouched — Immo already
done, its INBOX copies parked in Trash). Uses the SAME move_to_folder path as the
verified System/Finance tranche: COPY to target + remove-from-INBOX (stale-filtered).

Meant to run in the BACKGROUND — Yahoo rejects batch COPY (NO) and falls back to
per-UID, which for ~147 mails exceeds the 2-min foreground tool timeout (that was the
original Immo interruption cause).

Reuses imap_cleanup classification + correction snapshots (misclassification safety
net). Reports real per-folder move counts (folder-count deltas).
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import imap_cleanup as ic  # noqa: E402
from imap_actions import ensure_folder, move_to_folder  # noqa: E402
from paths import FEEDBACK_DB, FOLIO_DB  # noqa: E402

TARGETS = ("Job", "Shopping")


def _count(conn, folder: str) -> int:
    t, d = conn.status(f'"{folder}"', "(MESSAGES)")
    m = re.search(rb"MESSAGES (\d+)", d[0] or b"")
    return int(m.group(1)) if m else -1


def main() -> int:
    cfg = ic._load_config()
    dfm = cfg.get("domain_folder_map", {}) or {}
    fb = sqlite3.connect(f"file:{FEEDBACK_DB}?mode=ro", uri=True)
    folio = sqlite3.connect(str(FOLIO_DB))
    buckets = ic._classify_mails_generic(fb, folio, dfm)
    fb.close()

    conn = ic._imap_connect(ic._load_yahoo_account())  # password_cmd → bw-free
    try:
        before = {f: _count(conn, f) for f in TARGETS}
        conn.select("INBOX", readonly=True)
        t, d = conn.uid("SEARCH", None, "ALL")
        inbox_before = len((d[0] or b"").split())
        print(f"BEFORE: INBOX={inbox_before} " + " ".join(f"{f}={before[f]}" for f in TARGETS))

        for fname in TARGETS:
            entries = buckets.get(fname, [])
            uids = [e["imap_uid"] for e in entries]
            print(f"{fname}: {len(uids)} klassifiziert (pre-filter)")
            if not entries:
                continue
            ensure_folder(conn, fname)
            for e in entries:
                ic._write_correction_snapshot(
                    folio, feedback_id=e["feedback_id"], imap_uid=e["imap_uid"],
                    markers=e["markers"], correction_marker_csv=None,
                    source="imap_cleanup_job_shopping",
                )
            conn.select("INBOX")  # read-write for the move
            move_to_folder(conn, uids, fname)

        after = {f: _count(conn, f) for f in TARGETS}
        conn.select("INBOX", readonly=True)
        t, d = conn.uid("SEARCH", None, "ALL")
        inbox_after = len((d[0] or b"").split())
        print(f"AFTER:  INBOX={inbox_after} " + " ".join(f"{f}={after[f]}" for f in TARGETS))
        for f in TARGETS:
            print(f"  {f}: +{after[f]-before[f]} real (delta)")
        print(f"  INBOX: {inbox_before-inbox_after} entfernt")
    finally:
        folio.close()
        try:
            conn.logout()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
