#!/usr/bin/env python3
"""recover_immo_dups.py — P0.4c recovery: the full move (--max 360) was killed by a
2-min tool timeout mid-Immo, AFTER COPY / BEFORE delete → 170 immo mails ended up in
BOTH INBOX and Immo. This moves the 170 INBOX copies to Yahoo Trash (recoverable 30
days) — NOT permanent deletion. Originals stay safely in Immo.

Identification (robust, Message-ID based): a duplicate = an INBOX mail whose
Message-ID is in (current Immo) MINUS (original 26 Immo from the migration snapshot).
Safety guard: abort unless the identified count is in the expected window.

Read-only until the move; move_to_trash = COPY to Trash + remove-from-INBOX. No
Trash emptying (that stays a separate, human-authorized step).
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import imap_cleanup as ic  # noqa: E402
from imap_actions import move_to_trash  # noqa: E402

EXPECT_MIN, EXPECT_MAX = 150, 180  # guard window around the diagnosed 170


def _mid_map(conn) -> dict:
    """UID → Message-ID for the currently selected folder."""
    t, d = conn.uid("SEARCH", None, "ALL")
    uids = (d[0] or b"").split()
    if not uids:
        return {}
    t, resp = conn.uid("FETCH", b",".join(uids), "(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
    out = {}
    for item in resp:
        if isinstance(item, tuple):
            u = re.search(rb"UID (\d+)", item[0] or b"")
            h = (item[1] or b"").decode("utf-8", "replace")
            mid = ""
            for ln in h.splitlines():
                if ln.lower().startswith("message-id:"):
                    mid = ln.split(":", 1)[1].strip()
            if u and mid:
                out[u.group(1).decode()] = mid
    return out


def main() -> int:
    snap = json.load(open(sorted(glob.glob(
        str(Path(__file__).resolve().parent.parent / "data" / "yahoo-migration-snapshot-*.json")))[0]))
    orig = set()
    for f in snap["origin"]:
        if f["folder"].endswith("/Immo"):
            orig = {m["message_id"] for m in f["messages"] if m["message_id"]}
    print(f"original Immo Message-IDs (snapshot): {len(orig)}")

    conn = ic._imap_connect(ic._load_yahoo_account())  # password_cmd → bw-free
    try:
        conn.select("Immo", readonly=True)
        immo = _mid_map(conn)
        immo_new_mids = {m for m in immo.values() if m not in orig}
        conn.select("INBOX", readonly=True)
        inbox = _mid_map(conn)
        dup_uids = [int(u) for u, m in inbox.items() if m in immo_new_mids]
        print(f"Immo current={len(immo)} new(copies)={len(immo_new_mids)} | "
              f"INBOX duplicates to trash={len(dup_uids)}")

        if not (EXPECT_MIN <= len(dup_uids) <= EXPECT_MAX):
            print(f"!! ABORT: {len(dup_uids)} outside guard [{EXPECT_MIN},{EXPECT_MAX}] — nichts verschoben")
            return 1

        # move to Trash (COPY to Trash + remove from INBOX). Recoverable 30 days.
        conn.select("INBOX")  # read-write for the move
        move_to_trash(conn, dup_uids)
        print(f">>> {len(dup_uids)} INBOX-Duplikate → Yahoo Trash verschoben (recoverable).")
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
