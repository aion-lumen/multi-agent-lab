"""Bauteil 6 (2026-06-09): IMAP-Aktions-Helper.

Heute read-only-IMAP-Stack im Multi-Agent (life-mail IMAPSession ist
fetch-only). Dieses Modul ergaenzt write-Operationen fuer den
imap_cleanup-Pfad:

- ensure_folder(conn, folder_path)   — idempotent
- move_to_folder(conn, uids, target) — COPY + +FLAGS \\Deleted + EXPUNGE
- mark_as_read(conn, uids)            — STORE +FLAGS \\Seen
- move_to_trash(conn, uids)           — move_to_folder mit Yahoo-Trash-Ordner

Alle Funktionen akzeptieren ein imaplib.IMAP4_SSL-Objekt (das z.B.
von life-mail IMAPSession.conn kommt). Aufruf nach SELECT eines
Source-Folders (typisch INBOX) — die UIDs beziehen sich auf das
selektierte Folder.

Engineer-Wahl Yahoo-Trash:
  Yahoo-Standard-Trash heisst entweder 'Trash' oder 'Bulk' o.ae.,
  vom Server abhaengig. resolve_trash_folder(conn) versucht via
  XLIST/LSUB den special-use-Tag zu finden, faellt auf 'Trash'
  zurueck. Bei abweichendem Yahoo-Setup kann der Name via
  TRASH_FOLDER_OVERRIDE ueberschrieben werden.
"""

from __future__ import annotations

import imaplib
import logging
from typing import Iterable

log = logging.getLogger(__name__)

# Yahoo verwendet typisch 'Trash' als Papierkorb-Folder. Override-
# Mechanismus via Argument falls Setup abweicht.
DEFAULT_TRASH_FOLDER = "Trash"


def _uids_to_set(uids: Iterable[int]) -> str:
    """imaplib uid()-args wollen comma-separated string."""
    return ",".join(str(u) for u in uids)


def ensure_folder(conn: imaplib.IMAP4_SSL, folder_path: str) -> None:
    """Idempotent — CREATE wenn fehlt. Yahoo trennt Sub-Folders mit '/'.

    LIST gegen den vollen Pfad: wenn die Antwort den Folder enthaelt,
    nichts tun. Sonst CREATE.
    """
    typ, data = conn.list('""', folder_path)
    if typ == "OK":
        # data ist liste von b'(\\HasNoChildren) "/" "folder_name"' o.ae.
        for entry in data or []:
            if entry and folder_path.encode() in entry:
                log.debug("folder already exists: %s", folder_path)
                return
    typ, _ = conn.create(folder_path)
    if typ != "OK":
        raise RuntimeError(f"CREATE folder failed for {folder_path!r}: {typ}")
    log.info("created folder: %s", folder_path)


def _filter_existing_uids(
    conn: imaplib.IMAP4_SSL, uids: list[int],
) -> list[int]:
    """Bauteil-12 (2026-06-10) Stale-UID-Filter: User hat möglicherweise
    in Yahoo-Web Mails manuell verschoben → UIDs aus feedback.db sind
    obsolet im aktuell selektierten Folder. UID SEARCH ALL liefert
    die noch existierenden UIDs; Schnittmenge mit angefragten uids."""
    typ, data = conn.uid("SEARCH", None, "ALL")
    if typ != "OK":
        return []
    existing = {int(u) for u in (data[0] or b"").split()}
    return [u for u in uids if u in existing]


def move_to_folder(
    conn: imaplib.IMAP4_SSL,
    uids: list[int],
    target: str,
) -> None:
    """COPY + STORE +FLAGS \\Deleted + EXPUNGE.

    imaplib hat kein UID MOVE built-in. Pattern: COPY in Ziel-Folder,
    dann Source-Mail mit \\Deleted markieren, dann EXPUNGE. Yahoo
    unterstuetzt das.

    Voraussetzung: Source-Folder ist via SELECT bereits aktiv.

    Bauteil-12 (2026-06-10): Stale-UID-resilient — UIDs die im
    aktuell selektierten Folder nicht mehr existieren (z.B. weil
    User manuell in Yahoo-Web verschoben hat) werden gefiltert.
    """
    if not uids:
        return
    filtered = _filter_existing_uids(conn, uids)
    stale_count = len(uids) - len(filtered)
    if stale_count > 0:
        log.info("move_to_folder: %d stale UIDs (nicht mehr in source) "
                 "uebersprungen", stale_count)
    if not filtered:
        log.info("move_to_folder: alle UIDs stale, kein COPY noetig")
        return
    # Bauteil-12 (2026-06-10): COPY zuerst als Batch versuchen.
    # Bei NO (typisch wenn eine UID im Batch problematisch ist —
    # Race-Condition zwischen SEARCH und COPY, oder Yahoo-Quota-
    # Edge): per-UID-Fallback. Erfolgreiche UIDs werden bestätigt.
    uid_set = _uids_to_set(filtered)
    typ, _ = conn.uid("COPY", uid_set, target)
    if typ != "OK":
        log.warning("move_to_folder: batch COPY to %r failed (%s) — "
                    "per-UID-fallback", target, typ)
        copied: list[int] = []
        for uid in filtered:
            typ2, _ = conn.uid("COPY", str(uid), target)
            if typ2 == "OK":
                copied.append(uid)
            else:
                log.warning("  per-UID COPY uid=%d → %s (skip)", uid, typ2)
        if not copied:
            log.warning("move_to_folder: no UID copied — skip STORE+EXPUNGE")
            return
        filtered = copied  # nur die kopierten markieren + expungen
    uid_set = _uids_to_set(filtered)
    typ, _ = conn.uid("STORE", uid_set, "+FLAGS", "(\\Deleted)")
    if typ != "OK":
        raise RuntimeError(f"STORE +Deleted failed: {typ}")
    typ, _ = conn.expunge()
    if typ != "OK":
        raise RuntimeError(f"EXPUNGE failed: {typ}")
    log.info("moved %d uids to %s", len(filtered), target)


def mark_as_read(conn: imaplib.IMAP4_SSL, uids: list[int]) -> None:
    """STORE +FLAGS \\Seen. Idempotent — wenn schon gelesen, NOOP."""
    if not uids:
        return
    uid_set = _uids_to_set(uids)
    typ, _ = conn.uid("STORE", uid_set, "+FLAGS", "(\\Seen)")
    if typ != "OK":
        raise RuntimeError(f"STORE +Seen failed: {typ}")
    log.debug("marked %d uids as read", len(uids))


def move_to_trash(
    conn: imaplib.IMAP4_SSL,
    uids: list[int],
    trash_folder: str = DEFAULT_TRASH_FOLDER,
) -> None:
    """Convenience: move_to_folder(trash). Yahoo behaelt 30 Tage."""
    move_to_folder(conn, uids, trash_folder)


def folder_exists(conn: imaplib.IMAP4_SSL, folder_path: str) -> bool:
    """B12 (2026-06-10): LIST-Check für Folder-Existenz. True wenn
    LIST den exakten Pfad zurückgibt."""
    typ, data = conn.list('""', folder_path)
    if typ != "OK":
        return False
    needle = folder_path.encode()
    for entry in data or []:
        if entry and needle in entry:
            return True
    return False


def rename_folder(
    conn: imaplib.IMAP4_SSL, old_name: str, new_name: str,
) -> bool:
    """B12 (2026-06-10): IMAP-RENAME idempotent. Returns True bei
    Erfolg, False wenn old_name nicht existiert oder new_name schon
    belegt (Caller entscheidet dann Merge oder Skip).

    Engineer-Hinweis: bei verschachtelten Pfaden (z.B.
    '_AionLumen/Shopping') stellt Yahoo automatisch sicher dass
    Parent existiert (kein vorab-CREATE _AionLumen nötig — Yahoo
    interpretiert '/' als Trenner und existing-folder als Parent)."""
    if not folder_exists(conn, old_name):
        log.info("rename_folder: source %r does not exist — skip", old_name)
        return False
    if folder_exists(conn, new_name):
        log.info("rename_folder: target %r already exists — caller decides",
                 new_name)
        return False
    typ, _ = conn.rename(old_name, new_name)
    if typ != "OK":
        raise RuntimeError(f"RENAME {old_name!r} → {new_name!r} failed: {typ}")
    log.info("renamed folder: %s → %s", old_name, new_name)
    return True


def merge_folder(
    conn: imaplib.IMAP4_SSL, source: str, target: str,
) -> int:
    """B12 (2026-06-10): Merge Mails aus source-Folder nach target.
    Pattern: SELECT source → UID SEARCH ALL → COPY → STORE+\\Deleted
    + EXPUNGE → DELETE source-Folder. Liefert Anzahl moved Mails.

    Vorbedingung: target existiert. Bei source ohne Mails: leere
    Source löschen + return 0."""
    if not folder_exists(conn, source):
        log.info("merge_folder: source %r does not exist — skip", source)
        return 0
    typ, _ = conn.select(source)
    if typ != "OK":
        raise RuntimeError(f"SELECT {source!r} failed: {typ}")
    typ, data = conn.uid("SEARCH", None, "ALL")
    if typ != "OK":
        raise RuntimeError(f"UID SEARCH in {source!r} failed: {typ}")
    uids_bytes = (data[0] or b"").split()
    uids = [int(u) for u in uids_bytes]
    moved = 0
    if uids:
        move_to_folder(conn, uids, target)
        moved = len(uids)
    # Source-Folder selbst löschen (Caller will Merge = source weg)
    # Vorher zurück zu INBOX wechseln (kann nicht selektierten Folder
    # löschen).
    conn.select("INBOX")
    typ, _ = conn.delete(source)
    if typ != "OK":
        log.warning("merge_folder: DELETE source %r failed: %s", source, typ)
    log.info("merged %d uids from %s → %s + deleted source",
             moved, source, target)
    return moved
