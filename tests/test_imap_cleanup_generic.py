"""imap_cleanup generic routing (Council-aus, P0.3–P0.5).

auto-generated + domain-has-folder → root domain folder; personal / kontakt /
unsorted / no-folder → INBOX; werbung + user-dismissed → Trash.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import imap_cleanup as ic  # noqa: E402

DOMAIN_FOLDER_MAP = {
    "immo": "Immo", "job": "Job", "job-lead": "Job",
    "shopping": "Shopping", "finance": "Finance", "system": "System",
}

FB_COLS = (
    "id, imap_uid, account_id, sender, subject, body_excerpt, domain, actionability, "
    "heuristic_markers, to_addr, list_id, auto_submitted, precedence, list_unsubscribe"
)


@pytest.fixture()
def conns(tmp_path):
    fb = tmp_path / "feedback.db"
    folio = tmp_path / "folio.db"
    with sqlite3.connect(fb) as c:
        c.execute(
            "CREATE TABLE feedback (id INTEGER PRIMARY KEY, imap_uid INTEGER, "
            "account_id TEXT, sender TEXT, subject TEXT, body_excerpt TEXT, domain TEXT, "
            "actionability TEXT, heuristic_markers TEXT, to_addr TEXT, list_id TEXT, "
            "auto_submitted TEXT, precedence TEXT, list_unsubscribe TEXT)"
        )
        rows = [
            # id, uid, acct, sender, subject, body, domain, action, markers, to, list_id, auto_sub, prec, list_unsub
            (1, 101, "yahoo", "noreply@immobilienscout24.de", "1 Angebot", "", "immo", "actionable", "[]", "mirhamed@yahoo.de", "", "", "", ""),      # auto → Immo
            (2, 102, "yahoo", "makler@firma.de", "Ihr Objekt", "Hallo Afschin, anbei Details.", "immo", "actionable", "[]", "mirhamed@yahoo.de", "", "", "", ""),  # personal → inbox
            (3, 103, "yahoo", "deals@shop.de", "Sale", "", "werbung", "archive-silent", "[]", "mirhamed@yahoo.de", "", "", "", ""),                    # werbung → trash
            (4, 104, "yahoo", "freund@gmail.com", "Hi", "Lieber Afshin, wie gehts?", "kontakt", "actionable", "[]", "mirhamed@yahoo.de", "", "", "", ""),  # kontakt personal → inbox
            (5, 105, "yahoo", "list@news.de", "Update", "", "kontakt", "actionable", "[]", "mirhamed@yahoo.de", "", "", "", "<mailto:u@news.de>"),      # kontakt auto but NO folder → inbox
            (6, 106, "yahoo", "x@y.de", "?", "", "unsorted", "actionable", "[]", "mirhamed@yahoo.de", "", "", "", ""),                                 # unsorted → inbox
            (7, 107, "yahoo", "myscout@indeed.com", "Job", "", "immo", "actionable", "[]", "mirhamed@yahoo.de", "", "", "", ""),                       # dismissed (immo) → trash
            (8, 108, "yahoo", "noreply@bank.de", "Rechnung", "", "finance", "actionable", "[]", "mirhamed@yahoo.de", "", "", "", ""),                  # auto → Finance
        ]
        c.executemany(
            f"INSERT INTO feedback ({FB_COLS}) VALUES ({','.join('?' * 14)})", rows
        )
    with sqlite3.connect(folio) as c:
        c.execute("CREATE TABLE corrections (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                  "feedback_id INTEGER, corrected_actionability TEXT, correction_marker TEXT)")
        c.execute("CREATE TABLE mail_actionability_override (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                  "feedback_id INTEGER, overridden_actionability TEXT)")
        # id 7 user-dismissed via correction
        c.execute("INSERT INTO corrections (feedback_id, corrected_actionability, correction_marker) "
                  "VALUES (7, 'archive-silent', NULL)")
    fb_conn = sqlite3.connect(f"file:{fb}?mode=ro", uri=True)
    folio_conn = sqlite3.connect(f"file:{folio}?mode=ro", uri=True)
    yield fb_conn, folio_conn
    fb_conn.close(); folio_conn.close()


def _ids(bucket):
    return sorted(e["feedback_id"] for e in bucket)


def test_generic_routing(conns):
    fb_conn, folio_conn = conns
    b = ic._classify_mails_generic(fb_conn, folio_conn, DOMAIN_FOLDER_MAP)
    assert _ids(b["Immo"]) == [1]
    assert _ids(b["Finance"]) == [8]
    assert b.get("Job", []) == []
    assert _ids(b[ic._TRASH]) == [3, 7]          # werbung + user-dismissed
    assert _ids(b[ic._INBOX]) == [2, 4, 5, 6]    # personal, kontakt(personal), kontakt(auto but no folder), unsorted
    # nothing lost
    total = sum(len(v) for v in b.values())
    assert total == 8
