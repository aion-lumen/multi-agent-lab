#!/usr/bin/env python3
"""yahoo_migrate_folders.py — P0.4a: migrate the old _AionLumen/* scheme to the
flat root-domain scheme on Yahoo. Rename → Root (Afschin's decision 2026-07-12).

Actions:
  RENAME _AionLumen/Immo     → Immo
  RENAME _AionLumen/Job      → Job
  RENAME _AionLumen/Shopping → Shopping
  MERGE  _AionLumen/Korrespondenz → INBOX   (no Kontakt folder in the new scheme)
  DELETE _AionLumen                          (empty \\Noselect container afterwards)

Reversibility: BEFORE any mutation an origin snapshot JSON is written — per affected
folder its attributes, message count, and every message's (uid, message-id, subject)
via BODY.PEEK (no \\Seen change). Renames reverse by renaming back; the Korrespondenz
merge reverses by recreating the folder and moving its message-ids back from INBOX.

System folders (Inbox/Sent/Draft/Trash/Bulk/Archive) and Archive/2026 are NOT touched.

Default = DRY-RUN (snapshot + plan, no mutation). Live needs --execute (gated on Afschin's
final go). Live IMAP via imap_cleanup._imap_connect (password_cmd → bw-free).

Usage:
    .venv/bin/python scripts/yahoo_migrate_folders.py            # dry-run + snapshot
    .venv/bin/python scripts/yahoo_migrate_folders.py --execute  # perform (after go)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import imap_cleanup as ic  # noqa: E402
from imap_actions import folder_exists, merge_folder, rename_folder  # noqa: E402

CONTAINER = "_AionLumen"
RENAMES = [
    ("_AionLumen/Immo", "Immo"),
    ("_AionLumen/Job", "Job"),
    ("_AionLumen/Shopping", "Shopping"),
]
MERGES = [("_AionLumen/Korrespondenz", "INBOX")]

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "data"


def _folder_snapshot(conn, name: str) -> dict:
    """Read-only snapshot of one folder: count + per-message (uid, message-id, subject)."""
    info: dict = {"folder": name, "messages": []}
    if not folder_exists(conn, name):
        info["exists"] = False
        return info
    info["exists"] = True
    typ, _ = conn.select(f'"{name}"', readonly=True)
    if typ != "OK":
        info["error"] = f"SELECT {typ}"
        return info
    typ, data = conn.uid("SEARCH", None, "ALL")
    uids = [u.decode() for u in (data[0] or b"").split()] if typ == "OK" else []
    info["count"] = len(uids)
    for uid in uids:
        typ, fdata = conn.uid(
            "FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT)])"
        )
        hdr = ""
        if typ == "OK" and fdata and isinstance(fdata[0], tuple) and fdata[0][1]:
            hdr = fdata[0][1].decode("utf-8", "replace")
        mid, subj = "", ""
        for ln in hdr.splitlines():
            low = ln.lower()
            if low.startswith("message-id:"):
                mid = ln.split(":", 1)[1].strip()
            elif low.startswith("subject:"):
                subj = ln.split(":", 1)[1].strip()
        info["messages"].append({"uid": uid, "message_id": mid, "subject": subj[:120]})
    return info


def _write_snapshot(conn, stamp: str) -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    touched = [old for old, _ in RENAMES] + [src for src, _ in MERGES]
    snap = {
        "created_at": stamp,
        "scheme": "Rename→Root (P0.4a)",
        "actions": {
            "renames": [{"from": o, "to": n} for o, n in RENAMES],
            "merges": [{"from": s, "to": t} for s, t in MERGES],
            "delete_container": CONTAINER,
        },
        "origin": [_folder_snapshot(conn, f) for f in touched],
    }
    path = SNAPSHOT_DIR / f"yahoo-migration-snapshot-{stamp}.json"
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _plan_lines(conn) -> list[str]:
    lines = []
    for old, new in RENAMES:
        if not folder_exists(conn, old):
            lines.append(f"  RENAME {old} → {new}   [SKIP: source missing]")
        elif folder_exists(conn, new):
            lines.append(f"  RENAME {old} → {new}   [target exists → will MERGE instead]")
        else:
            lines.append(f"  RENAME {old} → {new}")
    for src, tgt in MERGES:
        n = "?" if not folder_exists(conn, src) else "ok"
        lines.append(f"  MERGE  {src} → {tgt}" + ("   [SKIP: source missing]" if n == "?" else ""))
    lines.append(f"  DELETE {CONTAINER}   [after children are gone]")
    return lines


def _do_execute(conn) -> dict:
    result = {"renamed": [], "merged": [], "deleted_container": False, "notes": []}
    for old, new in RENAMES:
        if rename_folder(conn, old, new):
            result["renamed"].append(f"{old}→{new}")
        elif folder_exists(conn, new):
            moved = merge_folder(conn, old, new)  # target existed → merge
            result["merged"].append(f"{old}→{new} ({moved})")
        else:
            result["notes"].append(f"rename {old}→{new} skipped (source missing)")
    for src, tgt in MERGES:
        moved = merge_folder(conn, src, tgt)
        result["merged"].append(f"{src}→{tgt} ({moved})")
    # delete the now-empty container (children renamed/merged away)
    conn.select("INBOX")
    if folder_exists(conn, CONTAINER):
        typ, _ = conn.delete(CONTAINER)
        result["deleted_container"] = (typ == "OK")
        if typ != "OK":
            result["notes"].append(f"DELETE {CONTAINER} failed: {typ}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="perform (default: dry-run)")
    args = ap.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    yahoo = ic._load_yahoo_account()
    conn = ic._imap_connect(yahoo)  # password_cmd → bw-free
    try:
        snap_path = _write_snapshot(conn, stamp)
        print(f"Origin-Snapshot geschrieben: {snap_path}")
        counts = {s["folder"]: s.get("count", "?") for s in
                  json.loads(snap_path.read_text())["origin"]}
        print(f"Snapshot-Counts: {counts}")
        print()
        print("=== Geplante Aktionen (Rename→Root, P0.4a) ===")
        for ln in _plan_lines(conn):
            print(ln)
        print()
        if not args.execute:
            print(">>> DRY-RUN — keine IMAP-Mutation. Für live: --execute (nach Afschins Go).")
            return 0
        print(">>> EXECUTE — führe Migration aus …")
        res = _do_execute(conn)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
