#!/usr/bin/env python3
"""backfill_mail_signals.py — P0.4c prep: populate the RFC auto-signal columns
(list_unsubscribe, auto_submitted, precedence, list_id) for the Yahoo feedback
backlog whose rows were classified before header capture existed (all 0/555).

Read-only on the mailbox: UID FETCH BODY.PEEK[HEADER] (never sets \\Seen — backlog
mails stay unread). NOTE: Yahoo IMAP returns an empty body for the field-selective
BODY.PEEK[HEADER.FIELDS (...)] form, so we fetch the full header block and parse the
four fields client-side. Writes only the four already-existing feedback columns,
matched by imap_uid against the current INBOX. UIDs no longer in INBOX (already
moved) are reported and skipped.

Default = DRY-RUN (fetch + preview counts, NO db write). Live needs --execute.

Usage:
    .venv/bin/python scripts/backfill_mail_signals.py            # preview
    .venv/bin/python scripts/backfill_mail_signals.py --execute  # write feedback.db
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import imap_cleanup as ic  # noqa: E402
from paths import FEEDBACK_DB  # noqa: E402

# Yahoo returns empty for HEADER.FIELDS (...) — fetch the full header, parse client-side.
FETCH_SPEC = "(UID BODY.PEEK[HEADER])"
_UID_RE = re.compile(rb"UID (\d+)")


def _parse_headers(raw: str) -> dict:
    """Unfold and extract the four headers from a header block."""
    # unfold continuation lines (leading whitespace)
    unfolded, out = [], {}
    for ln in raw.splitlines():
        if ln[:1] in (" ", "\t") and unfolded:
            unfolded[-1] += " " + ln.strip()
        else:
            unfolded.append(ln)
    for ln in unfolded:
        if ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        k = k.strip().lower(); v = v.strip()
        if k == "list-unsubscribe":
            out["list_unsubscribe"] = v
        elif k == "auto-submitted":
            out["auto_submitted"] = v
        elif k == "precedence":
            out["precedence"] = v
        elif k == "list-id":
            out["list_id"] = v
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="write feedback.db (default: preview)")
    args = ap.parse_args()

    fb_ro = sqlite3.connect(f"file:{FEEDBACK_DB}?mode=ro", uri=True)
    uids = [str(r[0]) for r in fb_ro.execute(
        "SELECT imap_uid FROM feedback WHERE imap_uid IS NOT NULL AND account_id='yahoo'"
    ).fetchall()]
    fb_ro.close()

    conn = ic._imap_connect(ic._load_yahoo_account())  # password_cmd → bw-free
    parsed_by_uid: dict[str, dict] = {}
    try:
        conn.select("INBOX", readonly=True)  # read-only → no state change
        typ, data = conn.uid("SEARCH", None, "ALL")
        inbox = {u.decode() for u in (data[0] or b"").split()} if typ == "OK" else set()
        present = [u for u in uids if u in inbox]
        missing = [u for u in uids if u not in inbox]
        # one UID FETCH for all present UIDs, BODY.PEEK (no \Seen)
        if present:
            typ, resp = conn.uid("FETCH", ",".join(present), FETCH_SPEC)
            for item in resp:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                m = _UID_RE.search(item[0] or b"")
                if not m:
                    continue
                uid = m.group(1).decode()
                parsed_by_uid[uid] = _parse_headers(
                    (item[1] or b"").decode("utf-8", "replace")
                )
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    # stats
    def has(k):
        return sum(1 for d in parsed_by_uid.values() if d.get(k))
    print(f"yahoo feedback UIDs: {len(uids)}  in INBOX: {len(present)}  "
          f"missing(already-moved): {len(missing)}")
    print(f"fetched header blocks: {len(parsed_by_uid)}")
    for k in ("list_unsubscribe", "auto_submitted", "precedence", "list_id"):
        print(f"  {k:16}: found in {has(k)} mails")

    if not args.execute:
        print("\n>>> DRY-RUN — feedback.db NICHT geschrieben. Für live: --execute")
        return 0

    fb = sqlite3.connect(str(FEEDBACK_DB))
    updated = 0
    for uid, d in parsed_by_uid.items():
        fb.execute(
            """UPDATE feedback SET list_unsubscribe=?, auto_submitted=?, precedence=?, list_id=?
               WHERE imap_uid=? AND account_id='yahoo'""",
            (d.get("list_unsubscribe", ""), d.get("auto_submitted", ""),
             d.get("precedence", ""), d.get("list_id", ""), int(uid)),
        )
        updated += 1
    fb.commit(); fb.close()
    print(f"\n>>> EXECUTE — feedback.db aktualisiert: {updated} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
