#!/usr/bin/env python3
"""yahoo_folder_list.py — READ-ONLY: list every Yahoo folder with its message count.

P0.4a first step: show the folder inventory so Afschin can confirm which folders to
dissolve (content → INBOX, delete folder) vs. keep. This script NEVER moves, deletes,
copies, or flags anything — it only LISTs folders and reads message counts (STATUS).

Disposition is attribute-driven (robust, not hardcoded names):
  - \\Noselect                          → container (parent, no messages, not dissolvable)
  - special-use (\\Sent \\Drafts \\Trash → KEEP (system folder — never dissolve)
    \\Junk \\Archive) or name == Inbox
  - explicit EXEMPT names               → KEEP (directive: BI Studiim, Parastoo + Avisa)
  - everything else                     → dissolve? (candidate — Afschin confirms)

Live IMAP via imap_cleanup._imap_connect (password_cmd → 0600 file, bw-free).
Usage:  .venv/bin/python scripts/yahoo_folder_list.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import imap_cleanup as ic  # noqa: E402

# Directive P0.4a: folders Afschin named to KEEP (never dissolve), if present.
EXEMPT_NAMES = {"BI Studiim", "Parastoo + Avisa"}
# IMAP special-use attributes that mark a system folder (keep, never dissolve).
SYSTEM_FLAGS = {"\\Sent", "\\Drafts", "\\Trash", "\\Junk", "\\Archive", "\\All", "\\Flagged"}

_LIST_RE = re.compile(r'^\((?P<attrs>[^)]*)\)\s+"(?P<delim>[^"]*)"\s+(?P<name>.+)$')


def _parse_list_line(line: bytes) -> tuple[list[str], str] | None:
    """Return (attributes, folder_name) from an IMAP LIST response line, or None."""
    s = (line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line)).strip()
    m = _LIST_RE.match(s)
    if not m:
        return None
    attrs = m.group("attrs").split()
    name = m.group("name").strip()
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1]
    return attrs, name


def _target_folders() -> set[str]:
    """New-scheme root domain folders (regelwerk domain_folder_map) — these are the
    MOVE targets, never dissolve candidates."""
    try:
        return set((ic._load_config().get("domain_folder_map") or {}).values())
    except Exception:  # noqa: BLE001
        return set()


def _disposition(attrs: list[str], name: str, targets: set[str]) -> str:
    if "\\Noselect" in attrs:
        return "container"
    if name in EXEMPT_NAMES:
        return "KEEP"
    if name.lower() == "inbox":
        return "KEEP"
    if any(f in attrs for f in SYSTEM_FLAGS):
        return "KEEP"
    if name in targets:
        return "target"      # new-scheme domain folder — keep, move destination
    return "dissolve?"


def main() -> int:
    yahoo = ic._load_yahoo_account()
    conn = ic._imap_connect(yahoo)  # password_cmd → bw-free
    try:
        typ, data = conn.list()
        if typ != "OK":
            print("ERROR: LIST failed", file=sys.stderr)
            return 1
        targets = _target_folders()
        rows = []
        for line in data:
            if not line:
                continue
            parsed = _parse_list_line(line)
            if not parsed:
                rows.append(("?", "?", (line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line))))
                continue
            attrs, name = parsed
            disp = _disposition(attrs, name, targets)
            if disp == "container":
                count = "—"  # \Noselect: cannot STATUS
            else:
                count = "?"
                try:
                    st_typ, st_data = conn.status(f'"{name}"', "(MESSAGES)")
                    if st_typ == "OK" and st_data and st_data[0]:
                        txt = st_data[0].decode("utf-8", "replace")
                        if "MESSAGES" in txt:
                            count = txt.split("MESSAGES")[1].strip(" ()").split()[0]
                except Exception as e:  # noqa: BLE001 — one bad folder must not kill the run
                    count = f"err:{type(e).__name__}"
            rows.append((count, disp, name))

        print("=== Yahoo folders (READ-ONLY inventory) ===")
        print(f"{'count':>8}  {'disposition':<11}  folder")
        for count, disp, name in rows:
            print(f"{str(count):>8}  {disp:<11}  {name}")
        by = lambda d: [r for r in rows if r[1] == d]  # noqa: E731
        print()
        print(f"KEEP={len(by('KEEP'))}  target={len(by('target'))}  "
              f"dissolve?={len(by('dissolve?'))}  container={len(by('container'))}  total={len(rows)}")
        print("KEEP = system special-use + Inbox + EXEMPT(BI Studiim, Parastoo + Avisa).")
        print("target = new-scheme root domain folder (move destination — never dissolve).")
        print("dissolve? = candidate — Afschin confirms the final list before ANY change.")
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
